"""Read-only operator projection for AGCA Live Control Center.

This module has no scheduler, market connection, trading authority, or database
write path. It projects canonical PAPER runtime state into operator-friendly data.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from paper_trading.crypto_profit_agent import DB_NAME
from paper_trading.position_policy import TP1_R, TP2_R
from control_center.telemetry import runtime_visibility
from paper_trading.execution_model import validate_quote
from paper_trading.execution_contract import MarketSnapshot

MARKET_STATE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_MARKET_STATE",
    "/var/lib/agca/paper-realtime-market-state.json",
))
FEEDBACK_PATH = Path(os.getenv(
    "AGCA_CONTROL_CENTER_FEEDBACK",
    "/var/lib/agca/control-center-data-feedback.jsonl",
))
HEALTH_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_HEALTH",
    "/var/lib/agca/paper-realtime-health.json",
))
BRAIN_STATE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_BRAIN_STATE",
    "/var/lib/agca/paper-realtime-brain-state.json",
))
BRAIN_TIMELINE_PATH = Path(os.getenv(
    "AGCA_PAPER_REALTIME_BRAIN_TIMELINE",
    "/var/lib/agca/paper-realtime-brain-timeline.json",
))


def _utcnow():
    return datetime.now(timezone.utc)


def _parse_iso(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_market_state(path=MARKET_STATE_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"at": None, "live_market": []}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("execution_mode") != "PAPER_ONLY":
        return {"at": payload.get("at"), "live_market": []}
    if payload.get("real_order_allowed") is not False:
        return {"at": payload.get("at"), "live_market": []}
    return payload


def load_brain_state(path=BRAIN_STATE_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"at": None, "phase": "UNKNOWN", "scan": None}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("execution_mode") != "PAPER_ONLY":
        return {"at": payload.get("at"), "phase": "UNKNOWN", "scan": None}
    if payload.get("real_order_allowed") is not False:
        return {"at": payload.get("at"), "phase": "UNKNOWN", "scan": None}
    return payload


def load_brain_timeline(path=BRAIN_TIMELINE_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(payload, list):
        return []
    return [row for row in payload[-100:] if isinstance(row, dict)]


def load_health(path=HEALTH_PATH):
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {"status": "UNKNOWN", "at": None}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("execution_mode") != "PAPER_ONLY":
        return {"status": "UNKNOWN", "at": payload.get("at")}
    if payload.get("real_order_allowed") is not False:
        return {"status": "UNKNOWN", "at": payload.get("at")}
    return payload


def _safe_json_object(value):
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def load_operator_feedback(path=FEEDBACK_PATH, limit=50):
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except (TypeError, ValueError):
            continue
        if row.get("type") == "MARKET_DATA_MISMATCH":
            rows.append(row)
    return list(reversed(rows))


def _state_index(payload):
    return {
        (row.get("symbol"), row.get("market_type")): row
        for row in payload.get("live_market", [])
        if row.get("symbol") and row.get("market_type")
    }


def _freshness_seconds(state, now):
    stamp_ms = state.get("quote_timestamp_ms") or state.get("market_timestamp_ms")
    if not stamp_ms:
        return None
    try:
        age = now.timestamp() - float(stamp_ms) / 1000
        return age if math.isfinite(age) and age >= 0 else None
    except (ValueError, TypeError, OverflowError):
        return None


def _mark_price(direction, state):
    # What an operator can actually close against now: LONG sells to bid,
    # SHORT buys from ask. This matches executable-side PAPER semantics.
    if direction == "LONG":
        return state.get("best_bid")
    return state.get("best_ask")


def _live_pnl(direction, entry_price, quantity, mark):
    if not all(value is not None for value in (entry_price, quantity, mark)):
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return sign * (float(mark) - float(entry_price)) * float(quantity)


def overview(db_path=DB_NAME, market_state_path=MARKET_STATE_PATH,
             health_path=HEALTH_PATH, now=None, *, checkpoint=None):
    """Return a read-only, human-facing snapshot of canonical PAPER state."""
    progress = checkpoint if checkpoint is not None else lambda phase: None
    progress("INPUTS")
    now = now or _utcnow()
    market = load_market_state(market_state_path)
    health = load_health(health_path)
    brain = load_brain_state()
    brain_timeline = load_brain_timeline()
    feedback = load_operator_feedback()
    states = _state_index(market)
    progress("DB_CONNECT")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        con.execute("PRAGMA query_only=ON")
        progress("RUNTIME_VISIBILITY")
        visibility = runtime_visibility(con, health, brain, now)
        progress("SCHEMA")
        management_columns = {x[1] for x in con.execute("PRAGMA table_info(paper_position_management)")}
        position_columns = {x[1] for x in con.execute("PRAGMA table_info(paper_agent_positions)")}
        extras = [f"m.{name}" if name in management_columns else "NULL" for name in
                  ("remaining_cost_basis", "tp1_price", "tp2_price", "best_price")]
        extras.append("p.margin_allocated" if "margin_allocated" in position_columns else "NULL")
        progress("PORTFOLIO")
        portfolio = con.execute(
            """SELECT initial_capital,cash,realized_pnl,total_fees
               FROM paper_agent_portfolio WHERE portfolio_id=1"""
        ).fetchone()
        progress("OPEN_POSITIONS")
        rows = con.execute(
            f"""SELECT p.position_id,p.symbol,p.market_type,p.direction,p.entry_time,
                      p.entry_price,p.quantity,p.notional,p.leverage,p.selection_reason,
                      p.decision_source,p.decision_reference_id,
                      m.stop_distance_pct,m.current_stop_price,m.tp1_done,m.tp2_done,
                      m.remaining_quantity,{",".join(extras)}
               FROM paper_agent_positions p
               LEFT JOIN paper_position_management m USING(position_id)
               WHERE p.status='OPEN' ORDER BY p.position_id"""
        ).fetchall()
        progress("ACTIVITY")
        activity = {}
        # These tables are append-only audit streams with INTEGER PRIMARY KEY
        # identifiers. The panel needs the last persisted activity, not a
        # global timestamp sort across large tables.
        for table, id_column, time_column, label in (
            ("paper_behavior_selection_runs", "run_id", "created_at", "fırsat taraması"),
            ("paper_agent_positions", "position_id", "selected_at", "işlem seçimi"),
            ("paper_position_events", "event_id", "observed_at", "pozisyon yönetimi"),
            ("paper_agent_trade_learning_reviews", "review_id", "created_at", "öğrenme incelemesi"),
        ):
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                continue
            columns = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            if id_column not in columns or time_column not in columns:
                continue
            row = con.execute(
                f"SELECT {time_column} FROM {table} "
                f"ORDER BY {id_column} DESC LIMIT 1"
            ).fetchone()
            activity[label] = row[0] if row else None
        progress("LEARNING")
        learning_row = None
        has_learning = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_agent_trade_learning_reviews'"
        ).fetchone()
        if has_learning:
            learning_row = con.execute(
                """SELECT created_at,learning_version,validation_cohort,closed_count,
                          independent_weeks,status,promotion_allowed,evidence_json
                   FROM paper_agent_trade_learning_reviews
                   ORDER BY review_id DESC LIMIT 1"""
            ).fetchone()
        progress("SELECTION")
        selection = None
        has_selection = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_behavior_selection_runs'"
        ).fetchone()
        if has_selection:
            selection_columns = {
                row[1] for row in con.execute("PRAGMA table_info(paper_behavior_selection_runs)")
            }
            required = {
                "created_at", "core_status", "candidate_count", "selected_count",
                "active_position_count", "capacity", "available_exposure",
                "reason_counts_json",
            }
            if required.issubset(selection_columns):
                selection_row = con.execute(
                    """SELECT created_at,core_status,candidate_count,selected_count,
                              active_position_count,capacity,available_exposure,
                              reason_counts_json
                       FROM paper_behavior_selection_runs
                       ORDER BY run_id DESC LIMIT 1"""
                ).fetchone()
                if selection_row:
                    selection = {
                        "created_at": selection_row[0],
                        "core_status": selection_row[1],
                        "candidate_count": selection_row[2],
                        "selected_count": selection_row[3],
                        "active_position_count": selection_row[4],
                        "capacity": selection_row[5],
                        "available_exposure": selection_row[6],
                        "reason_counts": _safe_json_object(selection_row[7]),
                    }

        progress("REPORTS")
        reports = []
        has_reports = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_agent_daily_reports'"
        ).fetchone()
        if has_reports:
            columns = {row[1] for row in con.execute("PRAGMA table_info(paper_agent_daily_reports)")}
            if {"report_date", "created_at", "report_json"}.issubset(columns):
                for report_date, created_at, report_json in con.execute(
                    """SELECT report_date,created_at,report_json
                       FROM paper_agent_daily_reports
                       ORDER BY report_date DESC,created_at DESC LIMIT 30"""
                ).fetchall():
                    reports.append({
                        "report_date": report_date,
                        "created_at": created_at,
                        "report": _safe_json_object(report_json),
                    })
        progress("CLOSED_POSITIONS")
        closed_rows = con.execute(
            """SELECT position_id,symbol,market_type,direction,entry_time,entry_price,
                      exit_time,exit_price,quantity,net_pnl,close_reason,total_fees
               FROM (
                   SELECT position_id,symbol,market_type,direction,entry_time,entry_price,
                          exit_time,exit_price,quantity,net_pnl,close_reason,
                          entry_fee + exit_fee AS total_fees
                   FROM paper_agent_positions WHERE status='CLOSED'
               ) ORDER BY exit_time DESC,position_id DESC LIMIT 100"""
        ).fetchall()
    finally:
        con.close()

    progress("PROJECTION")
    market_rows = []
    for state in market.get("live_market", []):
        freshness = _freshness_seconds(state, now)
        market_rows.append({
            "symbol": state.get("symbol"),
            "market_type": state.get("market_type"),
            "last_price": state.get("last_price"),
            "best_bid": state.get("best_bid"),
            "best_ask": state.get("best_ask"),
            "quote_source": state.get("quote_source"),
            "freshness_seconds": freshness,
            "market_status": "CANLI" if freshness is not None and freshness <= 5.0 else "GECİKMİŞ",
            "book_imbalance": state.get("book_imbalance"),
            "taker_imbalance": state.get("taker_imbalance"),
            "visible_quote_volume": state.get("visible_quote_volume"),
        })
    market_rows.sort(key=lambda row: (row["symbol"] or "", row["market_type"] or ""))

    positions = []
    total_unrealized = 0.0
    for row in rows:
        (position_id, symbol, market_type, direction, entry_time, entry_price,
         quantity, notional, leverage, reason, decision_source, decision_reference_id,
         stop_distance_pct, current_stop_price, tp1_done, tp2_done,
         remaining_quantity, remaining_basis, frozen_tp1, frozen_tp2, best_price, margin) = row
        state = states.get((symbol, market_type), {})
        mark = _mark_price(direction, state)
        freshness = _freshness_seconds(state, now)
        live_quantity = remaining_quantity if remaining_quantity is not None else quantity
        validated_mark, mark_error = validate_quote(MarketSnapshot(
            reference_price=state.get("last_price") or 0,
            available_quote_volume=state.get("visible_quote_volume"),
            observed_at=now.isoformat(), best_bid=state.get("best_bid"),
            best_ask=state.get("best_ask"), quote_timestamp_ms=state.get("quote_timestamp_ms"),
            market_type=market_type, source=state.get("quote_source"),
        ), "SELL" if direction == "LONG" else "BUY")
        valid_mark = validated_mark is not None
        pnl = _live_pnl(direction, entry_price, live_quantity, mark) if valid_mark else None
        if pnl is not None:
            total_unrealized += pnl
        freshness = _freshness_seconds(state, now)
        positions.append({
            "position_id": position_id,
            "symbol": symbol,
            "market_type": market_type,
            "direction": direction,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "last_price": state.get("last_price"),
            "best_bid": state.get("best_bid"),
            "best_ask": state.get("best_ask"),
            "mark_price": mark,
            "quantity": live_quantity,
            "remaining_cost_basis": remaining_basis if remaining_basis is not None else margin,
            "valuation_error": mark_error,
            "gross_exposure": abs(float(live_quantity) * float(mark)) if valid_mark else None,
            "best_price": best_price,
            "trailing_active": bool(tp1_done),
            "notional": notional,
            "leverage": leverage,
            "unrealized_pnl": pnl,
            "quote_source": state.get("quote_source"),
            "freshness_seconds": freshness,
            "market_status": (
                "CANLI" if freshness is not None and freshness <= 5.0
                else "GECİKMİŞ"
            ),
            "decision_reason": reason,
            "decision_source": decision_source,
            "decision_reference_id": decision_reference_id,
            "decision_flow": {
                "observation": {
                    "status": "CANLI" if freshness is not None and freshness <= 5.0 else "GECİKMİŞ",
                    "source": state.get("quote_source"),
                    "last_price": state.get("last_price"),
                    "book_imbalance": state.get("book_imbalance"),
                    "taker_imbalance": state.get("taker_imbalance"),
                },
                "opportunity": {"reason": reason},
                "decision": {
                    "direction": direction,
                    "source": decision_source,
                    "reference_id": decision_reference_id,
                },
                "execution": {
                    "status": "AÇIK",
                    "entry_price": entry_price,
                    "mark_price": mark,
                    "unrealized_pnl": pnl,
                },
            },
            "current_stop_price": current_stop_price,
            "stop_distance_pct": stop_distance_pct,
            "tp1_price": frozen_tp1 if frozen_tp1 is not None else (
                float(entry_price) * (1 + float(stop_distance_pct) * TP1_R / 100)
                if direction == "LONG" and entry_price is not None and stop_distance_pct is not None
                else float(entry_price) * (1 - float(stop_distance_pct) * TP1_R / 100)
                if entry_price is not None and stop_distance_pct is not None else None
            ),
            "tp2_price": frozen_tp2 if frozen_tp2 is not None else (
                float(entry_price) * (1 + float(stop_distance_pct) * TP2_R / 100)
                if direction == "LONG" and entry_price is not None and stop_distance_pct is not None
                else float(entry_price) * (1 - float(stop_distance_pct) * TP2_R / 100)
                if entry_price is not None and stop_distance_pct is not None else None
            ),
            "tp1_done": bool(tp1_done),
            "tp2_done": bool(tp2_done),
            "remaining_quantity": remaining_quantity,
        })

    initial, cash, realized, fees = portfolio or (0.0, 0.0, 0.0, 0.0)
    open_notional = sum(float(p["notional"] or 0.0) for p in positions)
    valuation_complete = portfolio is not None and all(
        p["unrealized_pnl"] is not None and p["remaining_cost_basis"] is not None for p in positions)
    reserved = sum(float(p["remaining_cost_basis"] or 0) for p in positions)
    total_equity = float(cash) + reserved + total_unrealized if valuation_complete else None
    if not valuation_complete:
        total_unrealized = None
    fresh_count = sum(p["market_status"] == "CANLI" for p in positions)
    try:
        health_at = _parse_iso(health.get("at"))
    except (TypeError, ValueError):
        health_at = None
    heartbeat_age = (
        max(0.0, (now - health_at).total_seconds()) if health_at else None
    )
    worker_status = health.get("status") or "UNKNOWN"
    worker_healthy = worker_status == "CONNECTED" and (
        heartbeat_age is not None and heartbeat_age <= 30.0
    )
    market_states = list(market.get("live_market") or [])
    market_fresh = sum(
        1 for state in market_states
        if _freshness_seconds(state, now) is not None
        and _freshness_seconds(state, now) <= 5.0
    )
    observed_symbols = len(market_states)
    # Individual symbol quotes can legitimately stay unchanged for several
    # seconds on quiet markets. Global feed health must therefore follow the
    # canonical market telemetry snapshot timestamp, not require every symbol
    # to emit a quote inside the same 5-second window.
    try:
        market_at = _parse_iso(market.get("at"))
    except (TypeError, ValueError):
        market_at = None
    market_snapshot_age = (
        max(0.0, (now - market_at).total_seconds()) if market_at else None
    )
    # A freshly rewritten JSON file is not proof of fresh exchange data.
    # Require at least one recent quote. Quiet symbols remain independently
    # classified by their own quote age; do not require all symbols to tick.
    market_feed_fresh = (
        market_snapshot_age is not None and market_snapshot_age <= 30.0
        and market_fresh > 0
    )
    activity_ages = {}
    for name, stamp in activity.items():
        try:
            parsed = _parse_iso(stamp)
        except (TypeError, ValueError):
            parsed = None
        activity_ages[name] = max(0.0, (now - parsed).total_seconds()) if parsed else None
    latest_work_age = min(
        (age for age in activity_ages.values() if age is not None),
        default=None,
    )
    # A healthy heartbeat is not enough: the canonical decision loop must
    # itself keep producing fresh brain/scan telemetry.
    try:
        brain_at = _parse_iso(brain.get("at"))
    except (TypeError, ValueError):
        brain_at = None
    brain_age = max(0.0, (now - brain_at).total_seconds()) if brain_at else None
    scan_fresh = brain_age is not None and brain_age <= 15.0
    last_action = str(health.get("action") or "").upper()
    healthy_hold = last_action in {"HOLD", "NO_ACTION", "NO_OPPORTUNITY"}
    work_stalled = (
        worker_healthy
        and observed_symbols > 0
        and market_feed_fresh
        and (
            not scan_fresh
            or (latest_work_age is not None and latest_work_age > 300.0 and not healthy_hold)
        )
    )
    pipeline_state = (
        "İŞ ÜRETMİYOR"
        if work_stalled
        else "ÇALIŞIYOR"
        if worker_healthy and observed_symbols > 0 and market_feed_fresh
        else "VERİ GECİKİYOR"
        if worker_healthy and observed_symbols > 0
        else "PİYASA AKIŞI YOK"
        if worker_healthy
        else "WORKER SORUNLU"
    )
    return {
        "system": {
            "execution_mode": "PAPER_ONLY",
            "real_order_allowed": False,
            "open_positions": len(positions),
            "live_positions": fresh_count,
            "market_data_status": (
                "CANLI" if positions and fresh_count == len(positions)
                else "GECİKMİŞ" if positions else "POZİSYON_YOK"
            ),
            "worker_status": worker_status,
            "worker_healthy": worker_healthy,
            "heartbeat_age_seconds": heartbeat_age,
            "last_action": health.get("action"),
            "healthy_hold": healthy_hold,
            "active_symbols": health.get("active_symbols"),
            "pipeline_state": pipeline_state,
            "observed_symbols": observed_symbols,
            "fresh_market_symbols": market_fresh,
            "market_snapshot_age_seconds": market_snapshot_age,
            "market_feed_fresh": market_feed_fresh,
            "latest_work_age_seconds": latest_work_age,
            "brain_scan_age_seconds": brain_age,
            "brain_scan_fresh": scan_fresh,
            "pipeline_explanation": (
                "Worker, canlı piyasa akışı ve gerçek AGCA aktivitesi sağlıklı."
                if pipeline_state == "ÇALIŞIYOR"
                else "Worker/piyasa canlı ancak kanonik AGCA tarama döngüsü güncel değil."
                if pipeline_state == "İŞ ÜRETMİYOR"
                else "Worker çalışıyor ancak piyasa verisinin bir bölümü güncel değil."
                if pipeline_state == "VERİ GECİKİYOR"
                else "Worker çalışıyor fakat canlı piyasa akışı görünmüyor."
                if pipeline_state == "PİYASA AKIŞI YOK"
                else "Kanonik worker heartbeat sağlıklı değil."
            ),
        },
        "visibility": visibility,
        "brain": brain,
        "brain_timeline": brain_timeline,
        "selection": selection,
        "activity": {
            "stages": [{
                "name": name,
                "last_at": stamp,
                "age_seconds": (
                    max(0.0, (now - _parse_iso(stamp)).total_seconds())
                    if stamp else None
                ),
            } for name, stamp in activity.items()],
        },
        "portfolio": {
            "valuation_complete": valuation_complete,
            "reserved_margin": reserved if valuation_complete else None,
            "gross_exposure": sum(p["gross_exposure"] for p in positions) if valuation_complete else None,
            "initial_capital": initial,
            "cash": cash,
            "realized_pnl": realized,
            "total_fees": fees,
            "unrealized_pnl": total_unrealized,
            "open_position_notional": open_notional,
            "total_equity": total_equity,
            "total_return_pct": (
                ((total_equity / float(initial)) - 1.0) * 100.0
                if initial and total_equity is not None else None
            ),
        },
        "positions": positions,
        "market": market_rows,
        "learning": ({
            "created_at": learning_row[0],
            "learning_version": learning_row[1],
            "validation_cohort": learning_row[2],
            "closed_count": learning_row[3],
            "independent_weeks": learning_row[4],
            "status": learning_row[5],
            "promotion_allowed": bool(learning_row[6]),
            "evidence": _safe_json_object(learning_row[7]),
        } if learning_row else None),
        "risk": {
            "execution_mode": "PAPER_ONLY",
            "real_order_allowed": False,
            "open_positions": len(positions),
            "stale_positions": len(positions) - fresh_count,
            "positions": [{
                "position_id": p["position_id"],
                "symbol": p["symbol"],
                "direction": p["direction"],
                "leverage": p["leverage"],
                "current_stop_price": p["current_stop_price"],
                "remaining_quantity": p["remaining_quantity"],
                "market_status": p["market_status"],
            } for p in positions],
        },
        "incidents": ([{
            "severity": "KRİTİK" if pipeline_state in {"WORKER SORUNLU", "PİYASA AKIŞI YOK", "İŞ ÜRETMİYOR"} else "UYARI",
            "kind": "PIPELINE_VISIBILITY",
            "message": (
                "Worker/piyasa canlı ancak AGCA fırsat taraması durmuş veya gecikmiş"
                if pipeline_state == "İŞ ÜRETMİYOR"
                else "Sistem çalışıyor görünse de canlı piyasa akışı yok"
                if pipeline_state == "PİYASA AKIŞI YOK"
                else "Canlı piyasa verisinin bir bölümü gecikiyor"
                if pipeline_state == "VERİ GECİKİYOR"
                else "Kanonik PAPER worker sağlıklı değil"
            ),
            "status": pipeline_state,
            "age_seconds": heartbeat_age,
        }] if pipeline_state != "ÇALIŞIYOR" else []) + [{
            "severity": "OPERATÖR",
            "kind": "MARKET_DATA_MISMATCH",
            "message": (
                f'{row.get("symbol", "—")} için piyasa verisi uyuşmazlığı bildirildi'
            ),
            "status": "TEŞHİS BEKLİYOR",
            "age_seconds": None,
            "feedback_id": row.get("feedback_id"),
            "created_at": row.get("created_at"),
            "operator_price": row.get("operator_price"),
            "agent_price": row.get("agent_price"),
            "price_difference": (
                float(row["operator_price"]) - float(row["agent_price"])
                if row.get("operator_price") is not None and row.get("agent_price") is not None
                else None
            ),
            "price_difference_pct": (
                ((float(row["operator_price"]) / float(row["agent_price"])) - 1.0) * 100.0
                if row.get("operator_price") is not None
                and row.get("agent_price") not in (None, 0, 0.0)
                else None
            ),
            "agent_quote_source": row.get("agent_quote_source"),
            "note": row.get("note"),
        } for row in feedback] + ([{
            "severity": "UYARI",
            "kind": "WORKER_HEALTH",
            "message": "Kanonik PAPER worker sağlıklı görünmüyor",
            "status": worker_status,
            "age_seconds": heartbeat_age,
        }] if not worker_healthy else []) + ([{
            "severity": "UYARI",
            "kind": "STALE_MARKET_DATA",
            "message": f"{len(positions) - fresh_count} açık pozisyonda piyasa verisi gecikmiş",
            "status": "GECİKMİŞ",
            "age_seconds": None,
        }] if len(positions) - fresh_count else []),
        "reports": reports,
        "closed_positions": [{
            "position_id": r[0], "symbol": r[1], "market_type": r[2],
            "direction": r[3], "entry_time": r[4], "entry_price": r[5],
            "exit_time": r[6], "exit_price": r[7], "quantity": r[8],
            "net_pnl": r[9], "close_reason": r[10], "total_fees": r[11],
        } for r in closed_rows],
    }
