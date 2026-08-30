import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from paper_trading import external_trader_learning as external
from paper_trading import external_trader_rationale as rationale
from database.runtime_store import REMOTE_RUNTIME_TABLES


ENTRY = datetime(2026, 8, 25, 12, tzinfo=timezone.utc)


class ExternalTraderRationaleTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        external.ensure_tables(self.con)
        self.con.execute("""
            CREATE TABLE market_klines(
                symbol TEXT,interval TEXT,open_time TEXT,close_time TEXT,close REAL,high REAL,
                low REAL,volume REAL,quote_volume REAL,taker_buy_quote_volume REAL
            )
        """)
        for offset in range(25):
            close_at = ENTRY - timedelta(hours=25 - offset)
            price = 100 + offset
            self.con.execute(
                "INSERT INTO market_klines VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("BTCUSDT", "1h", (close_at - timedelta(hours=1)).isoformat(),
                 close_at.isoformat(), price, price + 1,
                 price - 1, 100 + offset, 1000, 600),
            )

    def tearDown(self):
        self.con.close()

    def trade(self, status="OPEN", pnl=None):
        payload = {
            "exchange": "OKX", "source_url": "https://www.okx.com/copy-trading",
            "source_trader_id": "trader", "source_trade_id": "trade",
            "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": 1,
            "leaderboard_size": 100, "symbol": "BTCUSDT", "direction": "LONG",
            "status": status, "entry_time": ENTRY.isoformat(),
            "entry_price": 124.0, "notional": 100.0, "leverage": 2.0,
            "fees": 0.1, "funding": 0.0,
        }
        if status == "CLOSED":
            payload.update({
                "exit_time": (ENTRY + timedelta(hours=4)).isoformat(),
                "exit_price": 120.0, "reported_pnl": pnl,
            })
        return payload

    def test_context_is_frozen_while_open_without_outcome_or_intent_claim(self):
        opened = self.trade()
        observed = ENTRY + timedelta(minutes=15)
        external.ingest_trade(self.con, opened, first_observed_at=observed)
        result = rationale.capture_open_trade_context(self.con, opened, observed)
        self.assertEqual(result["status"], "CONTEXT_CAPTURED")
        row = self.con.execute("""
            SELECT context_cutoff_at,context_json,rationale_hypotheses_json,
                   outcome_known_at_capture,intent_claimed
            FROM external_trader_entry_contexts
        """).fetchone()
        context = json.loads(row[1])
        hypotheses = json.loads(row[2])
        self.assertEqual(row[0], ENTRY.isoformat())
        self.assertFalse(context["outcome_used"])
        self.assertFalse(context["intent_claimed"])
        self.assertTrue(hypotheses)
        self.assertEqual(row[3:], (0, 0))

    def test_loss_review_uses_only_previously_frozen_context_and_never_copies(self):
        opened = self.trade()
        first_seen = ENTRY + timedelta(minutes=15)
        external.ingest_trade(self.con, opened, first_observed_at=first_seen)
        rationale.capture_open_trade_context(self.con, opened, first_seen)
        external.ingest_trade(
            self.con, self.trade("CLOSED", -4.0),
            first_observed_at=ENTRY + timedelta(hours=5),
        )
        result = rationale.review_available_closed_contexts(
            self.con, ENTRY + timedelta(hours=6)
        )
        self.assertEqual(result["new_reviews"], 1)
        row = self.con.execute("""
            SELECT outcome_class,assessment_json,intent_claimed,copy_allowed,
                   policy_mutation_allowed FROM external_trader_rationale_reviews
        """).fetchone()
        assessment = json.loads(row[1])
        self.assertEqual(row[0], "LOSS")
        self.assertIn("MOMENTUM_DID_NOT_PERSIST", assessment["possible_error_hypotheses"])
        self.assertFalse(assessment["causal_claim"])
        self.assertEqual(row[2:], (0, 0, 0))

    def test_closed_only_history_cannot_receive_retrofitted_rationale(self):
        external.ingest_trade(
            self.con, self.trade("CLOSED", 3.0),
            first_observed_at=ENTRY + timedelta(hours=5),
        )
        result = rationale.review_available_closed_contexts(self.con)
        self.assertEqual(result["new_reviews"], 0)

    def test_win_review_keeps_frozen_hypotheses_without_causal_claim(self):
        opened = self.trade()
        first_seen = ENTRY + timedelta(minutes=15)
        external.ingest_trade(self.con, opened, first_observed_at=first_seen)
        rationale.capture_open_trade_context(self.con, opened, first_seen)
        frozen = self.con.execute(
            "SELECT context_sha256 FROM external_trader_entry_contexts"
        ).fetchone()[0]
        external.ingest_trade(
            self.con, self.trade("CLOSED", 5.0),
            first_observed_at=ENTRY + timedelta(hours=5),
        )
        result = rationale.review_available_closed_contexts(
            self.con, ENTRY + timedelta(hours=6)
        )
        self.assertEqual(result["new_reviews"], 1)
        row = self.con.execute("""
            SELECT outcome_class,assessment_json,context_sha256
            FROM external_trader_rationale_reviews
        """).fetchone()
        assessment = json.loads(row[1])
        self.assertEqual(row[0], "WIN")
        self.assertTrue(assessment["frozen_hypotheses"])
        self.assertFalse(assessment["causal_claim"])
        self.assertFalse(assessment["copy_instruction"])
        self.assertEqual(row[2], frozen)

    def test_rationale_tables_remain_local_only(self):
        self.assertNotIn("external_trader_entry_contexts", REMOTE_RUNTIME_TABLES)
        self.assertNotIn("external_trader_rationale_reviews", REMOTE_RUNTIME_TABLES)


if __name__ == "__main__":
    unittest.main()
