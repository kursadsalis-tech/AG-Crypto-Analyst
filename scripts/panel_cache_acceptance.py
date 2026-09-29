"""Bounded GET-only panel canary; the documented shell wrapper owns rollback."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen


def get(path):
    started = time.monotonic()
    try:
        response = urlopen("http://127.0.0.1:8787" + path, timeout=1)
    except HTTPError as exc:
        response = exc
    with response:
        body = json.load(response)
        return {"status": response.status, "latency_seconds": time.monotonic() - started,
                "body": body}


def scheduler_sample(pid, group, cache):
    result = {"thread": None, "cgroups": {}}
    tid = cache.get("refresh_thread_id")
    if tid is not None:
        task = Path(f"/proc/{pid}/task/{tid}")
        try:
            cpu, wait, slices = map(int, (task / "schedstat").read_text().split())
            result["thread"] = {
                "tid": tid, "cpu_ns": cpu, "runqueue_wait_ns": wait, "timeslices": slices,
                "state": (task / "stat").read_text().rsplit(")", 1)[1].split()[0],
                "wchan": (task / "wchan").read_text().strip(),
            }
        except (OSError, ValueError):
            pass  # Finished/disappearing TID is unknown, never a zero sample.
    root = Path("/sys/fs/cgroup")
    path = root / group.lstrip("/")
    for _ in range(8):
        try:
            result["cgroups"][str(path.relative_to(root))] = {
                name: (path / name).read_text().strip()
                for name in ("cpu.stat", "cpu.max", "cpu.weight") if (path / name).exists()
            }
        except OSError:
            pass
        if path == root:
            break
        path = path.parent
    return result


def worker_sample():
    root = Path("/var/lib/agca")
    health = json.loads((root / "paper-realtime-health.json").read_text())
    market = json.loads((root / "paper-realtime-market-state.json").read_text())
    stamp = time.time() * 1000
    quotes = {r["symbol"]: stamp - r["quote_timestamp_ms"]
              for r in market.get("live_market", [])
              if r.get("symbol") in ("BTCUSDT", "ETHUSDT")
              and r.get("market_type") == "USD_M_PERPETUAL" and r.get("quote_timestamp_ms")}
    lag = health.get("quote_ingest", {}).get("USD_M_PERPETUAL", {}).get("last_receive_lag_ms")
    return {
        "quotes_ms": quotes,
        "receive_lag_ms": lag,
        "source": health.get("process_runtime_source"),
        "runtime_safety": {
            "health_execution_mode": health.get("execution_mode"),
            "health_real_order_allowed": health.get("real_order_allowed"),
            "market_execution_mode": market.get("execution_mode"),
            "market_real_order_allowed": market.get("real_order_allowed"),
        },
    }


def assert_runtime_safety(worker):
    safety = worker["runtime_safety"]
    expected = {
        "health_execution_mode": "PAPER_ONLY",
        "health_real_order_allowed": False,
        "market_execution_mode": "PAPER_ONLY",
        "market_real_order_allowed": False,
    }
    assert safety == expected, f"runtime safety mismatch: {safety!r}"


def main(output):
    report = {"passed": False, "samples": [], "requests": []}
    started = time.monotonic()
    try:
        props = subprocess.check_output([
            "systemctl", "show", "agca-live-panel.service", "-p", "MainPID", "-p", "ControlGroup"
        ], text=True, timeout=2)
        props = dict(line.split("=", 1) for line in props.splitlines())
        pid, group = int(props["MainPID"]), props["ControlGroup"]
        assert pid > 0 and group.startswith("/")
        baseline = worker_sample()
        report["worker_baseline"] = baseline
        assert_runtime_safety(baseline)

        def sample():
            response = get("/healthz")
            cache = response["body"]["snapshot_cache"]
            worker = worker_sample()
            report["samples"].append({"elapsed_seconds": time.monotonic() - started,
                "cache": cache, "scheduler": scheduler_sample(pid, group, cache),
                "worker": worker, "health_latency_seconds": response["latency_seconds"]})
            assert_runtime_safety(worker)
            assert response["status"] == 200 and response["body"]["authority"] == "READ_ONLY"
            assert response["latency_seconds"] < .5, "health latency"
            assert cache["refresh_failures"] == 0, "refresh failed"
            assert worker["source"] is not None and worker["source"] == baseline["source"]
            assert set(worker["quotes_ms"]) == {"BTCUSDT", "ETHUSDT"}, "missing quote evidence"
            assert all(0 <= age <= 1500 for age in worker["quotes_ms"].values()), "quote age"
            assert worker["receive_lag_ms"] is not None and 0 <= worker["receive_lag_ms"] <= 1000
            return cache

        def wait_generation(generation):
            deadline = time.monotonic() + 8
            while True:
                cache = sample()
                assert cache["refresh_attempts"] == generation, "unexpected extra compute"
                if cache["generation"] == generation:
                    assert cache["state"] == "CURRENT"
                    assert cache["compute_duration_seconds"] <= 5, "slow snapshot"
                    return
                assert time.monotonic() < deadline, "refresh not ready within 8 seconds"
                time.sleep(.5)

        def pair(generation=None):
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(get, ["/api/v1/overview"] * 2))
            for response in responses:
                cache = response["body"]["snapshot_cache"]
                report["requests"].append({"status": response["status"],
                    "latency_seconds": response["latency_seconds"], "cache": cache})
                assert response["status"] == 200 and response["latency_seconds"] < .5
                if generation is not None:
                    assert cache["state"] == "CURRENT" and cache["generation"] == generation
                    assert cache["refresh_attempts"] == generation
                else:
                    assert cache["state"] in ("STALE", "CURRENT")

        cold = get("/api/v1/overview")
        report["cold"] = cold
        assert cold["status"] == 503 and cold["latency_seconds"] < .5
        assert cold["body"]["snapshot_cache"]["state"] == "UNAVAILABLE"
        wait_generation(1)
        pair(1)
        time.sleep(2.05)
        pair()
        wait_generation(2)
        pair(2)
        report["passed"] = True
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        report["duration_seconds"] = time.monotonic() - started
        output.write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k not in ("samples", "requests")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(main(parser.parse_args().output))
