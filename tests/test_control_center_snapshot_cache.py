import json
import math
import re
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

from control_center import server
from control_center.snapshot_cache import MAX_COUNTER, SnapshotCache


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.compute = Mock(return_value={"durum": "ok", "prices": [123.45]})
        self.cache = SnapshotCache(self.compute, clock=self.clock)
        self.addCleanup(self.cache.close)

    def finish(self, cache=None):
        cache = cache or self.cache
        cache._thread.join(timeout=2)
        self.assertFalse(cache._thread.is_alive())
        self.assertFalse(cache.telemetry()["refresh_in_progress"])

    def warm(self):
        body, status, meta = self.cache.response()
        self.assertEqual(status, 503)
        self.assertEqual(meta["state"], "UNAVAILABLE")
        self.assertEqual(json.loads(body)["error"], "snapshot_unavailable")
        self.finish()

    def test_concurrent_cold_requests_start_exactly_one_snapshot_without_waiting(self):
        entered, release = threading.Event(), threading.Event()
        def compute():
            entered.set()
            if not release.wait(3):
                raise TimeoutError("test release missing")
            return {"value": 1}
        self.compute.side_effect = compute
        try:
            with ThreadPoolExecutor(max_workers=24) as pool:
                futures = [pool.submit(self.cache.response) for _ in range(24)]
                results = [f.result(timeout=1) for f in futures]
            self.assertTrue(entered.wait(1))
            self.assertEqual(self.compute.call_count, 1)
            self.assertTrue(all(r[1] == 503 for r in results))
            self.assertTrue(all(r[2]["refresh_in_progress"] for r in results))
        finally:
            release.set()
            self.finish()

    def test_cached_requests_are_fast_and_share_an_immutable_encoded_success(self):
        self.warm()
        self.compute.return_value["prices"].append(999)
        started = time.monotonic()
        for _ in range(100):
            body, status, meta = self.cache.response()
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["prices"], [123.45])
            self.assertEqual(meta["state"], "CURRENT")
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(self.compute.call_count, 1)
        self.assertEqual(meta["hits"], 100)
        self.assertEqual(meta["misses"], 1)

    def test_expired_requests_share_one_refresh_and_keep_old_generation_stale(self):
        self.warm()
        before = self.cache.telemetry()
        self.clock.now += 2
        release = threading.Event()
        self.compute.side_effect = lambda: (
            release.wait(3) and {"durum": "new", "prices": [456]})
        try:
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(lambda _: self.cache.response(), range(32)))
            self.assertEqual(self.compute.call_count, 2)
            for body, status, meta in results:
                self.assertEqual(status, 200)
                self.assertEqual(meta["state"], "STALE")
                self.assertEqual(meta["last_success_at"], before["last_success_at"])
                self.assertEqual(meta["generation"], 1)
                self.assertEqual(json.loads(body)["prices"], [123.45])
                self.assertTrue(meta["refresh_in_progress"])
        finally:
            release.set()
            self.finish()
        body, _, meta = self.cache.response()
        self.assertEqual(json.loads(body)["prices"], [456])
        self.assertEqual(meta["state"], "CURRENT")
        self.assertEqual(meta["generation"], 2)

    def test_failed_refresh_is_stale_backs_off_and_recovers_without_relabeling_history(self):
        self.warm()
        before = self.cache.telemetry()
        self.compute.side_effect = sqlite3.OperationalError("private DB path/SQL")
        self.clock.now += 3
        self.cache.response()
        self.finish()
        for _ in range(50):
            body, status, meta = self.cache.response()
            self.assertEqual(status, 200)
            self.assertEqual(meta["state"], "STALE")
            self.assertFalse(meta["refresh_in_progress"])
            self.assertEqual(meta["generation"], 1)
            self.assertEqual(meta["age_seconds"], 3)
            self.assertEqual(meta["last_success_at"], before["last_success_at"])
            self.assertEqual(meta["captured_at"], before["captured_at"])
            self.assertEqual(meta["last_error"], "snapshot_refresh_failed")
            self.assertNotIn(b"private", body)
        self.assertEqual(self.compute.call_count, 2)
        self.assertEqual(meta["refresh_failures"], 1)
        self.clock.now += 2
        self.compute.side_effect = None
        self.cache.response()
        self.finish()
        self.assertEqual(self.cache.telemetry()["state"], "CURRENT")
        self.assertIsNone(self.cache.telemetry()["last_error"])

    def test_first_failed_refresh_remains_503_and_cannot_retry_at_request_rate(self):
        self.compute.side_effect = RuntimeError("private")
        self.warm()
        for _ in range(50):
            body, status, meta = self.cache.response()
            self.assertEqual(status, 503)
            self.assertEqual(meta["state"], "UNAVAILABLE")
            self.assertIsNone(meta["last_success_at"])
        self.assertEqual(self.compute.call_count, 1)

    def test_serialization_failure_does_not_replace_last_success(self):
        self.warm()
        self.compute.return_value = {"price": math.nan}
        self.clock.now += 2
        self.cache.response()
        self.finish()
        body, _, meta = self.cache.response()
        self.assertEqual(json.loads(body)["prices"], [123.45])
        self.assertEqual(meta["state"], "STALE")
        self.assertEqual(meta["refresh_failures"], 1)

    def test_slow_snapshot_age_includes_compute_time_and_wall_clock_cannot_refresh_ttl(self):
        def compute():
            self.clock.now += 4
            return {}
        self.compute.side_effect = compute
        self.warm()
        with patch("control_center.snapshot_cache.datetime") as wall_clock:
            wall_clock.now.side_effect = AssertionError("TTL must use monotonic time")
            body, status, meta = self.cache.response()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["snapshot_cache"]["compute_duration_seconds"], 4)
        self.assertEqual(meta["age_seconds"], 0)
        self.assertEqual(meta["snapshot_age_seconds"], 4)
        self.assertGreaterEqual(meta["compute_cpu_seconds"], 0)

    def test_telemetry_is_constant_size_and_counters_saturate(self):
        self.warm()
        fields = set(self.cache.telemetry())
        self.cache._counters["hits"] = MAX_COUNTER
        for _ in range(10):
            self.assertEqual(set(self.cache.response()[2]), fields)
        self.assertEqual(self.cache.telemetry()["hits"], MAX_COUNTER)
        self.assertLess(len(json.dumps(self.cache.telemetry())), 1500)

    def test_inspection_and_close_do_not_start_refreshes(self):
        self.assertEqual(self.cache.telemetry()["state"], "UNAVAILABLE")
        self.cache.close()
        self.assertEqual(self.cache.response()[1], 503)
        self.compute.assert_not_called()

    def test_thread_start_failure_is_retryable_without_false_success(self):
        with patch("control_center.snapshot_cache.threading.Thread.start",
                   side_effect=RuntimeError("no thread available")):
            _, status, meta = self.cache.response()
        self.assertEqual(status, 503)
        self.assertFalse(meta["refresh_in_progress"])
        self.assertEqual(meta["last_error"], "refresh_start_failed")
        self.cache.response()
        self.compute.assert_not_called()
        self.clock.now += 2
        self.cache.response()
        self.finish()
        self.assertEqual(self.cache.telemetry()["state"], "CURRENT")

    def test_real_cached_projection_leaves_database_and_safety_contract_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime.db"
            with sqlite3.connect(db) as con:
                con.executescript("""
                    CREATE TABLE paper_agent_portfolio(portfolio_id,initial_capital,cash,realized_pnl,total_fees);
                    INSERT INTO paper_agent_portfolio VALUES(1,10000,10000,0,0);
                    CREATE TABLE paper_agent_positions(position_id,symbol,market_type,direction,entry_time,
                        entry_price,quantity,notional,leverage,selection_reason,status,decision_source,
                        decision_reference_id,exit_time,exit_price,net_pnl,close_reason,entry_fee,exit_fee);
                    CREATE TABLE paper_position_management(position_id,stop_distance_pct,current_stop_price,
                        tp1_done,tp2_done,remaining_quantity);
                """)
            before = db.read_bytes()
            compute = lambda: server.operator_view(server.overview(
                db_path=db, market_state_path=Path(tmp)/"missing-market",
                health_path=Path(tmp)/"missing-health"))
            cache = SnapshotCache(compute)
            self.addCleanup(cache.close)
            cache.response()
            self.finish(cache)
            body, status, _ = cache.response()
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["snapshot_cache"]["state"], "CURRENT")
            self.assertEqual(db.read_bytes(), before)
            self.assertIn("PAPER", json.loads(body)["işlem_modu"])
            missing = Path(tmp)/"never-created.db"
            failed = SnapshotCache(lambda: server.overview(db_path=missing))
            self.addCleanup(failed.close)
            failed.response()
            self.finish(failed)
            self.assertEqual(failed.response()[1], 503)
            self.assertFalse(missing.exists())


class CachedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()
        self.compute = Mock(side_effect=lambda: self.release.wait(3) and {"durum": "ok"})
        self.cache = SnapshotCache(self.compute)
        self.httpd = server.PanelHTTPServer(("127.0.0.1", 0), cache=self.cache)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.release.set()
        if self.cache._thread:
            self.cache._thread.join(timeout=2)
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)

    def request(self, path="/api/v1/overview", method="GET", body=None, headers=None):
        conn = HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=2)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            return r.status, json.loads(r.read()), dict(r.getheaders())
        finally:
            conn.close()

    def test_http_concurrency_liveness_headers_and_fast_cache(self):
        status, body, _ = self.request("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body["snapshot_cache"]["state"], "UNAVAILABLE")
        self.compute.assert_not_called()
        with ThreadPoolExecutor(max_workers=16) as pool:
            responses = list(pool.map(lambda _: self.request(), range(16)))
        for status, body, headers in responses:
            self.assertEqual(status, 503)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertEqual(headers["X-AGCA-Snapshot-Status"], "UNAVAILABLE")
            self.assertEqual(headers["Retry-After"], "2")
            self.assertTrue(body["snapshot_cache"]["refresh_in_progress"])
        self.assertEqual(self.compute.call_count, 1)
        self.assertEqual(self.request("/healthz")[0], 200)
        self.release.set()
        self.cache._thread.join(timeout=2)
        start = time.monotonic()
        for _ in range(20):
            status, body, headers = self.request()
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-AGCA-Snapshot-Status"], "CURRENT")
        self.assertLess(time.monotonic() - start, 1)
        self.assertEqual(self.compute.call_count, 1)

    def test_diagnostic_feedback_during_refresh_and_forbidden_mutations(self):
        self.request()
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "record_data_mismatch", wraps=partial(
                server.record_data_mismatch, path=Path(tmp)/"feedback.jsonl")
        ):
            payload = {"symbol": "ETHUSDT", "market_type": "USD_M_PERPETUAL",
                       "operator_price": 2301.5, "agent_price": 2300,
                       "agent_bid": 2299.5, "agent_ask": 2300.5,
                       "agent_quote_source": "binance", "agent_freshness": "1 s"}
            status, body, _ = self.request("/api/v1/feedback/market-data-mismatch", "POST",
                                          json.dumps(payload), {"Content-Type": "application/json"})
            self.assertEqual(status, 201)
            saved = json.loads((Path(tmp)/"feedback.jsonl").read_text())
            self.assertEqual(saved["agent_bid"], 2299.5)
            self.assertEqual(body["authority"], "DIAGNOSTIC_ONLY")
            self.assertFalse(saved["trading_mutation_allowed"])
            self.assertFalse(saved["learning_mutation_allowed"])
            for path in ("/api/v1/overview", "/api/v1/trade", "/api/v1/learning"):
                self.assertEqual(self.request(path, "POST", "{}")[0], 405)
            self.assertEqual(self.request("/api/v1/feedback/market-data-mismatch", "POST", "{}",
                                         {"Content-Type": "application/json", "Origin": "https://foreign.invalid"})[0], 403)
            self.assertEqual(self.compute.call_count, 1)


class CacheUITests(unittest.TestCase):
    def test_real_javascript_marks_stale_expires_current_and_recovers(self):
        script = re.search(r"<script>(.*?)</script>", server.INDEX_PATH.read_text(), re.S).group(1)
        harness = """
        const nodes=new Map();let expiry;
        global.document={getElementById(id){
          if(!nodes.has(id))nodes.set(id,{classList:{toggle(){}}});return nodes.get(id);
        },querySelectorAll(){return []},addEventListener(){}};
        global.setTimeout=(fn,ms)=>{expiry=fn;return 1};global.clearTimeout=()=>{};
        global.fetch=()=>new Promise(()=>{});
        """
        view = server.operator_view({
            "system": {"open_positions": 0, "market_data_status": "CANLI"},
            "portfolio": {"cash": 10000, "realized_pnl": 0, "unrealized_pnl": 0},
            "positions": [], "closed_positions": [],
        })
        view["snapshot_cache"] = {"state": "CURRENT", "age_seconds": 0.5,
                                   "snapshot_age_seconds": 2.5, "ttl_seconds": 2,
                                   "hits": 12, "misses": 3, "refresh_in_progress": False}
        tail = """
        render(VIEW);const current=nodes.get("snapshotCache").textContent;
        expiry();const expired=nodes.get("status").textContent;
        render({...VIEW,snapshot_cache:{...VIEW.snapshot_cache,state:"STALE",last_error:"snapshot_refresh_failed"}});
        const failed=nodes.get("snapshotCache").textContent;
        render(VIEW);const recovered=nodes.get("status").className;
        console.log(JSON.stringify({current,expired,failed,recovered}));
        """.replace("VIEW", json.dumps(view))
        result = subprocess.run(["node", "-e", harness + script + tail],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertIn("CURRENT", out["current"])
        self.assertIn("12/3", out["current"])
        self.assertIn("STALE", out["expired"])
        self.assertIn("Yenileme başarısız", out["failed"])
        self.assertNotIn("stale", out["recovered"])


if __name__ == "__main__":
    unittest.main()
