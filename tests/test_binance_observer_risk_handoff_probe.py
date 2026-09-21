import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from scripts import probe_binance_observer_risk_handoff as probe


class ObserverRiskHandoffProbeTests(unittest.TestCase):
    def _collector(self):
        collector = Mock()
        collector.allowed = frozenset({"BTCUSDT", "1000PEPEUSDT"})
        collector.run = AsyncMock(return_value={
            "minute_end_ms": 1_800_000_000_000,
            "bundles": [{"x": 1}],
            "transport": {
                "oi_error_count": 0,
                "websocket_errors": [],
                "subscription_confirmed": {
                    "public": True, "market": True,
                    "deep_public": True, "deep_market": True,
                },
                "liquidation_state": "AVAILABLE_NO_EVENT_IN_WINDOW",
            },
        })
        return collector

    def test_eligible_native_symbols_flow_directly_into_dynamic_risk(self):
        collector = self._collector()
        report = {
            "row_count": 2, "eligible_row_count": 2,
            "rows": [
                {"instrument": "BTCUSDT", "eligible_for_learning": True},
                {"instrument": "1000PEPEUSDT", "eligible_for_learning": True},
            ],
        }
        def metrics(symbol, candles):
            return {"symbol": symbol}
        def classified(row):
            return {
                "symbol": row["symbol"], "risk_tier": "NORMAL",
                "pre_global_max_position_pct": 5.0,
                "as_of": "2026-09-21T20:59:59+00:00",
            }
        with patch.dict(os.environ, {
            "AGCA_EXECUTION_MODE": "PAPER_ONLY",
            "AGCA_REMOTE_DATABASE_URL": "fake-dsn",
        }), patch.object(probe, "_metadata", return_value=(
            {"symbols": []}, "2026-09-21T20:00:00+00:00",
            frozenset({"BTCUSDT", "1000PEPEUSDT"}),
        )), patch.object(probe, "BinanceWsMinuteCollector",
                        return_value=collector), \
             patch.object(probe, "aggregate_closed_minute",
                          return_value=report), \
             patch.object(probe.time, "sleep"), \
             patch.object(probe.time, "time", return_value=1_800_000_000), \
             patch.object(probe.risk, "fetch_dynamic_closed_1h_klines",
                          return_value=[1]) as fetch, \
             patch.object(probe.risk, "candle_metrics",
                          side_effect=metrics), \
             patch.object(probe.risk, "classify_asset_risk",
                          side_effect=classified):
            result = probe.run_probe()
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(
            result["risk_symbols_computed"], ["1000PEPEUSDT", "BTCUSDT"]
        )
        self.assertEqual(
            [call.args[0] for call in fetch.call_args_list],
            ["1000PEPEUSDT", "BTCUSDT"],
        )
        self.assertFalse(result["snapshot_written"])
        self.assertFalse(result["paper_decision_written"])
        self.assertFalse(result["real_orders"])

    def test_invalid_exchange_identity_blocks_before_risk_fetch(self):
        collector = self._collector()
        report = {
            "row_count": 1, "eligible_row_count": 1,
            "rows": [{"instrument": "PEPEUSDT", "eligible_for_learning": True}],
        }
        with patch.dict(os.environ, {
            "AGCA_EXECUTION_MODE": "PAPER_ONLY",
            "AGCA_REMOTE_DATABASE_URL": "fake-dsn",
        }), patch.object(probe, "_metadata", return_value=(
            {"symbols": []}, "2026-09-21T20:00:00+00:00",
            frozenset({"1000PEPEUSDT"}),
        )), patch.object(probe, "BinanceWsMinuteCollector",
                        return_value=collector), \
             patch.object(probe, "aggregate_closed_minute",
                          return_value=report), \
             patch.object(probe.time, "sleep"), \
             patch.object(probe.time, "time", return_value=1_800_000_000), \
             patch.object(probe.risk, "fetch_dynamic_closed_1h_klines") as fetch:
            result = probe.run_probe()
        self.assertEqual(result["status"], "BLOCKED_NATIVE_IDENTITY")
        self.assertEqual(result["invalid_identity"], ["PEPEUSDT"])
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
