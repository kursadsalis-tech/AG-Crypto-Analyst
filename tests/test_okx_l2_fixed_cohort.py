import csv
import tempfile
import unittest
from pathlib import Path

from data import okx_l2_fixed_cohort as cohort
from data.okx_l2_feature_extractor import VERSION as FEATURE_VERSION


class OkxL2FixedCohortTests(unittest.TestCase):
    def source(self, root, instrument, boundaries):
        path = Path(root) / f"{instrument}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=("version", "instrument", "boundary_time")
            )
            writer.writeheader()
            for boundary in boundaries:
                writer.writerow({
                    "version": FEATURE_VERSION, "instrument": instrument,
                    "boundary_time": boundary,
                })
        return path

    def test_boundary_mismatch_fails_gate(self):
        names = [f"C{index}" for index in range(5)]
        with tempfile.TemporaryDirectory() as root:
            paths = [self.source(root, name, ["a", "b"]) for name in names]
            paths[-1] = self.source(root, names[-1], ["a", "c"])
            result = cohort.build(
                paths, Path(root) / "out.csv", Path(root) / "manifest.json", names
            )
        self.assertFalse(
            result["quality_gates"]["all_instruments_share_same_boundaries"]
        )
        self.assertFalse(result["fixed_cohort_feature_research_ready"])

    def test_instrument_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.source(root, "A", ["a"])
            with self.assertRaisesRegex(ValueError, "cohort mismatch"):
                cohort.build(
                    [path], Path(root) / "out.csv", Path(root) / "manifest.json",
                    ["B"],
                )


if __name__ == "__main__":
    unittest.main()
