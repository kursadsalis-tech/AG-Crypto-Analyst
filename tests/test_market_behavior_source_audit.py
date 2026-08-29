import csv
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from data import market_behavior_source_audit as audit


class MarketBehaviorSourceAuditTests(unittest.TestCase):
    def fixture(self, broken=False):
        root = tempfile.TemporaryDirectory()
        archive = Path(root.name) / "sample.zip"
        checksum = Path(root.name) / "sample.zip.CHECKSUM"
        csv_path = Path(root.name) / "sample.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(audit.EXPECTED_COLUMNS)
            for sample in range(2800):
                minute, second = divmod(sample * 30, 60)
                hour, minute = divmod(minute, 60)
                timestamp = f"2025-01-01 {hour:02d}:{minute:02d}:{second:02d}"
                for band in audit.EXPECTED_BANDS:
                    distance = abs(band)
                    depth = float(distance * 10)
                    if broken and sample == 0 and band == -2:
                        depth = 1.0
                    writer.writerow((timestamp, band, depth, distance * 100.0))
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            output.write(csv_path, arcname="sample.csv")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        checksum.write_text(f"{digest}  sample.zip\n", encoding="utf-8")
        return root, archive, checksum

    def test_valid_percentage_band_archive_passes_without_claiming_full_l2(self):
        root, archive, checksum = self.fixture()
        self.addCleanup(root.cleanup)
        result = audit.audit_book_depth(
            archive, checksum, "https://example.test", 404,
            {"BTCUSDT": 200, "MISSINGUSDT": 404},
        )
        self.assertTrue(result["book_depth_ready_for_feature_feasibility"])
        self.assertFalse(result["full_l2_order_book_available"])
        self.assertFalse(result["historical_liquidations_available"])
        self.assertFalse(result["cohort_ready_for_historical_model_test"])
        self.assertFalse(result["model_or_policy_changed"])
        self.assertEqual(result["symbol_coverage_probe"]["available_count"], 1)
        self.assertEqual(result["symbol_coverage_probe"]["coverage_ratio"], 0.5)

    def test_non_monotonic_depth_fails_closed(self):
        root, archive, checksum = self.fixture(broken=True)
        self.addCleanup(root.cleanup)
        result = audit.audit_book_depth(archive, checksum, "https://example.test", 200)
        self.assertFalse(result["quality_gates"]["all_depth_bands_are_monotonic"])
        self.assertFalse(result["book_depth_ready_for_feature_feasibility"])

    def test_checksum_mismatch_fails_closed(self):
        root, archive, checksum = self.fixture()
        self.addCleanup(root.cleanup)
        checksum.write_text("0" * 64 + "  sample.zip\n", encoding="utf-8")
        result = audit.audit_book_depth(archive, checksum, "https://example.test")
        self.assertFalse(result["quality_gates"]["checksum_matches"])
        self.assertFalse(result["book_depth_ready_for_feature_feasibility"])


if __name__ == "__main__":
    unittest.main()
