import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from data import okx_copy_trader_collector as collector
from paper_trading import external_trader_learning as external


class OkxCopyTraderCollectorTests(unittest.TestCase):
    def test_history_refresh_prioritizes_known_open_traders(self):
        con = sqlite3.connect(":memory:")
        external.ensure_tables(con)
        ranks = {f"trader-{index}": index + 1 for index in range(40)}
        for index in range(35):
            external.ingest_trade(con, {
                "exchange": "OKX", "source_url": collector.SOURCE_URL,
                "source_trader_id": f"trader-{index}",
                "source_trade_id": f"open-{index}",
                "sampling_frame": "FULL_LEADERBOARD",
                "leaderboard_rank": index + 1, "leaderboard_size": 40,
                "symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN",
                "entry_time": "2026-08-25T00:00:00+00:00",
                "entry_price": 100.0, "notional": 100.0, "leverage": 2.0,
                "reported_pnl": 0.0, "fees": 0.1, "funding": 0.0,
            }, first_observed_at="2026-08-26T00:00:00+00:00")
            # Existing retrospective history must not suppress closure refresh.
            external.ingest_trade(con, {
                "exchange": "OKX", "source_url": collector.SOURCE_URL,
                "source_trader_id": f"trader-{index}",
                "source_trade_id": f"old-{index}",
                "sampling_frame": "FULL_LEADERBOARD",
                "leaderboard_rank": index + 1, "leaderboard_size": 40,
                "symbol": "ETHUSDT", "direction": "LONG", "status": "CLOSED",
                "entry_time": "2026-08-20T00:00:00+00:00",
                "exit_time": "2026-08-21T00:00:00+00:00",
                "entry_price": 100.0, "exit_price": 101.0, "notional": 100.0,
                "leverage": 2.0, "reported_pnl": 1.0,
                "fees": 0.1, "funding": 0.0,
            }, first_observed_at="2026-08-26T00:00:00+00:00")
        selected = collector._history_profiles_for_run(
            con, ranks, datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        next_day = collector._history_profiles_for_run(
            con, ranks, datetime(2026, 8, 28, tzinfo=timezone.utc)
        )
        con.close()
        self.assertEqual(len(selected), collector.MAX_HISTORY_PROFILES_PER_RUN)
        self.assertTrue(all(int(trader.split("-")[1]) < 35 for trader, _ in selected))
        self.assertEqual(
            {trader for trader, _ in selected + next_day},
            {f"trader-{index}" for index in range(35)},
        )

    def test_history_refresh_uses_remaining_capacity_for_new_backfill(self):
        con = sqlite3.connect(":memory:")
        external.ensure_tables(con)
        ranks = {"open": 1, "new-a": 2, "new-b": 3}
        external.ingest_trade(con, {
            "exchange": "OKX", "source_url": collector.SOURCE_URL,
            "source_trader_id": "open", "source_trade_id": "position",
            "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": 1,
            "leaderboard_size": 3, "symbol": "BTCUSDT", "direction": "LONG",
            "status": "OPEN", "entry_time": "2026-08-25T00:00:00+00:00",
            "entry_price": 100.0, "notional": 100.0, "leverage": 2.0,
            "reported_pnl": 0.0, "fees": 0.1, "funding": 0.0,
        }, first_observed_at="2026-08-26T00:00:00+00:00")
        selected = collector._history_profiles_for_run(
            con, ranks, datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        con.close()
        self.assertEqual({trader for trader, _ in selected}, set(ranks))

    def page(self, number, *, version="v1", total=7):
        start = (number - 1) * collector.PAGE_SIZE
        count = min(collector.PAGE_SIZE, total - start)
        return {
            "dataVersion": version, "pages": 2, "total": total,
            "ranks": [{"uniqueName": f"trader-{start + i}"} for i in range(count)],
        }

    def test_full_cohort_requires_every_unique_trader(self):
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), self.page(2)]), \
             patch.object(collector.time, "sleep"):
            result = collector.fetch_full_cohort()
        self.assertEqual(result["total"], 7)
        self.assertEqual(len(result["rows"]), 7)

    def test_version_change_fails_closed(self):
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), self.page(2, version="v2")]), \
             patch.object(collector.time, "sleep"):
            with self.assertRaises(collector.PublicCopyTraderDataError):
                collector.fetch_full_cohort()

    def test_duplicate_trader_fails_closed(self):
        second = self.page(2)
        second["ranks"][0]["uniqueName"] = "trader-0"
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), second]), \
             patch.object(collector.time, "sleep"):
            with self.assertRaises(collector.PublicCopyTraderDataError):
                collector.fetch_full_cohort()

    def test_history_normalizes_costs_and_linear_notional(self):
        row = {
            "instId": "BTC-USDT-SWAP", "posSide": "long",
            "cTime": "1000", "uTime": "2000", "openAvgPx": "100",
            "closeAvgPx": "110", "openMaxAmount": "2", "ctVal": "0.01",
            "quoteCcy": "USDT", "lever": "3", "realizedPnl": "1.5",
            "pnlRatio": "0.1", "fee": "-0.2", "fundingFee": "-0.1",
        }
        result = collector._history_payload("trader", row, 1, 255)
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["notional"], 2.0)
        self.assertEqual(result["fees"], 0.2)
        self.assertEqual(result["reported_roi_pct"], 10.0)


if __name__ == "__main__":
    unittest.main()
