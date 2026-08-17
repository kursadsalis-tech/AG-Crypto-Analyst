"""Run the investment committee report once per Istanbul calendar day."""
from __future__ import annotations

import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from reports import investment_committee_report


DB_NAME = investment_committee_report.DB_NAME
AGENT_NAME = "report_generator_daily"
ISTANBUL = ZoneInfo("Europe/Istanbul")
REPORT_TIME = time(hour=11, minute=0)


def utcnow():
    return datetime.now(timezone.utc)


def parse_time(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def ensure_state_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_run_state (
            agent_name TEXT PRIMARY KEY,
            last_success_at TEXT,
            last_status TEXT,
            last_message TEXT
        )
    """)
    con.commit()


def last_success(con):
    row = con.execute(
        "SELECT last_success_at FROM agent_run_state WHERE agent_name=?",
        (AGENT_NAME,),
    ).fetchone()
    return parse_time(row[0]) if row else None


def schedule_state(con, now):
    local_now = now.astimezone(ISTANBUL)
    previous = last_success(con)
    if local_now.time().replace(tzinfo=None) < REPORT_TIME:
        return {
            "due": False,
            "reason": "WAITING_FOR_11_00",
            "local_date": local_now.date().isoformat(),
            "local_time": local_now.strftime("%H:%M"),
        }
    if (
        previous
        and previous.astimezone(ISTANBUL).date() == local_now.date()
    ):
        return {
            "due": False,
            "reason": "ALREADY_GENERATED_TODAY",
            "local_date": local_now.date().isoformat(),
            "local_time": local_now.strftime("%H:%M"),
        }
    return {
        "due": True,
        "reason": "DAILY_REPORT_DUE",
        "local_date": local_now.date().isoformat(),
        "local_time": local_now.strftime("%H:%M"),
    }


def save_state(con, status, message, success_at=None):
    con.execute("""
        INSERT INTO agent_run_state(
            agent_name, last_success_at, last_status, last_message
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(agent_name) DO UPDATE SET
            last_success_at=COALESCE(
                excluded.last_success_at,
                agent_run_state.last_success_at
            ),
            last_status=excluded.last_status,
            last_message=excluded.last_message
    """, (
        AGENT_NAME,
        success_at.isoformat() if success_at else None,
        status,
        message,
    ))
    con.commit()


def main(force=False, record_schedule=True):
    now = utcnow()
    con = runtime_connect(DB_NAME)
    try:
        ensure_state_table(con)
        schedule = schedule_state(con, now)
        if not force and not schedule["due"]:
            if schedule["reason"] == "WAITING_FOR_11_00":
                print(
                    "Report Generator: İstanbul 11:00 bekleniyor; "
                    f"şu an {schedule['local_time']}."
                )
            else:
                print(
                    "Report Generator: bugünün 11:00 raporu zaten üretildi; "
                    "bu cycle atlandı."
                )
            return {"status": "SKIPPED", "schedule": schedule}
    finally:
        con.close()

    try:
        result = investment_committee_report.main()
        report_state = result["summary"]["report_state"]
        status = "SUCCESS" if report_state in {
            "SHADOW_ONLY",
            "PRODUCTION",
        } else "DATA_BLOCKED"
        message = (
            f"report_state={report_state} | "
            f"decision_run={result['summary']['decision_run_id']} | "
            f"coins={result['summary']['decision_count']} | "
            f"risk_blocked={result['summary']['risk_blocked_count']} | "
            f"file={result['report_file']}"
        )
        if record_schedule:
            con = runtime_connect(DB_NAME)
            try:
                ensure_state_table(con)
                save_state(con, status, message, now)
            finally:
                con.close()
        print(f"Report Generator cycle: {status} | {message}")
        return {
            "status": status,
            "message": message,
            "schedule": schedule,
            "result": result,
        }
    except Exception as exc:
        message = str(exc)
        if record_schedule:
            con = runtime_connect(DB_NAME)
            try:
                ensure_state_table(con)
                save_state(con, "ERROR", message)
            finally:
                con.close()
        print(f"Report Generator cycle: HATA -> {message}")
        return {
            "status": "ERROR",
            "message": message,
            "schedule": schedule,
        }


if __name__ == "__main__":
    # Manual module execution validates generation without consuming the
    # scheduled 11:00 slot. The live product cycle uses the default arguments.
    main(force=True, record_schedule=False)
