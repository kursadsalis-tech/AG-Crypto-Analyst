"""Regression tests for exact Binance contract identity and fresh native quotes."""
import unittest

from data.binance_market_universe import build_universe


NOW = 1_800_000_000_000


def meta(symbol="1000PEPEUSDT", **extra):
    row = {
        "symbol": symbol, "status": "TRADING", "contractType": "PERPETUAL",
        "quoteAsset": "USDT", "marginAsset": "USDT",
        "baseAsset": symbol[:-4], "onboardDate": NOW - 100 * 86_400_000,
    }
    row.update(extra)
    return row


def ticker(symbol="1000PEPEUSDT"):
    return {
        "symbol": symbol, "lastPrice": "0.02", "quoteVolume": "10000000",
        "priceChangePercent": "3.5", "closeTime": NOW - 1000,
    }


def book(symbol="1000PEPEUSDT"):
    return {
        "symbol": symbol, "bidPrice": "0.02000", "bidQty": "100000",
        "askPrice": "0.02001", "askQty": "100000", "time": NOW - 100,
    }


class BinanceMarketUniverseTests(unittest.TestCase):
    def build(self, info=None, tickers=None, books=None, **kwargs):
        return build_universe(
            {"symbols": [meta()] } if info is None else info,
            [ticker()] if tickers is None else tickers,
            [book()] if books is None else books,
            snapshot_ms=NOW, **kwargs,
        )

    def test_exact_native_contract_never_converts_1000_prefix(self):
        report = self.build()
        self.assertEqual(report["selected_count"], 1)
        row = report["selected"][0]
        self.assertEqual(row["instrument"], "1000PEPEUSDT")
        self.assertEqual(row["market_type"], "USD_M_PERPETUAL")
        self.assertEqual(row["exchange"], "BINANCE_USD_M")
        self.assertEqual(report["selection_contract"]["symbol_authority"],
                         "BINANCE_EXCHANGE_INFO_EXACT_SYMBOL")

    def test_legacy_or_other_exchange_alias_is_never_inferred(self):
        report = self.build(
            info={"symbols": [meta()]}, tickers=[ticker("PEPEUSDT")],
            books=[book("PEPEUSDT")],
        )
        self.assertEqual(report["selected"], [])
        self.assertEqual(report["excluded_counts"]["MISSING_SAME_SYMBOL_QUOTES"], 1)

    def test_wrong_contract_type_quote_margin_and_delist_excluded(self):
        report = self.build(
            info={"symbols": [
                meta("AUSDT", status="BREAK"),
                meta("BUSDT", contractType="CURRENT_QUARTER"),
                meta("CUSDC", quoteAsset="USDC"),
                meta("DUSDT", marginAsset="USDC"),
                meta("EUSDT", quoteAsset="USDT"),
            ]},
            tickers=[ticker("EUSDT")],
            books=[book("EUSDT")],
        )
        self.assertEqual([x["instrument"] for x in report["selected"]], ["EUSDT"])
        self.assertEqual(report["excluded_counts"]["NOT_TRADING_USDT_PERPETUAL"], 4)

    def test_stale_or_future_book_and_ticker_fail_closed(self):
        for changed in (
            {"books": [dict(book(), time=NOW - 5001)]},
            {"books": [dict(book(), time=NOW + 1)]},
            {"tickers": [dict(ticker(), closeTime=NOW - 60001)]},
        ):
            with self.subTest(changed=changed):
                result = self.build(**changed)
                self.assertEqual(result["selected_count"], 0)
                self.assertEqual(result["excluded_counts"]["STALE_OR_FUTURE_QUOTE"], 1)

    def test_zero_crossed_or_unavailable_quotes_rejected(self):
        for change in ({"bidPrice": "0"}, {"askPrice": "0.01"},
                       {"bidQty": "0"}, {"bidPrice": "NaN"}):
            with self.subTest(change=change):
                result = self.build(books=[dict(book(), **change)])
                self.assertEqual(result["selected_count"], 0)

    def test_book_market_type_mismatch_is_rejected(self):
        report = self.build(books=[dict(book(), st=2)])
        self.assertEqual(report["excluded_counts"]["WRONG_MARKET_TYPE"], 1)

    def test_listing_age_is_configurable_no_coin_whitelist(self):
        recent = meta("NEWUSDT", onboardDate=NOW - 86_400_000)
        result = self.build(
            info={"symbols": [recent]}, tickers=[ticker("NEWUSDT")],
            books=[book("NEWUSDT")],
        )
        self.assertEqual([x["instrument"] for x in result["selected"]], ["NEWUSDT"])
        aged = self.build(
            info={"symbols": [recent]}, tickers=[ticker("NEWUSDT")],
            books=[book("NEWUSDT")], min_listing_days=90,
        )
        self.assertEqual(aged["selected_count"], 0)

    def test_invalid_market_wide_payload_fails_entire_universe(self):
        with self.assertRaises(ValueError):
            self.build(info={"symbols": None})
        with self.assertRaises(ValueError):
            self.build(books={})
        with self.assertRaises(ValueError):
            self.build(tickers={})


if __name__ == "__main__":
    unittest.main()
