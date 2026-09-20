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
                "tp1_price": 2323, "tp2_price": 2346,
                "tp1_done": False, "tp2_done": False,
                "current_stop_price": 2277,
            }],
            "closed_positions": [],
        }
        view = operator_view(snapshot)
        self.assertEqual(view["işlem_modu"], "PAPER — gerçek emir kapalı")
        self.assertIn("piyasa verisi gecikmiş", view["durum"])
        position = view["açık_pozisyonlar"][0]
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
                "tp1_price": 59400, "tp2_price": 58800,
                "tp1_done": True, "tp2_done": False,
                "current_stop_price": 59880,
            }],
        }
        snapshot["closed_positions"] = [{
            "symbol": "ETHUSDT", "direction": "LONG", "entry_price": 2300,
            "exit_price": 2350, "net_pnl": 45, "total_fees": 5,
            "close_reason": "PROTECTIVE_STOP",
            "entry_time": "2026-09-21T10:00:00+00:00",
            "exit_time": "2026-09-21T11:00:00+00:00",
        }]
        view = operator_view(snapshot)
        self.assertEqual(view["durum"], "Sistem çalışıyor — canlı piyasa verisi geliyor")
        self.assertEqual(view["açık_pozisyonlar"][0]["son_fiyat"], "1 saniyeden kısa süre önce")
        self.assertIsNone(view["açık_pozisyonlar"][0]["uyarı"])
        self.assertEqual(view["açık_pozisyonlar"][0]["tp1_durumu"], "GERÇEKLEŞTİ")
        self.assertEqual(view["kapanan_işlemler"][0]["durum_etiketi"], "KAPANDI")
        self.assertEqual(
            view["kapanan_işlemler"][0]["kapanış_nedeni"],
            "Koruyucu stop çalıştı",
        )


if __name__ == "__main__":
    unittest.main()
