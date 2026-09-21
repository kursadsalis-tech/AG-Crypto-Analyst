"""Safety checks for the independent, read-only futures feed probe."""
import unittest

from scripts.probe_realtime_quote_feeds import sample_shape


class ProbeFeedShapeTests(unittest.TestCase):
    def test_futures_ticker_requires_bid_ask_prices_and_quantities(self):
        payload = {
            "e": "24hrTicker", "s": "BTCUSDT", "E": 1700000000000,
            "b": "100", "B": "3", "a": "101", "A": "4",
        }
        valid = sample_shape(payload)
        self.assertTrue(valid["positive_both_sides"])
        self.assertTrue(valid["snapshot"])
        self.assertEqual(valid["has_fields"], {
            "b": True, "B": True, "a": True, "A": True,
        })
        invalid = sample_shape({key: value for key, value in payload.items()
                                if key != "B"})
        self.assertFalse(invalid["positive_both_sides"])
        self.assertFalse(invalid["snapshot"])

    def test_incremental_depth_must_never_be_executable_snapshot(self):
        diff = {
            "e": "depthUpdate", "s": "ETHUSDT",
            "U": 1, "u": 2, "pu": 0,
            "b": [["100", "3"]], "a": [["101", "4"]],
        }
        shape = sample_shape(diff)
        self.assertTrue(shape["positive_both_sides"])
        self.assertFalse(shape["snapshot"])
        self.assertIsNone(sample_shape({**diff, "s": "UNWATCHEDUSDT"}))

    def test_explicit_complete_book_uses_first_positive_bid_ask(self):
        book = {
            "e": "depthUpdate", "s": "BTCUSDT",
            "bids": [["100", "3"]], "asks": [["101", "4"]],
        }
        self.assertTrue(sample_shape(book)["snapshot"])
        book["asks"][0][1] = "0"
        self.assertFalse(sample_shape(book)["snapshot"])


if __name__ == "__main__":
    unittest.main()
