"""Bounded Binance WS collector pure ingest and native-source routing tests.

No network, SQL, time sleeps, file writes, or execution orders.
"""
import unittest
from datetime import datetime, timezone

from data.binance_ws_market_collector import BinanceWsMinuteCollector


NOW = 1_800_000_000_000
SYMBOL = "1000PEPEUSDT"
FETCHED = datetime.fromtimestamp((NOW-30_000)/1000, timezone.utc).isoformat()
INFO = {"symbols": [{
    "symbol": SYMBOL, "baseAsset": "1000PEPE",
    "contractType": "PERPETUAL", "quoteAsset": "USDT",
    "marginAsset": "USDT", "status": "TRADING",
    "onboardDate": NOW - 30*86400000,
}]}


def book():
    return {"e": "bookTicker", "E": NOW-100, "s": SYMBOL, "st": 1,
            "b": "1.0", "a": "1.0001", "B": "1000", "A": "1000"}


def mini():
    return {"e": "24hrMiniTicker", "E": NOW-300, "s": SYMBOL, "st": 1,
            "o": "1.0", "c": "1.1", "q": "10000000"}


class BinanceWsCollectorTests(unittest.TestCase):
    def collector(self):
        return BinanceWsMinuteCollector(
            INFO, FETCHED, database_url="unused-read-only-dsn",
            clock=lambda: NOW,
            private_oi_fetch=lambda *args: {},
        )

    def test_ingest_requires_authoritative_native_contract_and_market_type(self):
        state = self.collector()
        state.ingest({"id": 1, "result": None}, channel="public")
        state.ingest({"id": 2, "result": None}, channel="market")
        state.ingest(book(), channel="public")
        state.ingest([mini(), {**mini(), "s": "PEPEUSDT"},
                      {**mini(), "s": "BTCUSDT"}], channel="market")
        self.assertTrue(state.ack["public"])
        self.assertTrue(state.ack["market"])
        self.assertEqual(set(state.mini), {SYMBOL})
        self.assertEqual(set(state.books), {SYMBOL})
        got = state.discovery(now_ms=NOW)
        self.assertEqual(got["selected_count"], 1)
        self.assertEqual(got["selected"][0]["instrument"], SYMBOL)
        self.assertEqual(got["received_fresh_ws_pair_count"], 1)

    def test_rejects_wrong_channel_or_age_and_subscription_error(self):
        state = self.collector()
        state.ingest(book(), channel="market")
        state.ingest({**book(), "E": NOW-8000}, channel="public")
        state.ingest({**mini(), "st": 2}, channel="market")
        self.assertFalse(state.mini or state.books)
        state.ingest({"code": 400, "msg": "error"}, channel="public")
        self.assertEqual(state.errors, ["public:SUBSCRIPTION_ERROR"])

    def test_live_route_requires_confirmed_deep_subscriptions(self):
        state = self.collector()
        route = {"deep_stream_instruments": [SYMBOL]}
        self.assertEqual(state.snapshot(route, {}, at_ms=NOW)["instrument_count"], 0)
        state.ack["deep_public"] = True
        state.ack["deep_market"] = True
        state.deep = frozenset({SYMBOL})
        state.ingest({"e": "aggTrade", "E": NOW-200, "s": SYMBOL,
                      "a": 1, "T": NOW-210, "p": "1", "q": "1", "m": False},
                     channel="market")
        state.ingest({"e": "aggTrade", "E": NOW-200, "s": "PEPEUSDT",
                      "a": 2, "T": NOW-210, "p": "1", "q": "1", "m": False},
                     channel="market")
        result = state.snapshot(route, {}, at_ms=NOW)
        self.assertEqual(result["instrument_count"], 1)
        self.assertFalse(result["instruments"][0]["required_sources_available"])
        self.assertEqual(result["instruments"][0]["open_interest"]["usd"], None)
        self.assertEqual(state.received["trades"], 1)

    def test_cache_bounded_to_authoritative_inventory(self):
        state = self.collector()
        for i in range(1500):
            state.ingest({**book(), "s": f"{i}USDT"}, channel="public")
        self.assertFalse(state.books)
        state.ingest(book(), channel="public")
        self.assertEqual(len(state.books), 1)


if __name__ == "__main__":
    unittest.main()
