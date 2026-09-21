"""Pure Binance WS evidence: exact native frames, true missing-OI semantics."""
import unittest

from data.binance_ws_live_evidence import summarize_ws_instrument
from data.live_behavior_evidence_bundle import build_bundle
from data.rolling_behavior_window import aggregate_closed_minute


NOW = 1_800_000_000_000
SYMBOL = "1000PEPEUSDT"
ROUTE = {"instrument": SYMBOL, "exchange": "BINANCE_USD_M",
         "market_type": "USD_M_PERPETUAL"}


def levels(start, step, descending):
    return [[str(start - i*step if descending else start + i*step), "100"]
            for i in range(20)]


def inputs(t=NOW, sym=SYMBOL):
    return {
        "depth": {"e": "depthUpdate", "E": t-200, "T": t-220,
                  "s": sym, "st": 1, "u": 123,
                  "b": levels(1., .00001, True),
                  "a": levels(1.0001, .00001, False)},
        "trades": [
            {"e": "aggTrade", "E": t-300, "T": t-350,
             "s": sym, "a": 100, "p": "1.0", "q": "10", "m": False},
            {"e": "aggTrade", "E": t-200, "T": t-230,
             "s": sym, "a": 101, "p": "1.0", "q": "5", "m": True},
        ],
        "mark": {"e": "markPriceUpdate", "E": t-400, "s": sym,
                 "r": "0.0001", "T": t+3600_000},
        "oi": {"symbol": sym, "source_exchange": "BINANCE_USD_M",
               "open_interest_native": 10000.,
               "exchange_timestamp_ms": t-500},
        "received_ms": t,
    }


class BinanceWsLiveEvidenceTests(unittest.TestCase):
    def test_native_ws_and_private_oi_produce_same_minute_contract(self):
        result = summarize_ws_instrument(ROUTE, **inputs())
        self.assertTrue(result["required_sources_available"])
        self.assertEqual(result["instrument"], SYMBOL)
        self.assertEqual(result["order_book"]["coverage"], "ACTUAL_20_LEVEL_PUBLIC_WS")
        self.assertIn("20", result["order_book"]["depths"])
        self.assertNotIn("50", result["order_book"]["depths"])
        self.assertEqual(result["recent_trades"]["buy_notional_usd_estimate"], 10.)
        self.assertEqual(result["recent_trades"]["sell_notional_usd_estimate"], 5.)
        self.assertGreater(result["open_interest"]["usd"], 0)
        self.assertEqual(result["funding"]["rate"], .0001)

    def test_missing_oi_is_null_in_closed_report_not_a_zero_flow(self):
        snapshots = []
        end = NOW + 60_000
        for offset in (50_000, 40_000, 30_000, 20_000):
            t = end - offset
            state = inputs(t)
            state["oi"] = None
            snapshots.append(summarize_ws_instrument(ROUTE, **state))
        bundles = [build_bundle({"instruments": [state]},
            {"state": "AVAILABLE_NO_EVENT_IN_WINDOW", "events": []})
            for state in snapshots]
        report = aggregate_closed_minute(bundles, end)
        self.assertEqual(report["eligible_row_count"], 0)
        self.assertIsNone(report["rows"][0]["open_interest_usd_first"])
        self.assertIsNone(report["rows"][0]["open_interest_usd_change"])
        self.assertFalse(report["rows"][0]["quality_gates"]["required_sources"])

    def test_instrument_mismatch_stale_depth_and_duplicate_trades(self):
        for key, mutate in (
            ("depth", lambda d: d.update(s="PEPEUSDT")),
            ("depth", lambda d: d.update(E=NOW-5001)),
            ("mark", lambda d: d.update(r="nan")),
            ("oi", lambda d: d.update(symbol="PEPEUSDT")),
        ):
            with self.subTest(key=key):
                args = inputs()
                mutate(args[key])
                got = summarize_ws_instrument(ROUTE, **args)
                self.assertFalse(got["required_sources_available"])
        args = inputs()
        args["trades"][1]["a"] = args["trades"][0]["a"]
        got = summarize_ws_instrument(ROUTE, **args)
        self.assertEqual(got["source_states"]["recent_trades"], "MISSING")

    def test_foreign_venue_route_cannot_authorise_native_oi(self):
        with self.assertRaisesRegex(ValueError, "NON_BINANCE"):
            summarize_ws_instrument({**ROUTE, "exchange": "OKX"}, **inputs())


if __name__ == "__main__":
    unittest.main()
