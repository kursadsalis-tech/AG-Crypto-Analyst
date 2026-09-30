import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paper_trading import behavior_decision_core as core
from paper_trading import exchange_metadata
from scripts import runtime_acceptance as acceptance


SHA = "a" * 40
NOW = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)


class FakeClock:
    elapsed = 0

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds

    def now(self):
        return NOW + timedelta(seconds=self.elapsed)


class RuntimeAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.spec_by_file = {spec.health_file: (unit, spec) for unit, spec in acceptance.SERVICES.items()}

    def state(self, unit):
        return {"pid": list(acceptance.SERVICES).index(unit) + 101,
                "restarts": 3, "active": "active", "sub": "running"}

    def health(self, path):
        unit, spec = self.spec_by_file[Path(path).name]
        stamp = self.clock.now().isoformat()
        return {**spec.versions, "status": spec.statuses[0], "at": stamp,
                "last_success_at": stamp, "execution_mode": "PAPER_ONLY",
                "may_send_real_orders": False, "real_order_allowed": False,
                "process_source": {"git_sha": SHA, "git_dirty": False,
                                   "pid": self.state(unit)["pid"],
                                   "process_started_at": (NOW - timedelta(minutes=5)).isoformat()},
                "ENTRY_PATH": {"status": "READY"}, "OPEN_POSITION_MANAGEMENT": {"status": "IDLE"}}

    def run_acceptance(self, **kwargs):
        defaults = dict(window_seconds=10, ready_timeout=20, poll_seconds=5,
                        state_reader=self.state, health_reader=self.health,
                        clock=self.clock.monotonic, sleep=self.clock.sleep, now_fn=self.clock.now)
        defaults.update(kwargs)
        return acceptance.accept_runtime(SHA, "/test", **defaults)

    def test_success_requires_all_five_services_stable_for_entire_window(self):
        result = self.run_acceptance()
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["stable_window_seconds"], 10)
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(len(result["services"]), 5)
        self.assertTrue(all(row["nrestarts_delta"] == 0 for row in result["services"].values()))

    def test_pid_change_fails_immediately(self):
        def changed(unit):
            state = self.state(unit)
            if self.clock.elapsed >= 5:
                state["pid"] += 1000
            return state
        with self.assertRaisesRegex(acceptance.AcceptanceError, "PID_CHANGED"):
            self.run_acceptance(state_reader=changed)
        self.assertEqual(self.clock.elapsed, 5)

    def test_restart_increase_fails_even_if_final_status_is_healthy(self):
        def restarted(unit):
            state = self.state(unit)
            if self.clock.elapsed >= 5:
                state["restarts"] += 1
            return state
        with self.assertRaisesRegex(acceptance.AcceptanceError, "NRESTARTS_CHANGED"):
            self.run_acceptance(state_reader=restarted)

    def test_wrong_sha_dirty_and_version_from_current_pid_fail_fast(self):
        for mutation, expected in (("sha", "PROCESS_SHA_MISMATCH"), ("dirty", "PROCESS_SOURCE_NOT_CLEAN"),
                                   ("contract", "VERSION_MISMATCH")):
            with self.subTest(mutation=mutation):
                self.clock = FakeClock()
                def invalid(path):
                    health = self.health(path)
                    if mutation == "sha":
                        health["process_source"]["git_sha"] = "b" * 40
                    elif mutation == "dirty":
                        health["process_source"]["git_dirty"] = True
                    else:
                        health["health_contract_version"] = "OLD"
                    return health
                with self.assertRaisesRegex(acceptance.AcceptanceError, expected):
                    self.run_acceptance(health_reader=invalid)
                self.assertEqual(self.clock.elapsed, 0)

    def test_stale_or_missing_health_is_bounded_by_readiness_deadline(self):
        for mutation in ("stale", "missing"):
            with self.subTest(mutation=mutation):
                self.clock = FakeClock()
                def invalid(path):
                    health = self.health(path)
                    if mutation == "missing":
                        return {}
                    health["at"] = (NOW - timedelta(hours=1)).isoformat()
                    return health
                with self.assertRaisesRegex(acceptance.AcceptanceError, "RUNTIME_READINESS_TIMEOUT"):
                    self.run_acceptance(health_reader=invalid)
                self.assertEqual(self.clock.elapsed, 20)

    def test_previous_pid_health_can_be_replaced_during_readiness(self):
        def old_then_current(path):
            health = self.health(path)
            if self.clock.elapsed < 5:
                health["process_source"]["pid"] += 1000
            return health
        self.assertEqual(self.run_acceptance(health_reader=old_then_current)["status"], "ACCEPTED")
        self.assertEqual(self.clock.elapsed, 15)

    def starting_health(self, path):
        health = self.health(path)
        return {field: ("STARTING" if field == "status" else health[field])
                for field in ("status", "at", "process_source", "health_contract_version")}

    def test_current_pid_starting_waits_for_versions_and_full_readiness(self):
        for pending in ("absent", "null"):
            with self.subTest(pending=pending):
                self.clock = FakeClock()
                def starting_then_ready(path):
                    if self.clock.elapsed >= 5:
                        return self.health(path)
                    health = self.starting_health(path)
                    if pending == "null":
                        _, spec = self.spec_by_file[Path(path).name]
                        health.update({field: None for field in spec.versions if field not in health})
                    return health
                result = self.run_acceptance(health_reader=starting_then_ready)
                self.assertEqual(result["status"], "ACCEPTED")
                self.assertEqual(result["sample_count"], 4)
                self.assertEqual(result["stable_window_seconds"], 10)
                self.assertEqual(self.clock.elapsed, 15)

    def test_starting_with_explicit_wrong_version_fails_fast(self):
        for field in acceptance.SERVICES["agca-paper-realtime-worker.service"].versions:
            for value in ("OLD", ""):
                with self.subTest(field=field, value=value):
                    self.clock = FakeClock()
                    def invalid(path):
                        health = self.starting_health(path)
                        health[field] = value
                        return health
                    with self.assertRaisesRegex(acceptance.AcceptanceError, "VERSION_MISMATCH:" + field):
                        self.run_acceptance(health_reader=invalid)
                    self.assertEqual(self.clock.elapsed, 0)

    def test_current_pid_starting_times_out_without_full_health(self):
        with self.assertRaisesRegex(acceptance.AcceptanceError, "RUNTIME_READINESS_TIMEOUT:.*HEALTH_NOT_READY:STARTING"):
            self.run_acceptance(health_reader=self.starting_health)
        self.assertEqual(self.clock.elapsed, 20)

    def test_starting_never_defers_wrong_sha_or_dirty_source(self):
        for field, value, failure in (("git_sha", "b" * 40, "PROCESS_SHA_MISMATCH"),
                                      ("git_dirty", True, "PROCESS_SOURCE_NOT_CLEAN")):
            with self.subTest(field=field):
                self.clock = FakeClock()
                def invalid(path):
                    health = self.starting_health(path)
                    health["process_source"][field] = value
                    return health
                with self.assertRaisesRegex(acceptance.AcceptanceError, failure):
                    self.run_acceptance(health_reader=invalid)
                self.assertEqual(self.clock.elapsed, 0)

    def test_failure_status_with_missing_versions_still_fails_fast(self):
        for status in ("FAILED", "ERROR", "STARTUP_FAILED"):
            with self.subTest(status=status):
                self.clock = FakeClock()
                def failed(path):
                    health = self.starting_health(path)
                    health.update(status=status, error_detail="startup failed")
                    return health
                with self.assertRaisesRegex(acceptance.AcceptanceError, "startup failed"):
                    self.run_acceptance(health_reader=failed)
                self.assertEqual(self.clock.elapsed, 0)

    def test_ready_health_with_missing_version_still_fails_fast(self):
        def incomplete(path):
            health = self.health(path)
            del health["core_version"]
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "VERSION_MISMATCH:core_version"):
            self.run_acceptance(health_reader=incomplete)
        self.assertEqual(self.clock.elapsed, 0)

    def test_startup_failure_reports_actual_error_without_waiting(self):
        def failed(path):
            health = self.health(path)
            health.update(status="FAILED", error="RuntimeError", error_detail="metadata rules corrupt")
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "metadata rules corrupt"):
            self.run_acceptance(health_reader=failed)
        self.assertEqual(self.clock.elapsed, 0)

    def test_health_degradation_during_stability_window_is_rejected(self):
        def degraded(path):
            health = self.health(path)
            if self.clock.elapsed >= 5:
                health["status"] = "DEGRADED"
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "STABILITY_WINDOW_FAILED"):
            self.run_acceptance(health_reader=degraded)

    def test_entry_and_open_management_must_both_be_healthy(self):
        for field in ("ENTRY_PATH", "OPEN_POSITION_MANAGEMENT"):
            with self.subTest(field=field):
                self.clock = FakeClock()
                def degraded(path):
                    health = self.health(path)
                    health[field] = {"status": "DEGRADED"}
                    return health
                with self.assertRaisesRegex(acceptance.AcceptanceError, field + "_NOT_READY"):
                    self.run_acceptance(health_reader=degraded)


class RuntimePreflightTests(unittest.TestCase):
    def write_cache(self, database, source_sha=SHA, *, dirty=False, version=core.PATTERN_CACHE_CONTRACT_VERSION):
        source = {"git_sha": source_sha, "git_dirty": dirty, "pid": 101,
                  "process_started_at": (NOW - timedelta(minutes=1)).isoformat()}
        with sqlite3.connect(database) as con:
            core.ensure_tables(con)
            con.execute("""INSERT INTO paper_behavior_pattern_cache(
                generated_at,horizon_minutes,pattern_count,patterns_json,contract_version,
                producer_version,core_version,producer_git_sha,producer_source_json,
                real_order_allowed) VALUES(?,240,0,'[]',?,?,?,?,?,0)""",
                (NOW.isoformat(), version, core.PATTERN_CACHE_PRODUCER_VERSION,
                 core.VERSION, source_sha, json.dumps(source)))

    def test_valid_empty_cache_is_accepted_readonly(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cache.db"
            self.write_cache(database)
            before = hashlib.sha256(database.read_bytes()).hexdigest()
            result = acceptance.cache_preflight(database, SHA, now=NOW)
            after = hashlib.sha256(database.read_bytes()).hexdigest()
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["cache_status"], "READY")
        self.assertEqual(result["pattern_count"], 0)
        self.assertEqual(before, after)

    def test_cache_rejects_old_contract_wrong_sha_and_dirty_source(self):
        for kwargs, reason in (({"version": "PAPER_BEHAVIOR_PATTERN_CACHE_V1"}, "CONTRACT_MISMATCH"),
                               ({"source_sha": "b" * 40}, "PRODUCER_SHA_MISMATCH"),
                               ({"dirty": True}, "PRODUCER_SOURCE_NOT_CLEAN")):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as tmp:
                database = Path(tmp) / "cache.db"
                self.write_cache(database, **kwargs)
                with self.assertRaisesRegex(acceptance.AcceptanceError, reason):
                    acceptance.cache_preflight(database, SHA, now=NOW)

    def test_missing_database_preflight_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "missing.db"
            with self.assertRaises(acceptance.AcceptanceError):
                acceptance.cache_preflight(database, SHA, now=NOW)
            self.assertFalse(database.exists())

    def test_cache_wait_is_bounded_and_accepts_new_producer_when_ready(self):
        clock = FakeClock()
        def eventually_ready(database, sha):
            if clock.elapsed < 10:
                raise acceptance.AcceptanceError("CONTRACT_MISMATCH")
            return {"status": "ACCEPTED"}
        result = acceptance.wait_for_cache("unused", SHA, clock=clock.monotonic,
                                           sleep=clock.sleep, check=eventually_ready, ready_timeout=20)
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(clock.elapsed, 10)
        with self.assertRaisesRegex(acceptance.AcceptanceError, "CACHE_READINESS_TIMEOUT"):
            acceptance.wait_for_cache("unused", SHA, clock=clock.monotonic, sleep=clock.sleep,
                check=lambda *_: (_ for _ in ()).throw(acceptance.AcceptanceError("MISSING")), ready_timeout=10)
        self.assertEqual(clock.elapsed, 20)

    def test_metadata_preflight_requires_both_routes_and_never_fetches(self):
        from types import SimpleNamespace
        cache = SimpleNamespace(symbols={"BTCUSDT": object()}, fetched_at=NOW.isoformat(), payload_sha256="a" * 64)
        with patch.object(acceptance, "load_cache", side_effect=[cache, exchange_metadata.ExchangeMetadataError("STALE_METADATA_CACHE")]) as loader:
            with self.assertRaisesRegex(acceptance.AcceptanceError, "USD_M_PERPETUAL:STALE_METADATA_CACHE"):
                acceptance.metadata_preflight("/test")
        self.assertEqual(loader.call_count, 2)
        with patch.object(acceptance, "load_cache", return_value=cache):
            self.assertEqual(acceptance.metadata_preflight("/test")["status"], "ACCEPTED")

    def test_installer_preflights_before_restarts_and_accepts_after_all_workers(self):
        source = Path("deploy/install_gce_free_worker.sh").read_text()
        metadata = source.index("-m scripts.runtime_acceptance metadata")
        behavior = source.index("sudo systemctl restart agca-behavior-learning-worker.service")
        cache = source.index("-m scripts.runtime_acceptance cache")
        realtime = source.index("sudo systemctl restart agca-paper-realtime-worker.service")
        accepted = source.index("-m scripts.runtime_acceptance runtime")
        self.assertLess(source.index("EXPECTED_SHA=$(git rev-parse HEAD)"), source.index("if [[ ! -x .venv/bin/python ]]"))
        self.assertLess(source.index("checkout does not match AGCA_EXPECTED_DEPLOY_SHA"), source.index(".venv/bin/pip install"))
        self.assertLess(metadata, source.index("sudo systemctl disable --now"))
        self.assertLess(metadata, behavior)
        self.assertLess(behavior, cache)
        self.assertLess(cache, realtime)
        self.assertLess(realtime, accepted)
        self.assertIn("--window-seconds 60 --ready-timeout 120 --poll-seconds 5", source)


if __name__ == "__main__":
    unittest.main()
