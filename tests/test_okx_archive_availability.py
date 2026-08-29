import unittest

from data.okx_archive_availability import audit_availability


class OkxArchiveAvailabilityTests(unittest.TestCase):
    def test_separates_available_missing_and_errors(self):
        curriculum = {"version": "TEST", "archive_candidates": [
            {"url": "https://example/ok", "instrument": "A", "date": "2026-01-01"},
            {"url": "https://example/missing", "instrument": "B", "date": "2026-01-01"},
            {"url": "https://example/error", "instrument": "C", "date": "2026-01-01"},
        ]}
        answers = {
            "https://example/ok": {"status": 200, "content_length_bytes": 123},
            "https://example/missing": {"status": 404, "content_length_bytes": 0},
            "https://example/error": {"status": 0, "content_length_bytes": 0, "error": "timeout"},
        }
        result = audit_availability(curriculum, probe=lambda url: answers[url], workers=2)
        self.assertEqual(result["available_count"], 1)
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["error_count"], 1)
        self.assertEqual(result["available_compressed_bytes"], 123)
        self.assertFalse(result["bulk_archives_downloaded"])
        self.assertFalse(result["ready_for_bounded_download"])


if __name__ == "__main__":
    unittest.main()
