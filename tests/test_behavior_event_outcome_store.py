import sqlite3
import tempfile
import unittest
from pathlib import Path

from database.behavior_event_outcome_store import (
    OUTCOME_TABLE, backfill_market_structure_outcomes,
)
from database.market_behavior_store import persist_closed_minute
from database.runtime_store import REMOTE_RUNTIME_TABLES


def report(minute_end, prices, evidence=None):
    rows = []
    for instrument, price in prices.items():
        rows.append({
            "instrument": instrument,
            "minute_start_timestamp_ms": minute_end - 60_000,
            "sample_count": 4, "max_sample_gap_ms": 10_000,
            "book_imbalance_20_mean": 0.2,
            "book_imbalance_20_last": 0.3,
            "taker_imbalance_snapshot_mean": -0.4,
            "taker_imbalance_snapshot_last": -0.2,
            "spread_bps_mean": 1.5,
            "mid_price_first": price, "mid_price_last": price,
            "open_interest_usd_first": 100,
            "open_interest_usd_last": 95,
            "open_interest_usd_change": -5,
            "funding_rate_last": 0.0001,
            "long_liquidation_notional_usd_estimate": 1000,
            "short_liquidation_notional_usd_estimate": 0,
            "liquidation_event_count": 1,
            "quality_gates": {"all": True},
            "eligible_for_learning": True,
        })
    return {
        "minute_is_closed": True,
        "minute_end_timestamp_ms": minute_end,
        "causal_external_evidence": evidence or {},
        "rows": rows,
    }


def event(symbols=None):
    return {
        "assignment_clock": "AVAILABLE_AT",
        "events": [{
            "event_id": "event-1", "kind": "NEWS", "source": "source",
            "occurred_at": "1970-01-01T00:01:10+00:00",
            "available_at": "1970-01-01T00:01:10+00:00",
            "symbols": symbols or [], "direction": "UNKNOWN", "detail": {},
        }],
    }


class BehaviorEventOutcomeStoreTests(unittest.TestCase):
    def test_non_overlapping_endogenous_market_structure_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.db"
            instrument = {"BTC-USDT-SWAP": 100}
            event_minute = 14_400_000
            persist_closed_minute(path, report(event_minute - 60_000, instrument))
            persist_closed_minute(path, report(event_minute, {"BTC-USDT-SWAP": 101}))
            outcome_minute = event_minute + 239 * 60_000
            result = persist_closed_minute(
                path, report(outcome_minute, {"BTC-USDT-SWAP": 104})
            )
            self.assertEqual(result["forward_outcomes"]["settled_by_horizon"], {"240": 1})
            with sqlite3.connect(path) as con:
                row = con.execute(f"""SELECT kind,source,instrument,horizon_minutes
                    FROM {OUTCOME_TABLE}""").fetchone()
            self.assertEqual(
                row, ("MARKET_STRUCTURE", "AGCA_MARKET_BEHAVIOR", "BTC-USDT-SWAP", 240)
            )
            with sqlite3.connect(path) as con:
                self.assertEqual(
                    backfill_market_structure_outcomes(con),
                    {"examined": 1, "inserted": 0},
                )

    def test_settles_only_closed_horizons_and_is_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.db"
            instruments = {"BTC-USDT-SWAP": 100, "ETH-USDT-SWAP": 200}
            persist_closed_minute(path, report(60_000, instruments))
            event_result = persist_closed_minute(
                path,
                report(120_000, {
                    "BTC-USDT-SWAP": 98, "ETH-USDT-SWAP": 196,
                }, event()),
            )
            self.assertEqual(
                event_result["forward_outcomes"]["settled_by_horizon"], {"1": 2}
            )
            persist_closed_minute(
                path, report(300_000, {
                    "BTC-USDT-SWAP": 101, "ETH-USDT-SWAP": 202,
                })
            )
            with sqlite3.connect(path) as con:
                five_before_close = con.execute(
                    f"SELECT COUNT(*) FROM {OUTCOME_TABLE} WHERE horizon_minutes=5"
                ).fetchone()[0]
            self.assertEqual(five_before_close, 0)

            closed = persist_closed_minute(
                path, report(360_000, {
                    "BTC-USDT-SWAP": 102, "ETH-USDT-SWAP": 204,
                })
            )
            self.assertEqual(closed["forward_outcomes"]["settled"], 2)
            persist_closed_minute(
                path, report(360_000, {
                    "BTC-USDT-SWAP": 999, "ETH-USDT-SWAP": 999,
                })
            )
            with sqlite3.connect(path) as con:
                rows = con.execute(f"""
                    SELECT instrument,return_from_pre_event_pct,independence_unit
                    FROM {OUTCOME_TABLE} WHERE horizon_minutes=5
                    ORDER BY instrument
                """).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertAlmostEqual(rows[0][1], 2.0)
            self.assertAlmostEqual(rows[1][1], 2.0)
            self.assertEqual(rows[0][2], rows[1][2])

    def test_closed_minute_settlement_hot_path_never_runs_schema_ddl(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "database/behavior_event_outcome_store.py"
        ).read_text(encoding="utf-8")
        start = source.index("def settle_outcomes_for_closed_minute(")
        block = source[start:]
        self.assertIn("_require_outcome_table(connection)", block)
        self.assertNotIn("ensure_outcome_table(connection)", block)

    def test_symbol_scope_and_local_only_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.db"
            prices = {"BTC-USDT-SWAP": 100, "ETH-USDT-SWAP": 200}
            persist_closed_minute(path, report(60_000, prices))
            result = persist_closed_minute(
                path, report(120_000, prices, event(["BTC"]))
            )
            self.assertEqual(result["forward_outcomes"]["settled"], 1)
            with sqlite3.connect(path) as con:
                instrument = con.execute(
                    f"SELECT instrument FROM {OUTCOME_TABLE}"
                ).fetchone()[0]
            self.assertEqual(instrument, "BTC-USDT-SWAP")
        self.assertNotIn(OUTCOME_TABLE, REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
