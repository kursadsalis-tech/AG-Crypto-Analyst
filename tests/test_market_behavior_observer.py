import asyncio
from contextlib import asynccontextmanager
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import collect_closed_behavior_minute as observer
from data import binance_live_evidence as live


class MarketBehaviorObserverTests(unittest.TestCase):
    def test_continuous_mode_reuses_process_and_releases_lock_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = str(Path(tmp) / "observer.lock")
            argv = ["observer", "--output", str(Path(tmp) / "report.json"),
                    "--lock-file", lock, "--continuous"]
            with patch.object(observer.sys, "argv", argv), patch.object(
                observer, "run_cycle", side_effect=[None, RuntimeError("cycle failed")]
            ) as cycle:
                with self.assertRaisesRegex(RuntimeError, "cycle failed"):
                    observer.main()
                self.assertEqual(cycle.call_count, 2)
            with open(lock, "w") as handle:
                observer.fcntl.flock(handle, observer.fcntl.LOCK_EX | observer.fcntl.LOCK_NB)

    def test_failed_cycle_cancels_deadline_and_restores_signal_handler(self):
        args = type("Args", (), {"cycle_timeout_seconds": 240})()
        with patch.object(observer.signal, "signal", return_value="previous") as handler, patch.object(observer.signal, "alarm") as alarm, patch.object(observer, "run_cycle", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                observer.run_bounded_cycle(args)
        self.assertEqual([call.args for call in alarm.call_args_list], [(240,), (0,)])
        self.assertEqual(handler.call_args.args, (observer.signal.SIGALRM, "previous"))

    def test_cycle_deadline_cannot_be_swallowed_by_channel_error_handler(self):
        args = type("Args", (), {"cycle_timeout_seconds": 240})()
        def channel_cycle(_args):
            try:
                observer._cycle_timeout(None, None)
            except Exception:
                return "CHANNEL_ERROR"
        with patch.object(observer, "run_cycle", side_effect=channel_cycle), patch.object(observer.signal, "alarm") as alarm:
            with self.assertRaisesRegex(SystemExit, "time budget"):
                observer.run_bounded_cycle(args)
        self.assertEqual(alarm.call_args.args, (0,))

    def test_one_shot_mode_still_runs_once(self):
        with patch.object(observer.sys, "argv", ["observer", "--output", "/unused"]), patch.object(observer, "run_cycle") as cycle:
            observer.main()
        cycle.assert_called_once()

    def test_alignment_skips_partial_behavior_window(self):
        target, skipped = observer.collection_alignment(30_000, 38)
        self.assertEqual(target, 120_000)
        self.assertEqual(skipped, 60_000)

    def test_alignment_uses_next_minute_when_full_behavior_span_fits(self):
        target, skipped = observer.collection_alignment(20_000, 38)
        self.assertEqual(target, 60_000)
        self.assertIsNone(skipped)

    def test_late_hydration_keeps_all_four_samples_in_the_actual_target_minute(self):
        clock = SimpleNamespace(now=20.0)
        clock.time = clock.monotonic = lambda: clock.now
        real_sleep = asyncio.sleep
        active = set()
        stamps = []
        deadlines = []
        completed = asyncio.Event()

        async def sleep(delay):
            # The old eight-second setup estimate chose minute 60 here;
            # actual hydration at second 34 would put sample 4 outside it.
            clock.now += 14.0 if delay == 0.05 and clock.now == 20.0 else delay
            await real_sleep(0)

        @asynccontextmanager
        async def connection(_url, *, phase, **_kwargs):
            active.add(phase)
            sent = False
            async def recv():
                nonlocal sent
                if sent:
                    await asyncio.Future()
                sent = True
                payload = {"s": "BTCUSDT", "E": int(clock.now * 1000)}
                payload.update({"e": "depthUpdate", "b": [["100", "1"]],
                                "a": [["101", "1"]]} if phase == "public" else
                               {"e": "markPriceUpdate", "r": "0.001", "T": 120_000})
                return json.dumps(payload)
            try:
                yield SimpleNamespace(recv=recv)
            finally:
                active.remove(phase)

        def snapshot(_states, stamp, *_connected):
            self.assertEqual(active, {"public", "market"})
            stamps.append(stamp)
            if len(stamps) == 4:
                completed.set()
            return {"collected_timestamp_ms": stamp}

        async def probe(*_args, observe_until_ms, **_kwargs):
            deadlines.append(observe_until_ms())
            await completed.wait()
            deadlines.append(observe_until_ms())
            return {}

        timings = {}
        args = SimpleNamespace(samples=4, cadence_seconds=10)
        with patch.object(observer, "time", clock), patch.object(live, "time", clock), patch.object(
            asyncio, "sleep", new=sleep
        ), patch.object(live, "measured_connection", connection), patch.object(
            live, "_snapshot", side_effect=snapshot
        ), patch.object(observer, "probe", new=probe), patch.object(
            observer, "build_bundle", side_effect=lambda market, _liquidations: market
        ):
            bundles, target, alignment = asyncio.run(observer.collect_minute(
                {"deep_stream_instruments": ["BTC-USDT-SWAP"]},
                {"selected": [{"instrument": "BTC-USDT-SWAP", "symbol": "BTCUSDT"}]},
                {}, args, timings,
            ))
        self.assertEqual(target, 120_000)
        self.assertEqual(len(bundles), 4)
        self.assertTrue(all(target - 60_000 <= stamp < target for stamp in stamps))
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertAlmostEqual(later - earlier, 10_000, delta=1)
        self.assertGreater(deadlines[0], 120_000)
        self.assertEqual(deadlines[-1], target)
        self.assertTrue(alignment["late_start_recovery"])
        self.assertAlmostEqual(alignment["alignment_wait_ms"], 26_020, delta=1)
        self.assertEqual(timings["deep_hydration"], 14_000)
        self.assertEqual(active, set())

    def test_collection_without_target_cancels_liquidation_instead_of_waiting(self):
        cancelled = []
        async def probe(*_args, **_kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.append(True)
        with patch.object(observer, "probe", new=probe), patch.object(
            observer, "collect_minute_snapshots", new=AsyncMock(return_value=[])
        ):
            with self.assertRaisesRegex(RuntimeError, "target minute"):
                asyncio.run(observer.collect_minute({}, {}, {}, SimpleNamespace(
                    samples=4, cadence_seconds=10)))
        self.assertEqual(cancelled, [True])

    def test_cancelled_collection_cancels_both_stream_tasks(self):
        cancelled = []
        running = []
        started = asyncio.Event()
        async def waiting(name):
            running.append(name)
            if len(running) == 2:
                started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.append(name)
        async def probe(**_kwargs):
            await waiting("liquidation")
        async def collect(*_args, **_kwargs):
            await waiting("market")
        async def run():
            task = asyncio.create_task(observer.collect_minute({}, {}, {}, SimpleNamespace(
                samples=4, cadence_seconds=10)))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with patch.object(observer, "probe", new=probe), patch.object(
            observer, "collect_minute_snapshots", new=collect
        ):
            asyncio.run(run())
        self.assertCountEqual(cancelled, ["liquidation", "market"])

    def test_refresh_builds_market_wide_route(self):
        tickers = [{
            "s": "AAAUSDT", "c": "10", "o": "9", "q": "10000000",
            "b": "10", "a": "10.01",
        }]
        with patch.object(
            observer, "fetch_market_tickers",
            new=AsyncMock(return_value=tickers),
        ), patch.object(observer.time, "time", return_value=20_000_000):
            universe, route = observer.refresh_market_route()
        self.assertEqual(universe["selected_count"], 1)
        self.assertEqual(
            universe["selection_contract"]["exchange"], "BINANCE"
        )
        self.assertEqual(
            universe["selection_contract"]["transport"],
            "USD_M_ROUTED_WEBSOCKET_TICKER_PLUS_BOOK",
        )
        self.assertEqual(route["deep_stream_instruments"], ["AAA-USDT-SWAP"])

    def test_behavior_collection_does_not_request_technical_kline_sink(self):
        args = type("Args", (), {"samples": 1, "cadence_seconds": 1})()
        async def collect_snapshots(*_args, before_sampling, **_kwargs):
            await before_sampling()
            return []
        with patch.object(observer.time, "time", return_value=20.0), patch.object(
            observer, "probe", new=AsyncMock(return_value=[]),
        ), patch.object(
            observer, "collect_minute_snapshots",
            new=AsyncMock(side_effect=collect_snapshots),
        ) as collect:
            asyncio.run(observer.collect_minute(
                {"deep_stream_instruments": []},
                {"selected": []},
                {},
                args,
            ))
        self.assertIsNone(collect.await_args.kwargs["closed_bar_sink"])
        self.assertIsNone(collect.await_args.kwargs["observe_until_ms"])

    def test_atomic_health_write_is_private_and_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            observer.write_json_atomic(path, {"status": "HEALTHY"})
            self.assertEqual(json.loads(path.read_text()), {"status": "HEALTHY"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(Path(tmp).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
