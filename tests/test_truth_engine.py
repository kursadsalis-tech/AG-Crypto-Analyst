import unittest
import sqlite3
from unittest import mock
from datetime import datetime, timedelta, timezone

from data.truth_engine import (
    DataValidationError,
    audited_validation,
    validate_closed_klines,
    validate_cross_source_prices,
    validate_news_articles,
    validate_macro_series,
    validate_metric_rows,
    validate_social_records,
    validate_stored_candles,
    validate_whale_events,
)
from risk_manager import risk_manager, signal_risk_gate
from reports import investment_committee_report
from whale_tracker import ethereum_token_whale_collector
from analysis import forward_shadow_cycle


NOW = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)


def candle(open_time, opened=100, high=110, low=90, close=105):
    close_time = open_time + 3_599_999
    return [open_time, opened, high, low, close, 10, close_time, 1000]


class TruthEngineTests(unittest.TestCase):
    def test_cycle_step_isolates_source_failure(self):
        result = forward_shadow_cycle.run_step(
            "news",
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            blocked_status="DATA_BLOCKED",
        )
        self.assertEqual(result["status"], "DATA_BLOCKED")
        self.assertIn("offline", result["message"])

    def test_full_cycle_does_not_use_stale_decision_or_alert_after_risk_block(self):
        skipped = {"status": "SKIPPED"}
        with mock.patch.object(
            forward_shadow_cycle.news_collector,
            "main",
            side_effect=RuntimeError("no validated news"),
        ):
            with mock.patch.object(forward_shadow_cycle.news_analyzer, "main") as news_analysis:
                with mock.patch.object(
                    forward_shadow_cycle.social_sentiment_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.onchain_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.macro_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.whale_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.risk_cycle,
                    "main",
                    return_value={"status": "DATA_BLOCKED"},
                ), mock.patch.object(
                    forward_shadow_cycle.forecast_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.forecast_learning_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.forward_shadow_validator, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.report_cycle, "main", return_value=skipped
                ), mock.patch.object(
                    forward_shadow_cycle.decision_cycle, "main"
                ) as decision, mock.patch.object(
                    forward_shadow_cycle.forward_shadow_product_bridge,
                    "deliver_pending_alerts",
                ) as alerts:
                    result = forward_shadow_cycle.main()
        news_analysis.assert_not_called()
        decision.assert_not_called()
        alerts.assert_not_called()
        self.assertEqual(result["status"], "PARTIAL")
        self.assertIn("risk=DATA_BLOCKED", result["errors"])

    def test_valid_closed_contiguous_klines_pass(self):
        start = int((NOW - timedelta(hours=2, minutes=30)).timestamp() * 1000)
        rows = [candle(start), candle(start + 3_600_000)]
        self.assertEqual(
            validate_closed_klines(rows, source="test", now=NOW, minimum_rows=2),
            rows,
        )

    def test_stale_latest_candle_is_blocked(self):
        start = int((NOW - timedelta(hours=6)).timestamp() * 1000)
        with self.assertRaisesRegex(DataValidationError, "LATEST_CANDLE_STALE"):
            validate_closed_klines([candle(start)], source="test", now=NOW)

    def test_ohlc_inconsistency_is_blocked(self):
        start = int((NOW - timedelta(hours=2)).timestamp() * 1000)
        with self.assertRaisesRegex(DataValidationError, "OHLC_INCONSISTENT"):
            validate_closed_klines(
                [candle(start, opened=100, high=99, low=90, close=105)],
                source="test",
                now=NOW,
            )

    def test_gap_and_open_candle_are_blocked(self):
        start = int((NOW - timedelta(hours=2)).timestamp() * 1000)
        rows = [candle(start), candle(start + 7_200_000)]
        with self.assertRaises(DataValidationError) as caught:
            validate_closed_klines(rows, source="test", now=NOW, minimum_rows=2)
        message = str(caught.exception)
        self.assertIn("NON_CONTIGUOUS", message)
        self.assertIn("NOT_CLOSED", message)

    def test_cross_source_price_deviation_is_blocked(self):
        self.assertLess(
            validate_cross_source_prices(100, 101, source="test"), 2.0
        )
        with self.assertRaisesRegex(DataValidationError, "CROSS_SOURCE_DEVIATION"):
            validate_cross_source_prices(100, 110, source="test")

    def test_invalid_stored_row_is_not_silently_dropped(self):
        start = NOW - timedelta(hours=2)
        rows = [{
            "open_time": start,
            "close_time": start + timedelta(hours=1),
            "open": 100,
            "high": 99,
            "low": 90,
            "close": 105,
            "volume": 1,
            "quote_volume": 100,
        }]
        with self.assertRaisesRegex(DataValidationError, "OHLC_INCONSISTENT"):
            validate_stored_candles(rows, source="stored")

    def test_news_requires_exact_non_future_timestamp(self):
        valid = [{
            "source": "Official",
            "title": "Event",
            "link": "https://example.test/event",
            "published_at": (NOW - timedelta(minutes=5)).isoformat(),
        }]
        self.assertEqual(validate_news_articles(valid, source="news", now=NOW), valid)
        invalid = [dict(valid[0], published_at=(NOW + timedelta(hours=1)).isoformat())]
        with self.assertRaisesRegex(DataValidationError, "PUBLISHED_TIME_FUTURE"):
            validate_news_articles(invalid, source="news", now=NOW)

    def test_observation_source_contracts_block_corruption(self):
        social = [{
            "source": "Reddit",
            "external_id": "abc",
            "title": "BTC",
            "link": "https://example.test/abc",
            "published_at": (NOW - timedelta(minutes=1)).isoformat(),
        }]
        self.assertEqual(
            validate_social_records(social, source="social", now=NOW), social
        )
        with self.assertRaisesRegex(DataValidationError, "NEGATIVE_VALUE"):
            validate_metric_rows(
                [("provider", "chain", "metric", -1, "unit")],
                source="metrics",
            )
        with self.assertRaisesRegex(DataValidationError, "DATE_FUTURE"):
            validate_macro_series(
                [((NOW + timedelta(days=1)).date().isoformat(), 1.0)],
                source="macro", now=NOW,
            )
        with self.assertRaisesRegex(DataValidationError, "BLOCK_TIME_FUTURE"):
            validate_whale_events([{
                "chain": "Bitcoin", "txid": "tx", "output_index": 0,
                "block_time": (NOW + timedelta(hours=1)).isoformat(),
                "amount_native": 1, "usd_estimate": 100, "source": "node",
            }], source="whale", now=NOW)

    def test_audit_records_pass_and_block(self):
        con = sqlite3.connect(":memory:")
        audited_validation(
            con, gate_name="test_gate", source="good", validator=lambda: True,
        )
        with self.assertRaises(DataValidationError):
            audited_validation(
                con,
                gate_name="test_gate",
                source="bad",
                validator=lambda: (_ for _ in ()).throw(
                    DataValidationError("bad", ("BROKEN",))
                ),
            )
        statuses = con.execute(
            "SELECT source,status FROM data_validation_audit ORDER BY id"
        ).fetchall()
        self.assertEqual(statuses, [("good", "PASS"), ("bad", "DATA_BLOCKED")])
        con.close()

    def test_cross_source_failure_forces_data_blocked_policy(self):
        assets = [{
            "symbol": symbol,
            "market_risk_score": 0,
            "market_hard_block": False,
            "hard_block": False,
            "liquidity_hard_block": False,
            "pre_global_max_position_pct": 5.0,
            "risk_tier": "NORMAL",
            "return_24h_pct": 0.0,
            "range_24h_pct": 1.0,
            "drawdown_7d_pct": 0.0,
        } for symbol in risk_manager.SYMBOLS]
        policy = risk_manager.build_global_policy(
            assets, {"CROSS_SOURCE_BTC": "unavailable"}, NOW.isoformat()
        )
        self.assertEqual(policy["market_state"], "DATA_BLOCKED")
        self.assertFalse(policy["new_positions_allowed"])

    def test_future_policy_and_decision_timestamps_are_data_blocked(self):
        future = (NOW + timedelta(hours=1)).isoformat()
        risk = investment_committee_report.policy_status({
            "created_at": future,
            "market_state": "NORMAL",
            "new_positions_allowed": 1,
        }, NOW)
        decision = investment_committee_report.decision_run_status({
            "created_at": future,
        }, NOW)
        self.assertFalse(risk["actionable"])
        self.assertIn("RISK_POLICY_TIME_FUTURE", risk["reasons"])
        self.assertFalse(decision["fresh"])
        self.assertIn("DECISION_SNAPSHOT_TIME_FUTURE", decision["reasons"])

    def test_product_signal_gate_blocks_data_blocked_policy(self):
        con = sqlite3.connect(":memory:")
        con.executescript("""
            CREATE TABLE forward_shadow_predictions(
                model_version TEXT,status TEXT,multifactor_eligible INTEGER,
                hit INTEGER,directional_return REAL
            );
            CREATE TABLE risk_policy_snapshots(
                id INTEGER PRIMARY KEY,created_at TEXT,policy_version TEXT,
                market_state TEXT,coverage_count INTEGER,coverage_total INTEGER,
                max_total_exposure_pct REAL,max_alt_exposure_pct REAL,
                max_single_position_pct REAL,risk_per_trade_pct REAL,
                daily_loss_limit_pct REAL,max_new_positions INTEGER,
                new_positions_allowed INTEGER
            );
            CREATE TABLE asset_risk_snapshots(
                policy_snapshot_id INTEGER,symbol TEXT,as_of TEXT,risk_score REAL,
                risk_tier TEXT,max_position_pct REAL,
                volatility_stop_reference_pct REAL,new_position_allowed INTEGER,
                reason_json TEXT
            );
        """)
        con.execute(
            "INSERT INTO risk_policy_snapshots VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
            (NOW.isoformat(), "test", "DATA_BLOCKED", 13, 13, 0, 0, 0, 0, 0, 0, 0),
        )
        result = signal_risk_gate.evaluate_forward_candidate(
            con, prediction_id=1, symbol="BTCUSDT", now=NOW, persist=False,
        )
        self.assertEqual(result["decision"], "BLOCK")
        self.assertIn("GLOBAL_STATE_DATA_BLOCKED", result["reasons"])
        con.close()

    def test_ethereum_rpc_falls_back_after_provider_failure(self):
        failed = mock.Mock()
        failed.raise_for_status.side_effect = RuntimeError("rate limited")
        passed = mock.Mock()
        passed.raise_for_status.return_value = None
        passed.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        with mock.patch.dict(
            "os.environ",
            {"AGCA_ETHEREUM_RPC_URLS": "https://one.test,https://two.test"},
        ):
            with mock.patch.object(
                ethereum_token_whale_collector, "_preferred_rpc_url", None
            ), mock.patch.object(
                ethereum_token_whale_collector.requests,
                "post",
                side_effect=[failed, passed],
            ) as post:
                result = ethereum_token_whale_collector.rpc_call("eth_test", [])
        self.assertEqual(result, "0x1")
        self.assertEqual(post.call_count, 2)

    def test_ethereum_rpc_reuses_last_healthy_provider(self):
        passed = mock.Mock()
        passed.raise_for_status.return_value = None
        passed.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x1"}
        with mock.patch.dict(
            "os.environ",
            {"AGCA_ETHEREUM_RPC_URLS": "https://one.test,https://two.test"},
        ):
            with mock.patch.object(
                ethereum_token_whale_collector,
                "_preferred_rpc_url",
                "https://two.test",
            ), mock.patch.object(
                ethereum_token_whale_collector.requests,
                "post",
                return_value=passed,
            ) as post:
                ethereum_token_whale_collector.rpc_call("eth_test", [], timeout=60)
        self.assertEqual(post.call_args.args[0], "https://two.test")
        self.assertEqual(post.call_args.kwargs["timeout"], 15.0)


if __name__ == "__main__":
    unittest.main()
