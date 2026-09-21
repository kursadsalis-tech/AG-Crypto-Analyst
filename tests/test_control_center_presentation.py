import unittest

from control_center.presentation import operator_view


class ControlCenterPresentationTests(unittest.TestCase):
    def test_operator_view_is_human_readable_and_warns_on_stale_price(self):
        snapshot = {
            "system": {
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "open_positions": 1, "live_positions": 0,
                "market_data_status": "GECİKMİŞ", "worker_healthy": True,
                "worker_status": "CONNECTED", "heartbeat_age_seconds": 2,
                "last_action": "HOLD", "market_feed_fresh": False,
                "observed_symbols": 1, "fresh_market_symbols": 0,
                "market_snapshot_age_seconds": 45,
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
                "decision_flow": {
                    "observation": {"status":"GECİKMİŞ","source":"BINANCE_USD_M_WS_TICKER_BID_ASK"},
                    "opportunity": {"reason":"PAPER opportunity"},
                    "decision": {"direction":"LONG","source":"PAPER_OPPORTUNITY_POLICY","reference_id":101},
                    "execution": {"status":"AÇIK","entry_price":2300,"mark_price":2293.9,"unrealized_pnl":-12.25},
                },
            }],
            "risk": {"open_positions":1,"stale_positions":1,"positions":[]},
            "learning": {"status":"COLLECTING_EVIDENCE","closed_count":12,"independent_weeks":1,"promotion_allowed":False,"learning_version":"PAPER_TRADE_LEARNING_REVIEW_V1"},
            "closed_positions": [],
        }
        view = operator_view(snapshot)
        self.assertEqual(view["işlem_modu"], "PAPER — gerçek emir kapalı")
        self.assertEqual(view["kanonik_tarama_yaşı"], "fiyat zamanı bilinmiyor")
        self.assertEqual(view["kanonik_tarama_güncel"], "HAYIR")
        self.assertEqual(view["son_fırsat_taraması"]["durum"], "KAYIT YOK")
        self.assertIn("piyasa verisi gecikmiş", view["durum"])
        position = view["açık_pozisyonlar"][0]
        self.assertEqual(position["piyasa"], "Binance USD-M")
        self.assertEqual(position["durum"], "VERİ GECİKİYOR")
        self.assertIn("ajan güncel fiyatı görmüyor olabilir", position["uyarı"])
        self.assertEqual(position["son_fiyat"], "8.2 saniye önce")
        self.assertEqual(position["karar_akışı"]["karar"], "LONG — PAPER_OPPORTUNITY_POLICY")
        self.assertIn("yeni deneyim sonraki kararları besleyebilir", view["öğrenme"]["anlık_adaptasyon"])
        self.assertEqual(view["öğrenme"]["kalıcı_politika_terfisi"], "HENÜZ DOĞRULANMADI")

    def test_operator_view_reports_live_market_plainly(self):
        snapshot = {
            "system": {
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "open_positions": 1, "live_positions": 1,
                "market_data_status": "CANLI", "worker_healthy": True,
                "worker_status": "CONNECTED", "heartbeat_age_seconds": 1,
                "last_action": "HOLD", "market_feed_fresh": True,
                "observed_symbols": 1, "fresh_market_symbols": 1,
                "market_snapshot_age_seconds": 1,
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
                "decision_flow": {
                    "observation": {"status":"CANLI","source":"BINANCE_USD_M_WS_TICKER_BID_ASK"},
                    "opportunity": {"reason":"PAPER opportunity"},
                    "decision": {"direction":"SHORT","source":"PAPER_OPPORTUNITY_POLICY","reference_id":102},
                    "execution": {"status":"AÇIK","entry_price":60000,"mark_price":59901,"unrealized_pnl":10},
                },
            }],
            "risk": {"open_positions":1,"stale_positions":0,"positions":[]},
            "learning": {"status":"COLLECTING_EVIDENCE","closed_count":12,"independent_weeks":1,"promotion_allowed":False,"learning_version":"PAPER_TRADE_LEARNING_REVIEW_V1"},
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
        self.assertEqual(view["izlenen_piyasa"], "1 sembol · akış canlı · 1 son quote ≤5 sn")
        self.assertEqual(view["açık_pozisyonlar"][0]["son_fiyat"], "1 saniyeden kısa süre önce")
        self.assertIsNone(view["açık_pozisyonlar"][0]["uyarı"])
        self.assertEqual(view["açık_pozisyonlar"][0]["tp1_durumu"], "GERÇEKLEŞTİ")
        self.assertEqual(view["kapanan_işlemler"][0]["durum_etiketi"], "KAPANDI")
        self.assertEqual(
            view["kapanan_işlemler"][0]["kapanış_nedeni"],
            "Koruyucu stop çalıştı",
        )


    def test_operator_view_distinguishes_live_feed_from_quiet_symbol_quote(self):
        snapshot = {
            "system": {
                "execution_mode": "PAPER_ONLY", "real_order_allowed": False,
                "open_positions": 0, "live_positions": 0,
                "market_data_status": "POZİSYON_YOK", "worker_healthy": True,
                "worker_status": "CONNECTED", "heartbeat_age_seconds": 1,
                "last_action": "HOLD", "market_feed_fresh": True,
                "observed_symbols": 2, "fresh_market_symbols": 1,
                "market_snapshot_age_seconds": 1.2,
            },
            "portfolio": {
                "initial_capital": 10000, "cash": 10000, "realized_pnl": 0,
                "total_fees": 0, "unrealized_pnl": 0,
            },
            "positions": [],
            "market": [
                {"symbol":"BTCUSDT","market_type":"USD_M_PERPETUAL",
                 "last_price":60000,"best_bid":59999,"best_ask":60001,
                 "quote_source":"BINANCE_USD_M_WS_TICKER_BID_ASK",
                 "freshness_seconds":1.0,"market_status":"CANLI"},
                {"symbol":"UBUSDT","market_type":"USD_M_PERPETUAL",
                 "last_price":0.12,"best_bid":0.119,"best_ask":0.121,
                 "quote_source":"BINANCE_USD_M_WS_TICKER_BID_ASK",
                 "freshness_seconds":20.0,"market_status":"GECİKMİŞ"},
            ],
            "risk": {"open_positions":0,"stale_positions":0,"positions":[]},
            "learning": None,
            "closed_positions": [],
        }
        view = operator_view(snapshot)
        self.assertEqual(view["izlenen_piyasa"], "2 sembol · akış canlı · 1 son quote ≤5 sn")
        self.assertEqual(view["piyasa"][0]["durum"], "CANLI QUOTE")
        self.assertEqual(view["piyasa"][1]["durum"], "SON QUOTE ESKİ")


if __name__ == "__main__":
    unittest.main()
