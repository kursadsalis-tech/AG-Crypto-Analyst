import json
import sqlite3
import unittest
from datetime import datetime, timezone

from reports import investment_committee_report as report
from whale_tracker import ethereum_token_whale_collector as token_whales


class ReportEvidenceCoverageTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.executescript("""
            CREATE TABLE data_validation_audit(
              id INTEGER PRIMARY KEY, gate_name TEXT, source TEXT,
              status TEXT, checked_at TEXT, detail_json TEXT
            );
            CREATE TABLE whale_events(
              asset TEXT, direction TEXT, exchange_flow TEXT,
              usd_estimate REAL, block_time TEXT
            );
        """)
        self.now = datetime(2026, 8, 27, 12, tzinfo=timezone.utc)
        for source in ("onchain", "whale_tracker"):
            self.con.execute(
                "INSERT INTO data_validation_audit VALUES(NULL,'SOURCE_CYCLE',?,'PASS',?,?)",
                (source, self.now.isoformat(), json.dumps({"cycle_status": "SUCCESS"})),
            )

    def tearDown(self):
        self.con.close()

    @staticmethod
    def decision(symbol, coin, onchain_state="NOT_APPLICABLE"):
        return {
            "symbol": symbol,
            "coin": coin,
            "evidence": {"onchain": {"state": onchain_state}},
        }

    def test_distinguishes_unknown_direction_no_event_and_unsupported(self):
        self.con.execute(
            "INSERT INTO whale_events VALUES('BTC','UNKNOWN','UNVERIFIED',12000000,?)",
            (self.now.isoformat(),),
        )
        result = report.supporting_evidence_coverage(
            self.con,
            [
                self.decision("BTCUSDT", "BTC", "OBSERVATION_ONLY"),
                self.decision("ETHFIUSDT", "ETHFI"),
                self.decision("FIDAUSDT", "FIDA"),
            ],
            self.now,
        )
        rows = {row["symbol"]: row for row in result["rows"]}
        self.assertEqual(rows["BTCUSDT"]["whale_state"], "OLAY_VAR_YON_DOGRULANMADI")
        self.assertEqual(rows["BTCUSDT"]["whale_direction"], "UNKNOWN")
        self.assertEqual(rows["ETHFIUSDT"]["whale_state"], "KAPSAMDA_OLAY_YOK")
        self.assertEqual(rows["FIDAUSDT"]["whale_state"], "KAPSAM_DISI")
        self.assertEqual(rows["FIDAUSDT"]["onchain_state"], "KAPSAM_DISI_TOKEN_SEVIYESI")

    def test_stale_source_is_the_only_data_missing_case(self):
        self.con.execute(
            "UPDATE data_validation_audit SET checked_at='2026-08-27T09:00:00+00:00'"
        )
        result = report.supporting_evidence_coverage(
            self.con, [self.decision("BTCUSDT", "BTC")], self.now
        )
        row = result["rows"][0]
        self.assertEqual(row["onchain_state"], "VERI_EKSIK")
        self.assertEqual(row["whale_state"], "VERI_EKSIK")

    def test_erc20_scanner_has_bounded_backlog_catchup(self):
        heights = token_whales.heights_to_scan(100, 10_000)
        self.assertEqual(len(heights), token_whales.MAX_BLOCKS_PER_RUN)
        self.assertEqual(heights[0], 101)
        self.assertEqual(heights[-1], 2_100)
        ranges = token_whales.chunk_ranges(heights)
        self.assertEqual(ranges[0], (101, 600))
        self.assertEqual(ranges[-1], (1_601, 2_100))


if __name__ == "__main__":
    unittest.main()
