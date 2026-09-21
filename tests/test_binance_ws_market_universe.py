"""WebSocket-first native universe: exact metadata IDs and incremental freshness."""
import unittest
from datetime import datetime, timezone

from data.binance_ws_market_universe import from_ws_caches


NOW = 1_800_000_000_000
FETCHED = datetime.fromtimestamp((NOW - 30_000)/1000, timezone.utc).isoformat()


def contract(symbol, **other):
    row = {
        "symbol": symbol, "baseAsset": symbol[:-4], "quoteAsset": "USDT",
        "marginAsset": "USDT", "status": "TRADING",
        "contractType": "PERPETUAL", "onboardDate": NOW-86_400_000*10,
    }
    row.update(other)
    return row


def mini(symbol, **other):
    row = {"e": "24hrMiniTicker", "E": NOW-200, "s": symbol, "st": 1,
           "o": "1.0", "c": "1.1", "q": "10000000"}
    row.update(other)
    return row


def book(symbol, **other):
    row = {"e": "bookTicker", "E": NOW-100, "s": symbol, "st": 1,
           "b": "1.0", "a": "1.0001", "B": "5000", "A": "5000"}
    row.update(other)
    return row


class BinanceWsNativeUniverseTests(unittest.TestCase):
    def build(self, *, symbols=None, minis=None, books=None, fetched=FETCHED, now_ms=NOW):
        symbol = "1000PEPEUSDT"
        return from_ws_caches(
            {"symbols": [contract(symbol)] if symbols is None else symbols},
            fetched,
            {symbol: mini(symbol)} if minis is None else minis,
            {symbol: book(symbol)} if books is None else books,
            now_ms=now_ms,
        )

    def test_binance_native_contract_without_cross_exchange_alias(self):
        report = self.build()
        self.assertEqual(report["selected_count"], 1)
        row = report["selected"][0]
        self.assertEqual(row["instrument"], "1000PEPEUSDT")
        self.assertEqual(row["exchange"], "BINANCE_USD_M")
        self.assertEqual(report["received_fresh_ws_pair_count"], 1)
        self.assertEqual(
            report["selection_contract"]["all_market_coverage"],
            "FRESH_UPDATES_ONLY_NOT_FULL_SNAPSHOT",
        )

    def test_probed_external_and_delisted_symbols_cannot_enter(self):
        other = "NVDAUSDT"
        report = self.build(
            symbols=[contract("1000PEPEUSDT"), contract(other, status="BREAK")],
            minis={other: mini(other)}, books={other: book(other)},
        )
        self.assertEqual(report["selected_count"], 0)

    def test_stale_partial_update_is_not_substituted(self):
        sym = "1000PEPEUSDT"
        for minis, books in (
            ({sym: mini(sym, E=NOW-60_001)}, {sym: book(sym)}),
            ({sym: mini(sym)}, {sym: book(sym, E=NOW-5_001)}),
            ({sym: mini(sym)}, {sym: book(sym, st=2)}),
            ({sym: mini(sym, st=2)}, {sym: book(sym)}),
            ({sym: mini("PEPEUSDT")}, {sym: book(sym)}),
        ):
            with self.subTest(minis=minis, books=books):
                report = self.build(minis=minis, books=books)
                self.assertEqual(report["selected_count"], 0)
                self.assertEqual(report["received_fresh_ws_pair_count"], 0)

    def test_cache_timestamp_fails_closed(self):
        stale = datetime.fromtimestamp((NOW-7_200_001)/1000, timezone.utc).isoformat()
        with self.assertRaisesRegex(ValueError, "STALE_EXCHANGE_INFO_CACHE"):
            self.build(fetched=stale)
        with self.assertRaisesRegex(ValueError, "STALE_EXCHANGE_INFO_CACHE"):
            self.build(fetched=stale, now_ms=NOW + 10000)

    def test_missing_unchanged_symbol_yields_no_candidate(self):
        self.assertEqual(self.build(minis={}, books={})["selected_count"], 0)

    def test_legacy_absent_market_discriminator_retains_exact_contract_gate(self):
        sym = "1000PEPEUSDT"
        a, b = mini(sym), book(sym)
        del a["st"]
        del b["st"]
        report = self.build(minis={sym: a}, books={sym: b})
        self.assertEqual(report["selected"][0]["instrument"], sym)


if __name__ == "__main__":
    unittest.main()
