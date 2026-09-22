"""Binance-only closed-minute input regression; no HTTP, DB, or credentials."""
import unittest

from data.binance_live_evidence import collect, summarize_instrument


NOW = 1_800_000_000_000
SYMBOL = "1000PEPEUSDT"
ROW = {"instrument": SYMBOL, "exchange": "BINANCE_USD_M"}


def wrap(payload, received=NOW, rtt=100):
    return {"payload": payload, "received_ms": received, "round_trip_ms": rtt}


def levels(start, step, ascending):
    return [[str(start + (i*step if ascending else -i*step)), "1000"]
            for i in range(50)]


def sample(symbol=SYMBOL):
    return dict(
        depth=wrap({
            "lastUpdateId": 100, "E": NOW-200, "T": NOW-250,
            "bids": levels(1., .00001, False),
            "asks": levels(1.0001, .00001, True),
        }),
        trades=wrap([
            {"id": 1, "price": "1.00", "qty": "10", "time": NOW-300,
             "isBuyerMaker": False},
            {"id": 2, "price": "1.00", "qty": "5", "time": NOW-200,
             "isBuyerMaker": True},
        ]),
        open_interest=wrap({"symbol": symbol, "openInterest": "100000",
                            "time": NOW-300}),
        funding=wrap({"symbol": symbol, "lastFundingRate": "0.0001",
                      "nextFundingTime": NOW+3600_000, "time": NOW-300}),
    )


class BinanceLiveEvidenceTests(unittest.TestCase):
    def test_native_snapshot_is_eligible_with_same_venue_provenance(self):
        row = summarize_instrument(ROW, **sample())
        self.assertEqual(row["instrument"], SYMBOL)
        self.assertEqual(row["source_exchange"], "BINANCE_USD_M")
        self.assertTrue(row["required_sources_available"])
        self.assertEqual(row["recent_trades"]["buy_notional_usd_estimate"], 10.)
        self.assertEqual(row["recent_trades"]["sell_notional_usd_estimate"], 5.)
        self.assertEqual(row["open_interest"]["base_currency"], 100000.)
        self.assertAlmostEqual(row["order_book"]["depths"]["20"]["imbalance"], 0,
                               delta=0.001)
        self.assertEqual(row["order_book"]["age_ms"], 200)

    def test_stale_and_future_book_are_ineligible(self):
        for timestamp in (NOW-5001, NOW+1):
            with self.subTest(timestamp=timestamp):
                inputs = sample()
                inputs["depth"]["payload"]["E"] = timestamp
                row = summarize_instrument(ROW, **inputs)
                self.assertFalse(row["required_sources_available"])
                self.assertEqual(row["source_states"]["order_book"], "MISSING")

    def test_crossed_or_unsorted_book_rejected(self):
        for modify in ("cross", "sort", "quantity"):
            with self.subTest(modify=modify):
                inputs = sample()
                book = inputs["depth"]["payload"]
                if modify == "cross":
                    book["asks"][0][0] = "0.99"
                elif modify == "sort":
                    book["bids"][1][0] = "1.1"
                else:
                    book["asks"][3][1] = "0"
                row = summarize_instrument(ROW, **inputs)
                self.assertFalse(row["required_sources_available"])

    def test_wrong_oi_or_funding_contract_blocks_eligibility(self):
        for name in ("open_interest", "funding"):
            with self.subTest(name=name):
                inputs = sample()
                inputs[name]["payload"]["symbol"] = "PEPEUSDT"
                row = summarize_instrument(ROW, **inputs)
                self.assertFalse(row["required_sources_available"])
                self.assertEqual(row["source_states"][name], "MISSING")

    def test_stale_trade_or_missing_funding_blocks_eligibility(self):
        inputs = sample()
        inputs["trades"]["payload"][-1]["time"] = NOW - 120_000
        self.assertFalse(summarize_instrument(ROW, **inputs)["required_sources_available"])
        inputs = sample()
        inputs["funding"]["payload"] = None
        self.assertFalse(summarize_instrument(ROW, **inputs)["required_sources_available"])

    def test_collect_rejects_foreign_route_even_if_name_matches(self):
        route = {"deep_stream_instruments": [SYMBOL]}
        with self.assertRaisesRegex(ValueError, "NON_BINANCE"):
            collect(route, {"selected": [{"instrument": SYMBOL, "exchange": "OKX"}]},
                    fetch=lambda *_: self.fail("no network before source validation"))

    def test_collect_only_requests_exact_binance_symbol(self):
        route = {"deep_stream_instruments": [SYMBOL]}
        universe = {"selected": [ROW]}
        payloads = sample()
        paths = {"depth": "depth", "trades": "trades",
                 "openInterest": "open_interest", "premiumIndex": "funding"}
        calls = []

        def fake_fetch(path, params):
            calls.append((path, params["symbol"]))
            return payloads[paths[path]]

        result = collect(route, universe, fetch=fake_fetch)
        self.assertEqual(result["complete_instrument_count"], 1)
        self.assertEqual(len(calls), 4)
        self.assertEqual({symbol for _, symbol in calls}, {SYMBOL})


if __name__ == "__main__":
    unittest.main()
