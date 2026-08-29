import csv
import tempfile
import unittest
from pathlib import Path

from data import okx_l2_feature_dataset as dataset
from data.okx_l2_feature_extractor import VERSION


class OkxL2FeatureDatasetTests(unittest.TestCase):
    def source(self, root, name, boundary):
        path = Path(root) / name
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle, fieldnames=("version", "instrument", "boundary_time")
            )
            writer.writeheader()
            writer.writerow({
                "version": VERSION, "instrument": "LQTY-USDT-SWAP",
                "boundary_time": boundary,
            })
        return path

    def test_combines_and_sorts_unique_causal_boundaries(self):
        with tempfile.TemporaryDirectory() as root:
            later = self.source(root, "later.csv", "2026-01-02T00:00:00+00:00")
            earlier = self.source(root, "earlier.csv", "2026-01-01T00:00:00+00:00")
            fields, rows, sources, instruments = dataset.combine([later, earlier])
        self.assertEqual(rows[0]["boundary_time"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(len(sources), 2)
        self.assertEqual(instruments, ["LQTY-USDT-SWAP"])
        self.assertEqual(fields, ("version", "instrument", "boundary_time"))

    def test_duplicate_boundary_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            first = self.source(root, "one.csv", "2026-01-01T00:00:00+00:00")
            second = self.source(root, "two.csv", "2026-01-01T00:00:00+00:00")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                dataset.combine([first, second])


if __name__ == "__main__":
    unittest.main()
