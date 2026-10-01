import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from paper_trading import behavior_decision_core as core
from paper_trading import exchange_metadata
from scripts import runtime_acceptance as acceptance
from runtime import process_identity


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

    def progressing(self, path, *, ready_at=35):
        health = self.health(path)
        if self.clock.elapsed < ready_at:
            health.update(status="RUNNING", last_success_at=None,
                          progress={"phase": "SCAN", "sequence": int(self.clock.elapsed // 5),
                                    "at": self.clock.now().isoformat()})
        return health

    def test_running_progress_extends_cold_start_then_full_stability_window(self):
        result = self.run_acceptance(health_reader=self.progressing, max_ready_seconds=60)
        self.assertEqual(result["readiness_seconds"], 35)
        self.assertEqual(result["stable_window_seconds"], 10)
        self.assertEqual(self.clock.elapsed, 45)

    def test_heartbeat_without_work_progress_times_out(self):
        def heartbeat(path):
            health = self.progressing(path, ready_at=100)
            health["progress"] = {"phase": "SCAN", "sequence": 1, "at": NOW.isoformat()}
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "RUNTIME_READINESS_TIMEOUT:.*NO_PROGRESS"):
            self.run_acceptance(health_reader=heartbeat, max_ready_seconds=100)
        self.assertEqual(self.clock.elapsed, 20)

    def test_one_producer_progress_never_extends_another_stuck_producer(self):
        def one_stuck(path):
            health = self.progressing(path, ready_at=100)
            if Path(path).name == "trade-learning-health.json":
                health["progress"] = {"phase": "SCAN", "sequence": 1, "at": NOW.isoformat()}
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "NO_PROGRESS"):
            self.run_acceptance(health_reader=one_stuck)
        self.assertEqual(self.clock.elapsed, 20)

    def test_progress_cannot_extend_absolute_ceiling(self):
        with self.assertRaisesRegex(acceptance.AcceptanceError, "ABSOLUTE_LIMIT"):
            self.run_acceptance(health_reader=lambda path: self.progressing(path, ready_at=100),
                                max_ready_seconds=30)
        self.assertEqual(self.clock.elapsed, 30)

    def test_previous_pid_progress_never_extends_readiness(self):
        def previous(path):
            health = self.progressing(path, ready_at=100)
            health["process_source"]["pid"] += 1000
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "NO_PROGRESS"):
            self.run_acceptance(health_reader=previous)
        self.assertEqual(self.clock.elapsed, 20)

    def test_stale_authenticated_progress_is_pending_not_invalid(self):
        def stale(path):
            health = self.progressing(path, ready_at=100)
            health["progress"] = {
                "phase": "SCAN",
                "sequence": 7,
                "at": (NOW - timedelta(minutes=10)).isoformat(),
            }
            return health

        with self.assertRaisesRegex(
            acceptance.AcceptanceError,
            "RUNTIME_READINESS_TIMEOUT:.*NO_PROGRESS",
        ):
            self.run_acceptance(
                health_reader=stale,
                max_ready_seconds=100,
            )
        self.assertEqual(self.clock.elapsed, 20)

    def test_progress_sequence_regression_is_fatal(self):
        def regressed(path):
            health = self.progressing(path)
            health["progress"]["sequence"] = 2 if self.clock.elapsed == 0 else 1
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "PROGRESS_REGRESSED"):
            self.run_acceptance(health_reader=regressed)
        self.assertEqual(self.clock.elapsed, 5)

    def test_success_must_be_from_current_process_and_not_after_health(self):
        unit = "agca-trade-learning-worker.service"
        for success in (NOW - timedelta(hours=1), NOW + timedelta(seconds=1)):
            health = self.health("trade-learning-health.json")
            health["last_success_at"] = success.isoformat()
            self.assertIn("PRODUCER_SUCCESS_STALE_OR_FUTURE", acceptance.health_failures(
                unit, health, self.state(unit), SHA, NOW))

    def test_current_pid_identity_replacement_is_fatal_even_without_pid_change(self):
        def replaced(path):
            health = self.health(path)
            if self.clock.elapsed:
                health["process_source"]["process_started_at"] = NOW.isoformat()
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "PROCESS_IDENTITY_CHANGED"):
            self.run_acceptance(health_reader=replaced)
        self.assertEqual(self.clock.elapsed, 5)

    def test_real_order_authority_true_is_fatal_during_readiness(self):
        def unsafe(path):
            health = self.starting_health(path)
            health["real_order_allowed"] = True
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "REAL_ORDER_AUTHORITY_NOT_FALSE"):
            self.run_acceptance(health_reader=unsafe)
        self.assertEqual(self.clock.elapsed, 0)

    def test_explicit_fatal_database_health_fails_immediately(self):
        def failed(path):
            health = self.health(path)
            health.update(status="DEGRADED", fatal_error=True, error="no such table")
            return health
        with self.assertRaisesRegex(acceptance.AcceptanceError, "FATAL_PRODUCER_ERROR"):
            self.run_acceptance(health_reader=failed)
        self.assertEqual(self.clock.elapsed, 0)

    def test_current_success_with_wrong_cache_contract_fails_immediately(self):
        def check(*args, **kwargs):
            raise acceptance.AcceptanceError("CACHE_PREFLIGHT_FAILED:CONTRACT_MISMATCH")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "CONTRACT_MISMATCH"):
            self.run_acceptance(database_path="unused", cache_check=check)
        self.assertEqual(self.clock.elapsed, 0)

    def test_cache_wait_binds_health_and_snapshot_to_current_producer(self):
        def check(database, sha, *, now, expected_source):
            self.assertEqual(expected_source["pid"], self.state("agca-behavior-learning-worker.service")["pid"])
            if self.clock.elapsed < 10:
                raise acceptance.AcceptanceError("CACHE_PREFLIGHT_FAILED:PRODUCER_PROCESS_MISMATCH")
            return {"status": "ACCEPTED"}
        result = acceptance.wait_for_cache(
            "unused", SHA, health_dir="/test", window_seconds=5, ready_timeout=20,
            state_reader=self.state, health_reader=self.health, clock=self.clock.monotonic,
            sleep=self.clock.sleep, now_fn=self.clock.now, cache_check=check
        )
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(self.clock.elapsed, 15)
        self.assertEqual(tuple(result["services"]), ("agca-behavior-learning-worker.service",))

    def test_producer_wait_never_checks_or_restarts_other_units(self):
        requested = []
        unit = "agca-technical-kline-worker.service"
        def read_state(name):
            requested.append(name)
            return self.state(name)
        result = self.run_acceptance(units=(unit,), window_seconds=0, state_reader=read_state,
                                     health_reader=self.progressing)
        self.assertEqual(set(requested), {unit})
        self.assertEqual(result["readiness_seconds"], 35)



class RuntimePreflightTests(unittest.TestCase):
    def test_process_identity_retries_timeout_and_uses_fast_clean_probes(self):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=SHA + "\n", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(
            process_identity.subprocess, "run", side_effect=fake_run
        ), patch.object(process_identity.time, "sleep") as sleeper:
            source = process_identity.capture_process_source("/repo")

        self.assertEqual(source["git_sha"], SHA)
        self.assertFalse(source["git_dirty"])
        self.assertEqual(len(calls), 4)
        sleeper.assert_called_once()

    def test_process_identity_marks_tracked_worktree_change_dirty(self):
        def fake_run(cmd, **_kwargs):
            if cmd[-2:] == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, stdout=SHA + "\n", stderr="")
            if cmd[-2:] == ["diff", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(process_identity.subprocess, "run", side_effect=fake_run):
            source = process_identity.capture_process_source("/repo")

        self.assertEqual(source["git_sha"], SHA)
        self.assertTrue(source["git_dirty"])

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

    def test_cache_requires_current_process_not_just_same_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "cache.db"
            self.write_cache(database)
            source = {"pid": 999, "process_started_at": (NOW - timedelta(minutes=1)).isoformat()}
            with self.assertRaisesRegex(acceptance.AcceptanceError, "PRODUCER_PROCESS_MISMATCH"):
                acceptance.cache_preflight(database, SHA, now=NOW, expected_source=source)
            source["pid"] = 101
            self.assertEqual(acceptance.cache_preflight(database, SHA, now=NOW,
                                                        expected_source=source)["status"], "ACCEPTED")

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
        self.assertIn("--window-seconds 60 --ready-timeout 120 --max-ready-seconds 1800 --poll-seconds 5", source)


class StagedInstallerTests(unittest.TestCase):
    """Execute only the staged block against isolated fake commands, never systemd."""

    units = ("agca-behavior-learning-worker.service", "agca-technical-kline-worker.service",
             "agca-market-behavior-observer.service", "agca-trade-learning-worker.service",
             "agca-paper-realtime-worker.service")

    def run_stages(self, fail_unit=""):
        source = Path("deploy/install_gce_free_worker.sh").read_text()
        block = source.split("# Begin staged producer restart.", 1)[1].split("# End staged producer restart.", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv/bin").mkdir(parents=True)
            driver = root / "driver.py"
            driver.write_text("""import json, os, sys
from pathlib import Path
state_file = Path('calls.json')
state = json.loads(state_file.read_text()) if state_file.exists() else {'events': [], 'restarted': [], 'ready': []}
args = sys.argv[1:]
state['events'].append(args)
failed = False
if args[0] == 'systemctl':
    assert args[1] in {'restart', 'enable', 'start'} and len(args) in {3, 4}, args
    if args[1] == 'start':
        assert all(unit in state['ready'] for unit in state['restarted']), state
    if args[1] == 'restart':
        assert all(unit in state['ready'] for unit in state['restarted']), state
        state['restarted'].append(args[2])
else:
    assert args[:2] == ['-m', 'scripts.runtime_acceptance'], args
    assert args[args.index('--expected-sha') + 1] == 'a' * 40, args
    phase = args[2]
    units = (['agca-behavior-learning-worker.service'] if phase == 'cache' else
             [args[i + 1] for i, arg in enumerate(args) if arg == '--service'])
    assert all(unit in state['restarted'] for unit in units), state
    failed = bool(os.getenv('FAIL_UNIT') in units)
    if not failed:
        state['ready'].extend(units)
state_file.write_text(json.dumps(state))
sys.exit(17 if failed else 0)
""")
            fake_python = root / ".venv/bin/python"
            fake_python.write_text('#!/bin/sh\nexec "$REAL_PYTHON" "$DRIVER" "$@"\n')
            fake_python.chmod(0o755)
            # Only this function can receive sudo; it writes the isolated log.
            prefix = ('set -euo pipefail\n'
                      'sudo() { "$REAL_PYTHON" "$DRIVER" "$@"; }\n'
                      'EXPECTED_SHA=' + SHA + '\nDB=unused\nDATA_DIR=unused\nactive_timers=(agca-risk-refresh.timer)\n')
            import os
            env = {**os.environ, "REAL_PYTHON": sys.executable, "DRIVER": str(driver), "FAIL_UNIT": fail_unit}
            result = subprocess.run(["bash", "-s"], input=prefix + block, cwd=root, env=env,
                                    capture_output=True, text=True, timeout=10)
            calls = json.loads((root / "calls.json").read_text())
            return result, calls

    def test_every_restart_waits_for_exact_current_producer_and_realtime_is_last(self):
        result, calls = self.run_stages()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls["restarted"], list(self.units))
        commands = calls["events"]
        realtime_index = commands.index(["systemctl", "restart", self.units[-1]])
        all_producers = [args for args in commands[:realtime_index] if args.count("--service") == 4]
        self.assertEqual(len(all_producers), 1)
        self.assertEqual(commands[realtime_index - 2][2], "metadata")

    def test_failed_producer_never_starts_next_writer_or_realtime(self):
        for index, unit in enumerate(self.units[:-1]):
            with self.subTest(unit=unit):
                result, calls = self.run_stages(unit)
                self.assertEqual(result.returncode, 17, result.stderr)
                self.assertEqual(calls["restarted"], list(self.units[:index + 1]))

    def test_timers_are_quiesced_before_schema_and_resumed_after_producers(self):
        source = Path("deploy/install_gce_free_worker.sh").read_text()
        self.assertLess(source.index('sudo systemctl stop "$timer"'), source.index('bootstrap_schema(os.environ'))
        self.assertLess(source.index('sudo systemctl enable --now "$timer"'),
                        source.index('sudo systemctl restart agca-paper-realtime-worker.service'))


if __name__ == "__main__":
    unittest.main()
