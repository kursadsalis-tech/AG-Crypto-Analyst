import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import observe_exact_macro_events as observer


CALENDAR = """
<h4><a>2026 FOMC Meetings</a></h4>
<div class="fomc-meeting">
<div class="fomc-meeting__month x"><strong>September</strong></div>
<div class="fomc-meeting__date x">15-16*</div>
<a href="/monetarypolicy/fomcpresconf20260916.htm">meeting</a>
</div>
<h4><a>2025 FOMC Meetings</a></h4>
"""
PAGE = """
<p>FOMC Meeting Statement:<a href="/newsevents/statement.htm">HTML</a>
<span>(Released September 16, 2026 at 2:00 p.m.)</span></p>
"""


class ExactMacroObserverTests(unittest.TestCase):
    def test_outside_window_uses_cached_schedule_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            health = Path(tmp) / "health.json"
            health.write_text(json.dumps({
                "calendar_checked_at": "2026-08-30T00:00:00Z",
                "scheduled_release_times": ["2026-09-16T18:00:00Z"],
            }))
            now = datetime(2026, 8, 30, 1, tzinfo=timezone.utc)
            with patch.object(observer.fed, "fetch_text") as fetch:
                result = observer.observe(Path(tmp) / "db.sqlite", health, now)
            fetch.assert_not_called()
            self.assertEqual(result["status"], "HEALTHY")
            self.assertEqual(result["source_status"], "NOT_DUE")

    def test_release_window_fetches_one_page_and_preserves_observation_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            health = Path(tmp) / "health.json"
            health.write_text(json.dumps({
                "calendar_checked_at": "2026-09-16T17:00:00Z",
                "scheduled_release_times": ["2026-09-16T18:00:00Z"],
            }))
            now = datetime(2026, 9, 16, 18, 0, 20, tzinfo=timezone.utc)
            with patch.object(observer.fed, "fetch_text", side_effect=[CALENDAR, PAGE]):
                result = observer.observe(Path(tmp) / "db.sqlite", health, now)
            self.assertEqual(result["events_observed"], 1)
            self.assertEqual(result["source_status"], "OFFICIAL_RELEASE_PAGE_OBSERVED")
            import sqlite3
            with sqlite3.connect(Path(tmp) / "db.sqlite") as con:
                row = con.execute(
                    "SELECT official_release_at,observed_at,available_at "
                    "FROM exact_time_macro_events"
                ).fetchone()
            self.assertEqual(row, (
                "2026-09-16T18:00:00+00:00",
                "2026-09-16T18:00:20+00:00",
                "2026-09-16T18:00:20+00:00",
            ))
            self.assertEqual(os.stat(health).st_mode & 0o777, 0o600)

    def test_missing_page_after_grace_degrades_without_inventing_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            health = Path(tmp) / "health.json"
            health.write_text(json.dumps({
                "calendar_checked_at": "2026-09-16T17:00:00Z",
                "scheduled_release_times": ["2026-09-16T18:00:00Z"],
            }))
            now = datetime(2026, 9, 16, 18, 11, tzinfo=timezone.utc)
            calendar_without_page = CALENDAR.replace(
                '<a href="/monetarypolicy/fomcpresconf20260916.htm">meeting</a>', ""
            )
            with patch.object(observer.fed, "fetch_text", return_value=calendar_without_page):
                result = observer.observe(Path(tmp) / "db.sqlite", health, now)
            self.assertEqual(result["status"], "DEGRADED")
            self.assertEqual(result["events_observed"], 0)

    def test_calendar_error_uses_bounded_retry_backoff(self):
        state = {
            "calendar_attempted_at": "2026-08-30T10:00:00Z",
            "scheduled_release_times": [],
        }
        self.assertFalse(observer.refresh_due(
            state, datetime(2026, 8, 30, 10, 14, tzinfo=timezone.utc)
        ))
        self.assertTrue(observer.refresh_due(
            state, datetime(2026, 8, 30, 10, 15, tzinfo=timezone.utc)
        ))


if __name__ == "__main__":
    unittest.main()
