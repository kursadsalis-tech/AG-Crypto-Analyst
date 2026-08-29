import unittest

from data.bounded_archive_plan import build_plan


class BoundedArchivePlanTests(unittest.TestCase):
    def test_rotates_roles_without_using_size(self):
        rows = []
        for date in ("2026-01-01", "2026-02-01"):
            for role, symbols in (("CORE", ("A", "B", "C")),
                                  ("EXPLORATION", ("X", "Y", "Z"))):
                for index, symbol in enumerate(symbols):
                    rows.append({"date": date, "instrument": symbol,
                                 "liquidity_role": role, "content_length_bytes": 1000-index})
        result = build_plan({"available": rows}, per_date_per_role=1)
        self.assertEqual(result["selected_count"], 4)
        self.assertEqual([row["instrument"] for row in result["selected"]], ["A", "X", "B", "Y"])
        self.assertFalse(result["selection_contract"]["uses_archive_file_size_for_selection"])


if __name__ == "__main__":
    unittest.main()
