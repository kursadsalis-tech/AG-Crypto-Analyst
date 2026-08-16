"""Autonomous, auditable virtual-money trading agent for AG Crypto Analyst.

The agent consumes Unified Forecast Engine rows, never sends a real order, fills
only from exact Binance 1h timestamps, charges explicit fees, and produces a
daily Europe/Istanbul 03:00 portfolio report.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from decision_engine import forecast_engine


DB_NAME = forecast_engine.DB_NAME
AGENT_VERSION = "CRYPTO_PROFIT_AGENT_V1"
POLICY_VERSION = "PAPER_OPPORTUNITY_POLICY_V1_FIXED"
INITIAL_CAPITAL = float(os.getenv("AGCA_PAPER_INITIAL_USDT", "10000"))
FEE_BPS = float(os.getenv("AGCA_PAPER_FEE_BPS", "10"))
FEE_RATE = FEE_BPS / 10000.0
MAX_TOTAL_EXPOSURE = 0.60
POSITION_FRACTION = 0.10
MAX_OPEN_POSITIONS = 6
MAX_NEW_PER_CYCLE = 3
MIN_DIRECTION_PROBABILITY = 0.52
MIN_PROBABILITY_GAP = 0.05
TRADE_HORIZONS = (4, 24)
ISTANBUL = ZoneInfo("Europe/Istanbul")
REPORT_PATH = Path("reports/daily_crypto_profit_agent_report.md")


def utcnow():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).isoformat()


def parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def connect():
    con = sqlite3.connect(DB_NAME, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=30000")
    return con


def ensure_tables(con):
    con.executescript("""
        CREATE TABLE IF NOT EXISTS paper_agent_portfolio (
            portfolio_id INTEGER PRIMARY KEY CHECK(portfolio_id=1),
            created_at TEXT NOT NULL,
            agent_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            initial_capital REAL NOT NULL,
            cash REAL NOT NULL,
            realized_pnl REAL NOT NULL DEFAULT 0,
            total_fees REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS paper_agent_positions (
            position_id INTEGER PRIMARY KEY AUTOINCREMENT,
            forecast_id INTEGER NOT NULL UNIQUE,
            agent_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            horizon_hours INTEGER NOT NULL,
            direction TEXT NOT NULL CHECK(direction IN ('LONG','SHORT')),
            status TEXT NOT NULL CHECK(status IN
                ('PENDING_ENTRY','OPEN','CLOSED','CANCELLED')),
            selected_at TEXT NOT NULL,
            as_of TEXT NOT NULL,
            planned_entry_time TEXT NOT NULL,
            due_time TEXT NOT NULL,
            entry_time TEXT,
            entry_price REAL,
            quantity REAL,
            notional REAL NOT NULL,
            entry_fee REAL NOT NULL DEFAULT 0,
            exit_time TEXT,
            exit_price REAL,
            exit_fee REAL NOT NULL DEFAULT 0,
            gross_pnl REAL,
            net_pnl REAL,
            confidence REAL NOT NULL,
            p_up REAL NOT NULL,
            p_down REAL NOT NULL,
            target_price REAL NOT NULL,
            model_version TEXT NOT NULL,
            artifact_hash TEXT NOT NULL,
            selection_reason TEXT NOT NULL,
            cancel_reason TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_paper_positions_status_due
        ON paper_agent_positions(status,due_time);
        CREATE INDEX IF NOT EXISTS idx_paper_positions_symbol_status
        ON paper_agent_positions(symbol,status);

        CREATE TABLE IF NOT EXISTS paper_agent_daily_reports (
            report_date TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            window_start TEXT NOT NULL,
            window_end TEXT NOT NULL,
            report_json TEXT NOT NULL,
            telegram_sent INTEGER NOT NULL DEFAULT 0
        );
    """)
    con.execute("""
        INSERT OR IGNORE INTO paper_agent_portfolio(
            portfolio_id,created_at,agent_version,policy_version,
            initial_capital,cash,realized_pnl,total_fees
        ) VALUES(1,?,?,?,?,?,?,?)
    """, (
        iso(utcnow()), AGENT_VERSION, POLICY_VERSION,
        INITIAL_CAPITAL, INITIAL_CAPITAL, 0.0, 0.0,
    ))
    con.commit()


def exact_open(con, symbol, as_of):
    expected = parse_time(as_of) + timedelta(milliseconds=1)
    rows = con.execute("""
        SELECT open_time,open FROM market_klines
        WHERE symbol=? AND interval='1h'
          AND open_time>=? AND open_time<=?
        ORDER BY open_time LIMIT 3
    """, (
        symbol,
        iso(expected - timedelta(seconds=2)),
        iso(expected + timedelta(seconds=2)),
    )).fetchall()
    for stamp, price in rows:
        if abs((parse_time(stamp) - expected).total_seconds()) <= 1.0:
            return parse_time(stamp), float(price)
    return None


def exact_close(con, symbol, due_time):
    expected = parse_time(due_time)
    rows = con.execute("""
        SELECT close_time,close FROM market_klines
        WHERE symbol=? AND interval='1h'
          AND close_time>=? AND close_time<=?
        ORDER BY close_time LIMIT 3
    """, (
        symbol,
        iso(expected - timedelta(seconds=2)),
        iso(expected + timedelta(seconds=2)),
    )).fetchall()
    for stamp, price in rows:
        if abs((parse_time(stamp) - expected).total_seconds()) <= 1.0:
            return parse_time(stamp), float(price)
    return None


def directional_pnl(direction, entry_price, exit_price, quantity):
    if direction == "LONG":
        return (exit_price - entry_price) * quantity
    if direction == "SHORT":
        return (entry_price - exit_price) * quantity
    raise ValueError("UNKNOWN_DIRECTION")


def latest_mark(con, symbol):
    row = con.execute("""
        SELECT close FROM market_klines
        WHERE symbol=? AND interval='1h'
        ORDER BY close_time DESC LIMIT 1
    """, (symbol,)).fetchone()
    return float(row[0]) if row else None


def portfolio_snapshot(con):
    portfolio = con.execute("""
        SELECT initial_capital,cash,realized_pnl,total_fees
        FROM paper_agent_portfolio WHERE portfolio_id=1
    """).fetchone()
    open_rows = con.execute("""
        SELECT symbol,direction,entry_price,quantity,notional
        FROM paper_agent_positions WHERE status='OPEN'
    """).fetchall()
    reserved = 0.0
    unrealized = 0.0
    for symbol, direction, entry, quantity, notional in open_rows:
        reserved += float(notional)
        mark = latest_mark(con, symbol)
        if mark is not None:
            unrealized += directional_pnl(
                direction, float(entry), mark, float(quantity)
            )
    initial, cash, realized, fees = map(float, portfolio)
    equity = cash + reserved + unrealized
    return {
        "initial_capital": initial,
        "cash": cash,
        "reserved": reserved,
        "realized_pnl": realized,
        "unrealized_pnl": unrealized,
        "total_fees": fees,
        "equity": equity,
        "return_pct": (equity / initial - 1.0) * 100.0,
        "open_positions": len(open_rows),
    }


def fill_pending(con, now):
    rows = con.execute("""
        SELECT position_id,symbol,as_of,notional,direction
        FROM paper_agent_positions
        WHERE status='PENDING_ENTRY'
        ORDER BY selected_at,position_id
    """).fetchall()
    filled = 0
    cancelled = 0
    for position_id, symbol, as_of, notional, direction in rows:
        result = exact_open(con, symbol, as_of)
        if result is None:
            if now - parse_time(as_of) > timedelta(hours=2):
                con.execute("""
                    UPDATE paper_agent_positions
                    SET status='CANCELLED',cancel_reason=?
                    WHERE position_id=? AND status='PENDING_ENTRY'
                """, ("EXACT_NEXT_OPEN_UNAVAILABLE", position_id))
                cancelled += 1
            continue
        entry_time, entry_price = result
        notional = float(notional)
        fee = notional * FEE_RATE
        cash = float(con.execute(
            "SELECT cash FROM paper_agent_portfolio WHERE portfolio_id=1"
        ).fetchone()[0])
        if cash + 1e-9 < notional + fee:
            con.execute("""
                UPDATE paper_agent_positions
                SET status='CANCELLED',cancel_reason=?
                WHERE position_id=? AND status='PENDING_ENTRY'
            """, ("INSUFFICIENT_VIRTUAL_CASH", position_id))
            cancelled += 1
            continue
        quantity = notional / entry_price
        con.execute("""
            UPDATE paper_agent_positions SET
                status='OPEN',entry_time=?,entry_price=?,quantity=?,entry_fee=?
            WHERE position_id=? AND status='PENDING_ENTRY'
        """, (iso(entry_time), entry_price, quantity, fee, position_id))
        con.execute("""
            UPDATE paper_agent_portfolio
            SET cash=cash-?,total_fees=total_fees+?
            WHERE portfolio_id=1
        """, (notional + fee, fee))
        filled += 1
    con.commit()
    return filled, cancelled


def settle_open(con, now):
    rows = con.execute("""
        SELECT position_id,symbol,direction,due_time,entry_price,quantity,
               notional,entry_fee
        FROM paper_agent_positions
        WHERE status='OPEN' AND due_time<=?
        ORDER BY due_time,position_id
    """, (iso(now),)).fetchall()
    closed = 0
    for row in rows:
        position_id, symbol, direction, due, entry, quantity, notional, entry_fee = row
        result = exact_close(con, symbol, due)
        if result is None:
            continue
        exit_time, exit_price = result
        entry = float(entry)
        quantity = float(quantity)
        notional = float(notional)
        entry_fee = float(entry_fee)
        gross = directional_pnl(direction, entry, exit_price, quantity)
        exit_value = quantity * exit_price
        exit_fee = exit_value * FEE_RATE
        net = gross - entry_fee - exit_fee
        con.execute("""
            UPDATE paper_agent_positions SET
                status='CLOSED',exit_time=?,exit_price=?,exit_fee=?,
                gross_pnl=?,net_pnl=?
            WHERE position_id=? AND status='OPEN'
        """, (
            iso(exit_time), exit_price, exit_fee, gross, net, position_id,
        ))
        con.execute("""
            UPDATE paper_agent_portfolio SET
                cash=cash+?,realized_pnl=realized_pnl+?,
                total_fees=total_fees+?
            WHERE portfolio_id=1
        """, (notional + gross - exit_fee, net, exit_fee))
        closed += 1
    con.commit()
    return closed


def candidate_direction(row):
    p_up = float(row["p_up"])
    p_down = float(row["p_down"])
    current = float(row["current_price"])
    target = float(row["target_price"])
    if p_up >= p_down:
        direction = "LONG"
        confidence = p_up
        aligned = target > current
    else:
        direction = "SHORT"
        confidence = p_down
        aligned = target < current
    gap = abs(p_up - p_down)
    if confidence < MIN_DIRECTION_PROBABILITY:
        return None
    if gap < MIN_PROBABILITY_GAP or not aligned:
        return None
    return direction, confidence, gap


def latest_candidates(con):
    latest = con.execute(
        "SELECT MAX(as_of) FROM forecast_engine_predictions"
    ).fetchone()[0]
    if not latest:
        return []
    rows = con.execute("""
        SELECT forecast_id,symbol,horizon_hours,as_of,due_time,current_price,
               target_price,p_up,p_down,p_neutral,risk_eligible,risk_cap_pct,
               model_version,artifact_hash
        FROM forecast_engine_predictions
        WHERE as_of=? AND horizon_hours IN (4,24)
        ORDER BY symbol,horizon_hours
    """, (latest,)).fetchall()
    keys = (
        "forecast_id","symbol","horizon","as_of","due_time","current_price",
        "target_price","p_up","p_down","p_neutral","risk_eligible",
        "risk_cap_pct","model_version","artifact_hash",
    )
    candidates = []
    for raw in rows:
        row = dict(zip(keys, raw))
        if not bool(row["risk_eligible"]):
            continue
        decision = candidate_direction(row)
        if decision is None:
            continue
        direction, confidence, gap = decision
        predicted_move = abs(
            (float(row["target_price"]) / float(row["current_price"]) - 1.0)
            * 100.0
        )
        row.update({
            "direction": direction,
            "confidence": confidence,
            "gap": gap,
            "score": confidence + min(predicted_move / 100.0, 0.10),
            "predicted_move_pct": predicted_move,
        })
        candidates.append(row)
    return sorted(candidates, key=lambda item: item["score"], reverse=True)


def select_new_positions(con, now):
    snapshot = portfolio_snapshot(con)
    active = con.execute("""
        SELECT symbol FROM paper_agent_positions
        WHERE status IN ('PENDING_ENTRY','OPEN')
    """).fetchall()
    active_symbols = {row[0] for row in active}
    capacity = max(0, MAX_OPEN_POSITIONS - len(active))
    if capacity <= 0:
        return 0
    max_new = min(MAX_NEW_PER_CYCLE, capacity)
    max_exposure = snapshot["equity"] * MAX_TOTAL_EXPOSURE
    available_exposure = max(0.0, max_exposure - snapshot["reserved"])
    base_notional = snapshot["equity"] * POSITION_FRACTION
    selected = 0
    for row in latest_candidates(con):
        if selected >= max_new or row["symbol"] in active_symbols:
            continue
        notional = min(
            base_notional,
            available_exposure,
            snapshot["cash"] / (1.0 + FEE_RATE),
        )
        if notional < max(10.0, snapshot["equity"] * 0.01):
            break
        reason = (
            f"{row['horizon']}h {row['direction']} | "
            f"p={100*row['confidence']:.1f}% | "
            f"gap={100*row['gap']:.1f}pp | "
            f"q50_move={row['predicted_move_pct']:.3f}%"
        )
        planned_entry = parse_time(row["as_of"]) + timedelta(milliseconds=1)
        con.execute("""
            INSERT OR IGNORE INTO paper_agent_positions(
                forecast_id,agent_version,policy_version,symbol,horizon_hours,
                direction,status,selected_at,as_of,planned_entry_time,due_time,
                notional,confidence,p_up,p_down,target_price,model_version,
                artifact_hash,selection_reason
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            row["forecast_id"], AGENT_VERSION, POLICY_VERSION, row["symbol"],
            row["horizon"], row["direction"], "PENDING_ENTRY", iso(now),
            row["as_of"], iso(planned_entry), row["due_time"], notional,
            row["confidence"], row["p_up"], row["p_down"],
            row["target_price"], row["model_version"], row["artifact_hash"],
            reason,
        ))
        if con.total_changes:
            selected += 1
            active_symbols.add(row["symbol"])
            available_exposure -= notional
            snapshot["cash"] -= notional * (1.0 + FEE_RATE)
    con.commit()
    return selected


def report_window(now):
    local = now.astimezone(ISTANBUL)
    report_date = local.date()
    end_local = datetime.combine(
        report_date, datetime.min.time(), tzinfo=ISTANBUL
    ) + timedelta(hours=3)
    if local < end_local:
        return None
    start_local = end_local - timedelta(days=1)
    return (
        report_date.isoformat(),
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def build_daily_report(con, report_date, start, end):
    snapshot = portfolio_snapshot(con)
    opened = con.execute("""
        SELECT position_id,symbol,direction,horizon_hours,selected_at,entry_time,
               entry_price,notional,entry_fee,confidence,selection_reason,status
        FROM paper_agent_positions
        WHERE selected_at>=? AND selected_at<?
        ORDER BY selected_at,position_id
    """, (iso(start), iso(end))).fetchall()
    closed = con.execute("""
        SELECT position_id,symbol,direction,horizon_hours,entry_time,entry_price,
               exit_time,exit_price,notional,entry_fee,exit_fee,gross_pnl,
               net_pnl,selection_reason
        FROM paper_agent_positions
        WHERE exit_time>=? AND exit_time<?
        ORDER BY exit_time,position_id
    """, (iso(start), iso(end))).fetchall()
    wins = sum(1 for row in closed if float(row[12]) > 0)
    daily_net = sum(float(row[12]) for row in closed)
    daily_fees = sum(float(row[9]) + float(row[10]) for row in closed)
    payload = {
        "schema": "CRYPTO_PROFIT_AGENT_DAILY_REPORT_V1",
        "report_date": report_date,
        "window_start": iso(start),
        "window_end": iso(end),
        "portfolio": snapshot,
        "opened_count": len(opened),
        "closed_count": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "win_rate_pct": (100.0 * wins / len(closed)) if closed else 0.0,
        "daily_net_pnl": daily_net,
        "daily_fees": daily_fees,
        "opened": opened,
        "closed": closed,
    }
    return payload


def markdown_report(payload):
    p = payload["portfolio"]
    lines = [
        "# AG Crypto Analyst — Kripto Kazanç Ajanı",
        "",
        f"Rapor tarihi: {payload['report_date']} | PAPER ONLY",
        f"Pencere: {payload['window_start']} → {payload['window_end']}",
        "",
        "## Portföy",
        "",
        f"- Başlangıç sermayesi: {p['initial_capital']:.2f} USDT",
        f"- Güncel özsermaye: {p['equity']:.2f} USDT ({p['return_pct']:+.2f}%)",
        f"- Nakit: {p['cash']:.2f} USDT",
        f"- Açık pozisyon teminatı: {p['reserved']:.2f} USDT",
        f"- Gerçekleşmiş P&L: {p['realized_pnl']:+.2f} USDT",
        f"- Gerçekleşmemiş P&L: {p['unrealized_pnl']:+.2f} USDT",
        f"- Toplam ücret: {p['total_fees']:.2f} USDT",
        f"- Açık pozisyon: {p['open_positions']}",
        "",
        "## Günlük sonuç",
        "",
        f"- Açılan işlem: {payload['opened_count']}",
        f"- Kapanan işlem: {payload['closed_count']}",
        f"- Kazanan/Kaybeden: {payload['wins']}/{payload['losses']}",
        f"- Başarı oranı: {payload['win_rate_pct']:.1f}%",
        f"- Günlük net P&L: {payload['daily_net_pnl']:+.2f} USDT",
        f"- Kapanan işlemlerin ücretleri: {payload['daily_fees']:.2f} USDT",
        "",
        "## Açılan işlemler",
        "",
    ]
    if not payload["opened"]:
        lines.append("- İşlem açılmadı.")
    for row in payload["opened"]:
        lines.append(
            f"- #{row[0]} {row[1]} {row[2]} {row[3]}h | "
            f"seçim={row[4]} | giriş={row[5] or 'PENDING'} @ "
            f"{float(row[6]):.8g if row[6] is not None else 'N/A'} | "
            f"notional={float(row[7]):.2f} | ücret={float(row[8]):.2f} | "
            f"confidence={100*float(row[9]):.1f}% | status={row[11]} | {row[10]}"
        )
    lines.extend(["", "## Kapanan işlemler", ""])
    if not payload["closed"]:
        lines.append("- İşlem kapanmadı.")
    for row in payload["closed"]:
        lines.append(
            f"- #{row[0]} {row[1]} {row[2]} {row[3]}h | "
            f"giriş={row[4]} @ {float(row[5]):.8g} | "
            f"çıkış={row[6]} @ {float(row[7]):.8g} | "
            f"notional={float(row[8]):.2f} | "
            f"ücret={float(row[9])+float(row[10]):.2f} | "
            f"brüt={float(row[11]):+.2f} | net={float(row[12]):+.2f} USDT | "
            f"{row[13]}"
        )
    lines.extend([
        "",
        "Not: Bu ajan tamamen sanal para kullanır; gerçek borsa emri vermez.",
    ])
    return "\n".join(lines) + "\n"


def telegram_summary(payload):
    p = payload["portfolio"]
    return (
        "🤖 AGCA KRİPTO KAZANÇ AJANI — PAPER\n"
        f"Tarih: {payload['report_date']} | 03:00 İstanbul kapanışı\n"
        f"Bütçe: {p['equity']:.2f} USDT ({p['return_pct']:+.2f}%)\n"
        f"Nakit: {p['cash']:.2f} | Açık risk: {p['reserved']:.2f}\n"
        f"Bugün açılan/kapanan: {payload['opened_count']}/{payload['closed_count']}\n"
        f"Kazanan/Kaybeden: {payload['wins']}/{payload['losses']} "
        f"(%{payload['win_rate_pct']:.1f})\n"
        f"Günlük net: {payload['daily_net_pnl']:+.2f} USDT\n"
        f"Toplam ücret: {p['total_fees']:.2f} USDT\n"
        "Detay: reports/daily_crypto_profit_agent_report.md"
    )


def maybe_daily_report(con, now):
    window = report_window(now)
    if window is None:
        return None
    report_date, start, end = window
    existing = con.execute("""
        SELECT report_json,telegram_sent FROM paper_agent_daily_reports
        WHERE report_date=?
    """, (report_date,)).fetchone()
    if existing:
        return json.loads(existing[0])
    payload = build_daily_report(con, report_date, start, end)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(markdown_report(payload), encoding="utf-8")
    sent = False
    try:
        from notifications.telegram_notifier import is_configured, send_message
        if is_configured():
            sent = bool(send_message(telegram_summary(payload)))
    except Exception as exc:
        payload["telegram_error"] = str(exc)
    con.execute("""
        INSERT INTO paper_agent_daily_reports(
            report_date,created_at,window_start,window_end,report_json,
            telegram_sent
        ) VALUES(?,?,?,?,?,?)
    """, (
        report_date, iso(now), iso(start), iso(end),
        json.dumps(payload, ensure_ascii=False), int(sent),
    ))
    con.commit()
    return payload


def render(result):
    p = result["portfolio"]
    print("AG Crypto Analyst - Kripto Kazanç Ajanı v1.0.0 | PAPER_ONLY")
    print(
        f"Bütçe={p['equity']:.2f} USDT ({p['return_pct']:+.2f}%) | "
        f"nakit={p['cash']:.2f} | açık_risk={p['reserved']:.2f} | "
        f"ücret={p['total_fees']:.2f}"
    )
    print(
        f"Bu cycle: seçilen={result['selected']} | doldurulan={result['filled']} | "
        f"kapanan={result['closed']} | iptal={result['cancelled']} | "
        f"açık={p['open_positions']}"
    )
    if result["daily_report"]:
        report = result["daily_report"]
        print(
            f"03:00 raporu: {report['report_date']} | "
            f"açılan={report['opened_count']} | kapanan={report['closed_count']} | "
            f"net={report['daily_net_pnl']:+.2f} USDT"
        )
    print("Not: Gerçek emir yoktur; LONG/SHORT işlemler 1x sanal simülasyondur.")


def run(now=None):
    now = now or utcnow()
    con = connect()
    try:
        ensure_tables(con)
        filled, cancelled = fill_pending(con, now)
        closed = settle_open(con, now)
        selected = select_new_positions(con, now)
        daily = maybe_daily_report(con, now)
        snapshot = portfolio_snapshot(con)
        return {
            "status": "SUCCESS",
            "filled": filled,
            "cancelled": cancelled,
            "closed": closed,
            "selected": selected,
            "portfolio": snapshot,
            "daily_report": daily,
        }
    finally:
        con.close()


def self_test():
    assert abs(directional_pnl("LONG", 100.0, 105.0, 10.0) - 50.0) < 1e-9
    assert abs(directional_pnl("SHORT", 100.0, 95.0, 10.0) - 50.0) < 1e-9
    base = {
        "p_up": 0.60, "p_down": 0.30,
        "current_price": 100.0, "target_price": 102.0,
    }
    assert candidate_direction(base)[0] == "LONG"
    base.update({"p_up": 0.30, "p_down": 0.60, "target_price": 98.0})
    assert candidate_direction(base)[0] == "SHORT"
    base.update({"p_up": 0.49, "p_down": 0.48, "target_price": 101.0})
    assert candidate_direction(base) is None
    now = datetime(2026, 8, 17, 0, 5, tzinfo=timezone.utc)
    date, start, end = report_window(now)
    assert date == "2026-08-17"
    assert end - start == timedelta(days=1)
    print("Crypto Profit Agent self-test: PASS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return {"status": "SUCCESS"}
    result = run()
    render(result)
    return result


if __name__ == "__main__":
    main()
