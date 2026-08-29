import unittest
from unittest.mock import patch

from data.okx_live_evidence import collect, summarize_instrument


class OkxLiveEvidenceTests(unittest.TestCase):
    def test_compacts_raw_payloads_into_causal_evidence(self):
        result = summarize_instrument(
            {"instrument": "AAA-USDT-SWAP", "contract_value": 1},
            {"code": "0", "data": [{"ts": "1000", "bids": [["10", "2", "0", "1"]],
                                       "asks": [["11", "1", "0", "1"]]}]},
            {"code": "0", "data": [{"ts": "990", "px": "10", "sz": "2", "side": "buy"},
                                       {"ts": "995", "px": "11", "sz": "1", "side": "sell"}]},
            {"code": "0", "data": [{"ts": "998", "oi": "3", "oiCcy": "3", "oiUsd": "30"}]},
            {"code": "0", "data": [{"fundingRate": "0.0001", "fundingTime": "2000"}]},
            collected_ms=1100,
        )
        self.assertTrue(result["required_sources_available"])
        self.assertAlmostEqual(result["order_book"]["depths"]["5"]["imbalance"], 9 / 31)
        self.assertAlmostEqual(result["recent_trades"]["taker_imbalance"], 9 / 31)
        self.assertEqual(result["source_states"]["liquidations"], "NOT_IN_REST_CONTRACT")
        self.assertFalse(result["trade_signal_generated"])

    def test_missing_source_fails_evidence_completeness(self):
        empty = {"code": "0", "data": []}
        result = summarize_instrument(
            {"instrument": "AAA-USDT-SWAP", "contract_value": 1},
            empty, empty, empty, empty, collected_ms=1,
        )
        self.assertFalse(result["required_sources_available"])

    @patch("data.okx_live_evidence.time.time", return_value=2.0)
    def test_collection_timestamp_is_measured_after_fetch(self, _clock):
        book = {"code": "0", "data": [{"ts": "1900", "bids": [["10", "1"]],
                                         "asks": [["11", "1"]]}]}
        trade = {"code": "0", "data": [{"ts": "1900", "px": "10", "sz": "1", "side": "buy"}]}
        oi = {"code": "0", "data": [{"ts": "1900", "oi": "1"}]}
        funding = {"code": "0", "data": [{"fundingRate": "0", "fundingTime": "3000"}]}
        payloads = {"market/books": book, "market/trades": trade,
                    "public/open-interest": oi, "public/funding-rate": funding}
        result = collect(
            {"deep_stream_instruments": ["AAA-USDT-SWAP"]},
            {"selected": [{"instrument": "AAA-USDT-SWAP", "contract_value": 1}]},
            workers=1, fetch=lambda path, _params: payloads[path],
        )
        self.assertEqual(result["instruments"][0]["order_book"]["age_ms"], 100)


if __name__ == "__main__":
    unittest.main()
