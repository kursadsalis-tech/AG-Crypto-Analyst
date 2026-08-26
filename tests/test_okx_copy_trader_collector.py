import sqlite3
import unittest
from unittest.mock import patch

from data import okx_copy_trader_collector as collector


class OkxCopyTraderCollectorTests(unittest.TestCase):
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
