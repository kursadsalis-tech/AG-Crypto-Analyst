import unittest
import sqlite3
from datetime import datetime, timezone

from data import federal_reserve_exact_events as fed
from data.causal_minute_evidence import events_for_closed_minute
from database.causal_evidence_store import load_events, save_exact_macro_event


PAGE = """
<html><body>
<p>FOMC Meeting Statement:<br/>
<a href="/files/a.pdf">PDF</a> | <a href="/newsevents/a.htm">HTML</a>
<span>(Released July 29, 2026 at 2:00 p.m.)</span></p>
<p><a href="/newsevents/a1.htm">Implementation Note</a>
<span>(Released July 29, 2026 at 2:00 p.m.)</span></p>
<p>Minutes: <a href="/minutes/a.pdf">PDF</a> |
<a href="/minutes/a.htm">HTML</a>
<span>(Released August 19, 2026 at 2:00 p.m.)</span></p>
</body></html>
"""


class FederalReserveExactEventsTests(unittest.TestCase):
    def test_calendar_keeps_only_requested_year_and_deduplicates(self):
        calendar = """
        <a href='/monetarypolicy/fomcpresconf20260729.htm'>x</a>
        <a href='/monetarypolicy/fomcpresconf20260729.htm'>x</a>
        <a href='/monetarypolicy/fomcpresconf20250730.htm'>y</a>
        """
        self.assertEqual(fed.meeting_pages(calendar, 2026), [
            "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260729.htm"
        ])

    def test_exact_release_is_converted_from_new_york_to_utc(self):
        events = fed.parse_meeting_page(
            PAGE,
            "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260729.htm",
        )
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0]["event_name"], "FOMC_STATEMENT")
        self.assertEqual(events[0]["available_at"], "2026-07-29T18:00:00+00:00")
        self.assertEqual(events[2]["available_at"], "2026-08-19T18:00:00+00:00")
        self.assertEqual(len(events[0]["detail"]["meeting_page_sha256"]), 64)

    def test_page_without_explicit_release_time_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "NO_EXACT_RELEASE_EVENTS"):
            fed.parse_meeting_page(
                "<p>FOMC Meeting Statement: Released July 29, 2026</p>",
                "https://www.federalreserve.gov/monetarypolicy/example.htm",
            )

    def test_official_event_enters_only_its_available_minute(self):
        parsed = fed.parse_meeting_page(
            PAGE,
            "https://www.federalreserve.gov/monetarypolicy/fomcpresconf20260729.htm",
        )[0]
        con = sqlite3.connect(":memory:")
        try:
            save_exact_macro_event(con, parsed, observed_at=parsed["available_at"])
            stored = load_events(con)
        finally:
            con.close()
        minute_end = int(
            datetime(2026, 7, 29, 18, 1, tzinfo=timezone.utc).timestamp() * 1000
        )
        line = events_for_closed_minute(
            stored, minute_end,
            now=datetime(2026, 7, 29, 18, 2, tzinfo=timezone.utc),
        )
        self.assertEqual(line["counts"]["MACRO"], 1)
        self.assertEqual(line["events"][0]["source"],
                         "BOARD_OF_GOVERNORS_FEDERAL_RESERVE_SYSTEM")


if __name__ == "__main__":
    unittest.main()
