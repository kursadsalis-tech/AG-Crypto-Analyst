import unittest

from control_center.presentation import operator_view


class ControlCenterPresentationTests(unittest.TestCase):
    def test_operator_view_is_human_readable_and_warns_on_stale_price(self):
        snapshot = {
            "system": {
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "open_positions": 1, "live_positions": 0,
                "market_data_status": "GECİKMİŞ",
            },
            "portfolio": {
                "initial_capital": 10000, "cash": 8000,
                "realized_pnl": 25.5, "total_fees": 5,
                "unrealized_pnl": -12.25,
            },
            "positions": [{
                "symbol": "ETHUSDT", "market_type": "USD_M_PERPETUAL",
                "direction": "LONG", "entry_price": 2300,
                "last_price": 2294, "best_bid": 2293.9, "best_ask": 2294,
                "unrealized_pnl": -12.25, "freshness_seconds": 8.2,
                "market_status": "GECİKMİŞ",
                "decision_reason": "PAPER opportunity",
            }],
        }
        view = operator_view(snapshot)
        self.assertEqual(view["işlem_modu"], "PAPER — gerçek emir kapalı")
        self.assertIn("piyasa verisi gecikmiş", view["durum"])
        position = view["pozisyonlar"][0]
        self.assertEqual(position["piyasa"], "Binance USD-M")
        self.assertEqual(position["durum"], "GECİKMİŞ")
        self.assertIn("ajan güncel fiyatı görmüyor olabilir", position["uyarı"])
        self.assertEqual(position["son_fiyat"], "8.2 saniye önce")

    def test_operator_view_reports_live_market_plainly(self):
        snapshot = {
            "system": {
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "open_positions": 1, "live_positions": 1,
                "market_data_status": "CANLI",
            },
            "portfolio": {
                "initial_capital": 10000, "cash": 9000,
                "realized_pnl": 0, "total_fees": 0, "unrealized_pnl": 10,
            },
            "positions": [{
                "symbol": "BTCUSDT", "market_type": "USD_M_PERPETUAL",
                "direction": "SHORT", "entry_price": 60000,
                "last_price": 59900, "best_bid": 59899, "best_ask": 59901,
                "unrealized_pnl": 10, "freshness_seconds": 0.4,
                "market_status": "CANLI", "decision_reason": "PAPER opportunity",
            }],
        }
        view = operator_view(snapshot)
        self.assertEqual(view["durum"], "Sistem çalışıyor — canlı piyasa verisi geliyor")
        self.assertEqual(view["pozisyonlar"][0]["son_fiyat"], "1 saniyeden kısa süre önce")
        self.assertIsNone(view["pozisyonlar"][0]["uyarı"])


if __name__ == "__main__":
    unittest.main()
