import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from data import okx_l2_archive_audit as audit


class OkxL2ArchiveAuditTests(unittest.TestCase):
    def make_archive(self, rows):
        root = tempfile.TemporaryDirectory()
        path = Path(root.name) / "sample.tar.gz"
        content = b"".join(json.dumps(row).encode() + b"\n" for row in rows)
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("sample.data")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        return root, path

    def row(self, timestamp, action="update", levels=1):
        side = [[str(index + 1), "1.0", "1"] for index in range(levels)]
        return {
            "instId": "BTC-USDT-SWAP", "action": action,
            "ts": str(timestamp), "asks": side, "bids": side,
        }

    def test_small_fixture_fails_only_volume_and_span_gates(self):
        rows = [self.row(1, "snapshot", 400), self.row(2)]
        root, path = self.make_archive(rows)
        self.addCleanup(root.cleanup)
        result = audit.audit_archive(path, "BTC-USDT-SWAP")
        self.assertTrue(result["quality_gates"]["first_event_is_snapshot"])
        self.assertTrue(result["quality_gates"]["snapshot_has_400_ask_levels"])
        self.assertTrue(result["quality_gates"]["all_rows_schema_valid"])
        self.assertFalse(result["schema_and_sequence_ready"])
        self.assertFalse(result["ready_for_historical_model_test"])

    def test_backwards_timestamp_fails_closed(self):
        rows = [self.row(2, "snapshot", 400), self.row(1)]
        root, path = self.make_archive(rows)
        self.addCleanup(root.cleanup)
        result = audit.audit_archive(path, "BTC-USDT-SWAP")
        self.assertEqual(result["backwards_timestamps"], 1)
        self.assertFalse(
            result["quality_gates"]["timestamps_never_go_backwards"]
        )


if __name__ == "__main__":
    unittest.main()
