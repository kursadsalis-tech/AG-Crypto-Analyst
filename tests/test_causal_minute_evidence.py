import unittest
from datetime import datetime, timezone

from data.causal_minute_evidence import events_for_closed_minute, normalize_event


class CausalMinuteEvidenceTests(unittest.TestCase):
    def test_all_evidence_kinds_share_available_at_minute(self):
        events = []
        for index, kind in enumerate(("NEWS", "MACRO", "ONCHAIN", "WHALE")):
            events.append({
                "event_id": str(index), "kind": kind, "source": "official",
                "occurred_at": "2026-08-30T09:58:00+00:00",
                "available_at": f"2026-08-30T10:00:{10 + index:02d}+00:00",
            })
        end = int(datetime(2026, 8, 30, 10, 1, tzinfo=timezone.utc).timestamp() * 1000)
        result = events_for_closed_minute(
            events, end, now=datetime(2026, 8, 30, 10, 2, tzinfo=timezone.utc)
        )
        self.assertEqual(result["event_count"], 4)
        self.assertEqual(set(result["counts"].values()), {1})

    def test_late_event_cannot_rewrite_occurrence_minute(self):
        event = {
            "event_id": "late", "kind": "NEWS", "source": "official",
            "occurred_at": "2026-08-30T09:00:05+00:00",
            "available_at": "2026-08-30T10:00:05+00:00",
        }
        old_end = int(datetime(2026, 8, 30, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
        earlier = events_for_closed_minute(
            [event], old_end, now=datetime(2026, 8, 30, 11, tzinfo=timezone.utc)
        )
        self.assertEqual(earlier["event_count"], 0)

    def test_day_only_and_available_before_occurrence_fail_closed(self):
        base = {"event_id": "x", "kind": "MACRO", "source": "Fed"}
        with self.assertRaisesRegex(ValueError, "EXACT_INTRADAY"):
            normalize_event({**base, "occurred_at": "2026-08-30",
                             "available_at": "2026-08-30T10:00:00Z"})
        with self.assertRaisesRegex(ValueError, "AVAILABLE_BEFORE"):
            normalize_event({**base, "occurred_at": "2026-08-30T10:01:00Z",
                             "available_at": "2026-08-30T10:00:00Z"})


if __name__ == "__main__":
    unittest.main()
