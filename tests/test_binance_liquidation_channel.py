"""Binance liquidation stream is sparse, same-venue and fails closed."""
import unittest

from data.binance_liquidation_channel import normalize_event, classify


NOW = 1_800_000_000_000


def event(symbol="1000PEPEUSDT"):
    return {
        "e": "forceOrder", "E": NOW-200, "st": 1, "ps": symbol,
        "o": {"s": symbol, "T": NOW-250, "S": "SELL",
              "ap": "0.02", "q": "900", "l": "100", "X": "FILLED"},
    }


class BinanceLiquidationTests(unittest.TestCase):
    def test_native_contract_and_actual_filled_quantity(self):
        row = normalize_event(event(), NOW)
        self.assertEqual(row["instrument"], "1000PEPEUSDT")
        self.assertEqual(row["liquidated_side"], "LONG")
        self.assertEqual(row["notional_usd_estimate"], 2)
        self.assertEqual(row["coverage"], "LATEST_PER_SYMBOL_PER_1000MS_NOT_EXHAUSTIVE")

    def test_unfilled_stale_foreign_and_wrong_market_rejected(self):
        for update in (
            lambda e: e["o"].update(l="0"),
            lambda e: e.update(E=NOW-3000),
            lambda e: e.update(st=2),
            lambda e: e.update(ps="PEPEUSDT"),
            lambda e: e["o"].update(s="BTCUSD"),
            lambda e: e["o"].update(T=NOW+1),
        ):
            with self.subTest(update=str(update)):
                payload = event()
                update(payload)
                self.assertIsNone(normalize_event(payload, NOW))

    def test_subscribed_silence_is_available_not_proof_of_no_liquidations(self):
        result = classify(subscribed=True, errors=[], events=[])
        self.assertEqual(result["state"], "AVAILABLE_NO_EVENT_IN_WINDOW")
        self.assertEqual(classify(subscribed=False, errors=[], events=[])["state"],
                         "SUBSCRIPTION_NOT_CONFIRMED")
        self.assertEqual(classify(subscribed=True, errors=[], events=[], overflow=True)["state"],
                         "CHANNEL_OVERFLOW")


if __name__ == "__main__":
    unittest.main()
