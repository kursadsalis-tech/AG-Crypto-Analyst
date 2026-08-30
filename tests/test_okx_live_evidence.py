import unittest
from unittest.mock import patch

from data.okx_live_evidence import collect, summarize_instrument
from data.rolling_behavior_window import aggregate_closed_minute


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

    def test_one_transport_exception_isolated_to_affected_instrument(self):
        good = {"code": "0", "data": [{"ts": "1", "bids": [["10", "1"]],
                                           "asks": [["11", "1"]]}]}
        trade = {"code": "0", "data": [{"ts": "1", "px": "10", "sz": "1", "side": "buy"}]}
        oi = {"code": "0", "data": [{"ts": "1", "oi": "1"}]}
        funding = {"code": "0", "data": [{"fundingRate": "0", "fundingTime": "2"}]}

        def fetch(path, params):
            if params["instId"] == "BAD-USDT-SWAP" and path == "market/books":
                raise TimeoutError("isolated")
            return {"market/books": good, "market/trades": trade,
                    "public/open-interest": oi, "public/funding-rate": funding}[path]

        result = collect(
            {"deep_stream_instruments": ["GOOD-USDT-SWAP", "BAD-USDT-SWAP"]},
            {"selected": [
                {"instrument": "GOOD-USDT-SWAP", "contract_value": 1},
                {"instrument": "BAD-USDT-SWAP", "contract_value": 1},
            ]}, workers=8, fetch=fetch,
        )
        self.assertEqual(result["instrument_count"], 2)
        self.assertTrue(result["instruments"][0]["required_sources_available"])
        self.assertFalse(result["instruments"][1]["required_sources_available"])

        bundles = [{
            "liquidation_channel_healthy": True,
            "instruments": result["instruments"],
        }] * 4
        for index, bundle in enumerate(bundles):
            for row in bundle["instruments"]:
                row["collected_timestamp_ms"] = 1_000 + index * 10_000
        minute = aggregate_closed_minute(bundles, 60_000)
        bad = next(row for row in minute["rows"] if row["instrument"] == "BAD-USDT-SWAP")
        self.assertFalse(bad["eligible_for_learning"])
        self.assertEqual(bad["spread_bps_mean"], 0.0)


if __name__ == "__main__":
    unittest.main()
