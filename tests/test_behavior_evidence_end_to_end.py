import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from data.behavior_event_forward_measurement import audit
from database.causal_evidence_store import (
    evidence_for_closed_minute, save_exact_macro_event,
)
from database.behavior_event_outcome_store import settle_queued_outcomes
from database.market_behavior_store import persist_closed_minute
from database.runtime_store import connect


def behavior_report(minute_end, price, evidence=None):
    return {
        "minute_is_closed": True,
        "minute_end_timestamp_ms": minute_end,
        "causal_external_evidence": evidence or {},
        "rows": [{
            "instrument": "BTC-USDT-SWAP",
            "minute_start_timestamp_ms": minute_end - 60_000,
            "sample_count": 4, "max_sample_gap_ms": 10_000,
            "book_imbalance_20_mean": 0.2, "book_imbalance_20_last": 0.3,
            "taker_imbalance_snapshot_mean": -0.4,
            "taker_imbalance_snapshot_last": -0.2,
            "spread_bps_mean": 1.5,
            "mid_price_first": price, "mid_price_last": price,
            "open_interest_usd_first": 100, "open_interest_usd_last": 95,
            "open_interest_usd_change": -5, "funding_rate_last": 0.0001,
            "long_liquidation_notional_usd_estimate": 1000,
            "short_liquidation_notional_usd_estimate": 0,
            "liquidation_event_count": 1,
            "quality_gates": {"all": True}, "eligible_for_learning": True,
        }],
    }


class BehaviorEvidenceEndToEndTests(unittest.TestCase):
    def test_exact_macro_flows_to_minute_and_closed_reaction_without_lookahead(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "runtime.db"
            persist_closed_minute(database, behavior_report(60_000, 100))
            event = {
                "event_id": "fomc", "source": "Federal Reserve",
                "event_name": "FOMC_STATEMENT",
                "occurred_at": "1970-01-01T00:01:30+00:00",
                "available_at": "1970-01-01T00:01:30+00:00",
            }
            with connect(database) as con:
                save_exact_macro_event(
                    con, event, observed_at="1970-01-01T00:01:35+00:00"
                )
                con.commit()
            evidence = evidence_for_closed_minute(
                database, 120_000,
                now=datetime(1970, 1, 1, 0, 3, tzinfo=timezone.utc),
            )
            self.assertEqual(evidence["counts"]["MACRO"], 1)
            persist_closed_minute(
                database, behavior_report(120_000, 98, evidence)
            )
            persist_closed_minute(database, behavior_report(360_000, 102))
            with connect(database) as con:
                settle_queued_outcomes(con, limit=50)
                result = audit(con)
        self.assertEqual(result["exact_minute_join_events"], 1)
        self.assertEqual(result["forward_outcomes_by_horizon"]["5"], 1)
        self.assertEqual(result["durable_outcomes_by_horizon"]["1"], 1)
        self.assertEqual(result["durable_outcomes_by_horizon"]["5"], 1)
        self.assertEqual(result["durable_independent_event_count"], 1)
        self.assertAlmostEqual(
            result["reaction_summary"]["MACRO:5"]
                  ["mean_return_from_pre_event_pct"],
            2.0,
        )
        behavior = result["event_minute_behavior_summary"]["MACRO"]
        self.assertEqual(behavior["mean_open_interest_usd_change"], -5.0)
        self.assertEqual(behavior["mean_liquidation_event_count"], 1.0)


if __name__ == "__main__":
    unittest.main()
