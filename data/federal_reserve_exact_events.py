"""Collect exact-time FOMC releases from first-party Federal Reserve pages.

This is an evidence collector, not a signal or a policy rule. It accepts only
documents whose official meeting page explicitly says "Released ... at ...".
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests

from database.causal_evidence_store import save_exact_macro_event
from database.runtime_store import connect


VERSION = "FEDERAL_RESERVE_EXACT_EVENTS_V1"
BASE_URL = "https://www.federalreserve.gov"
CALENDAR_URL = BASE_URL + "/monetarypolicy/fomccalendars.htm"
USER_AGENT = "AG-Crypto-Analyst/FederalReserveExactEventsV1"
PAGE_LINK = re.compile(
    r'href=["\'](?P<href>/monetarypolicy/fomc(?:pressconf|presconf)\d{8}\.htm)["\']',
    re.I,
)
YEAR_HEADING = re.compile(r"<h4><a[^>]*>(\d{4}) FOMC Meetings</a></h4>", re.I)
MEETING_DATE = re.compile(
    r"fomc-meeting__month[^>]*><strong>(?P<month>[^<]+)</strong></div>\s*"
    r"<div class=\"fomc-meeting__date[^>]*>(?P<dates>[^<]+)</div>",
    re.I,
)
PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.I | re.S)
HREF = re.compile(r'href=["\']([^"\']+)["\']', re.I)
TAG = re.compile(r"<[^>]+>")
RELEASE = re.compile(
    r"\(Released\s+(?P<date>[A-Z][a-z]+\s+\d{1,2},\s+\d{4})\s+at\s+"
    r"(?P<time>\d{1,2}:\d{2}\s+[ap]\.m\.)\)",
    re.I,
)
EVENT_LABELS = {
    "FOMC Meeting Statement": "FOMC_STATEMENT",
    "Implementation Note": "FOMC_IMPLEMENTATION_NOTE",
    "Projections Materials": "FOMC_PROJECTIONS",
    "Minutes": "FOMC_MINUTES",
}
MONTHS = {
    name: index for index, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"), 1
    )
}


def fetch_text(url, timeout=25):
    response = requests.get(
        url, timeout=timeout,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
    )
    response.raise_for_status()
    if "federalreserve.gov" not in response.url.lower():
        raise ValueError("FEDERAL_RESERVE_REDIRECT_REQUIRED")
    return response.text


def meeting_pages(calendar_html, year=None):
    found = []
    seen = set()
    for match in PAGE_LINK.finditer(calendar_html):
        href = match.group("href")
        if year is not None and str(year) not in href:
            continue
        url = urljoin(BASE_URL, href)
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def decision_release_times(calendar_html, year):
    """Return regular meeting decision times explicitly scheduled by the Fed."""
    headings = list(YEAR_HEADING.finditer(calendar_html))
    section = None
    for index, heading in enumerate(headings):
        if int(heading.group(1)) == int(year):
            end = headings[index + 1].start() if index + 1 < len(headings) else len(calendar_html)
            section = calendar_html[heading.end():end]
            break
    if section is None:
        raise ValueError(f"FOMC_YEAR_SECTION_MISSING:{year}")
    releases = []
    for match in MEETING_DATE.finditer(section):
        month_text = html.unescape(match.group("month")).strip()
        date_text = html.unescape(match.group("dates")).strip()
        if "notation" in date_text.lower():
            continue
        clean_dates = re.sub(r"[^0-9\-]", "", date_text)
        if not clean_dates:
            continue
        decision_day = int(clean_dates.split("-")[-1])
        month_parts = month_text.split("/")
        decision_month_name = month_parts[-1]
        decision_month = MONTHS.get(decision_month_name)
        if decision_month is None:
            raise ValueError(f"FOMC_MONTH_UNSUPPORTED:{month_text}")
        decision_year = int(year)
        if len(month_parts) == 2 and MONTHS.get(month_parts[0], 0) > decision_month:
            decision_year += 1
        local = datetime(
            decision_year, decision_month, decision_day, 14, 0,
            tzinfo=ZoneInfo("America/New_York"),
        )
        releases.append(local.astimezone(timezone.utc))
    if not releases:
        raise ValueError(f"FOMC_DECISION_DATES_MISSING:{year}")
    return releases


def meeting_page_for_date(calendar_html, decision_date):
    stamp = decision_date.strftime("%Y%m%d")
    matches = [url for url in meeting_pages(calendar_html, decision_date.year)
               if stamp in url]
    if len(matches) > 1:
        raise ValueError(f"AMBIGUOUS_FOMC_MEETING_PAGE:{stamp}")
    return matches[0] if matches else None


def _release_utc(date_text, time_text):
    cleaned = time_text.lower().replace(".", "").upper()
    local = datetime.strptime(
        f"{date_text} {cleaned}", "%B %d, %Y %I:%M %p"
    ).replace(tzinfo=ZoneInfo("America/New_York"))
    return local.astimezone(timezone.utc)


def _label(text):
    for visible, event_name in EVENT_LABELS.items():
        if text.startswith(visible):
            return visible, event_name
    return None, None


def parse_meeting_page(page_html, page_url):
    if not page_url.startswith(BASE_URL + "/monetarypolicy/"):
        raise ValueError("UNOFFICIAL_PAGE_URL")
    page_hash = hashlib.sha256(page_html.encode("utf-8")).hexdigest()
    events = []
    for raw_paragraph in PARAGRAPH.findall(page_html):
        text = " ".join(html.unescape(TAG.sub(" ", raw_paragraph)).split())
        released = RELEASE.search(text)
        visible_label, event_name = _label(text)
        if not released or not event_name:
            continue
        links = [urljoin(page_url, value) for value in HREF.findall(raw_paragraph)]
        official_links = [value for value in links if value.startswith(BASE_URL + "/")]
        if not official_links:
            raise ValueError(f"OFFICIAL_DOCUMENT_LINK_MISSING:{event_name}")
        released_at = _release_utc(released.group("date"), released.group("time"))
        document_url = official_links[-1]
        identity = hashlib.sha256(
            f"{event_name}|{released_at.isoformat()}|{document_url}".encode("utf-8")
        ).hexdigest()
        events.append({
            "event_id": identity,
            "source": "BOARD_OF_GOVERNORS_FEDERAL_RESERVE_SYSTEM",
            "event_name": event_name,
            "occurred_at": released_at.isoformat(),
            "available_at": released_at.isoformat(),
            "detail": {
                "visible_label": visible_label,
                "document_url": document_url,
                "meeting_page_url": page_url,
                "meeting_page_sha256": page_hash,
                "timestamp_basis": "OFFICIAL_RELEASED_DATE_AND_TIME",
                "source_timezone": "America/New_York",
            },
        })
    if not events:
        raise ValueError("NO_EXACT_RELEASE_EVENTS")
    return events


def collect_year(year, max_pages=None):
    calendar_html = fetch_text(CALENDAR_URL)
    pages = meeting_pages(calendar_html, year=year)
    if max_pages is not None:
        pages = pages[:max_pages]
    events = []
    page_hashes = {}
    for url in pages:
        body = fetch_text(url)
        page_hashes[url] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        events.extend(parse_meeting_page(body, url))
    unique = {event["event_id"]: event for event in events}
    ordered = sorted(unique.values(), key=lambda row: (row["available_at"], row["event_id"]))
    return {
        "contract_version": VERSION,
        "source": "BOARD_OF_GOVERNORS_FEDERAL_RESERVE_SYSTEM",
        "calendar_url": CALENDAR_URL,
        "calendar_sha256": hashlib.sha256(calendar_html.encode("utf-8")).hexdigest(),
        "year": int(year), "meeting_page_count": len(pages),
        "event_count": len(ordered), "page_sha256": page_hashes,
        "events": ordered,
    }


def write_report(path, report):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--schedule-only", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--database")
    args = parser.parse_args()
    if args.schedule_only:
        calendar_html = fetch_text(CALENDAR_URL)
        releases = decision_release_times(calendar_html, args.year)
        report = {
            "contract_version": VERSION,
            "source": "BOARD_OF_GOVERNORS_FEDERAL_RESERVE_SYSTEM",
            "calendar_url": CALENDAR_URL,
            "calendar_sha256": hashlib.sha256(calendar_html.encode("utf-8")).hexdigest(),
            "year": args.year,
            "scheduled_release_times": [value.isoformat() for value in releases],
            "event_count": 0, "meeting_page_count": 0, "events": [],
        }
    else:
        report = collect_year(args.year, args.max_pages)
    if args.database:
        with connect(args.database) as con:
            for event in report["events"]:
                save_exact_macro_event(con, event)
            con.commit()
    write_report(args.output, report)
    print(json.dumps({
        "meeting_page_count": report["meeting_page_count"],
        "event_count": report["event_count"], "database_written": bool(args.database),
        "scheduled_release_count": len(report.get("scheduled_release_times") or []),
    }, sort_keys=True))
    return report


if __name__ == "__main__":
    main()
