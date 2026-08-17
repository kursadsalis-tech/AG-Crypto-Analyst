"""Forward-only validation for Decision Engine shadow candidates.

Only one open prediction per symbol is allowed.  This prevents a stable
15-minute signal from being counted four times inside the same one-hour
horizon.  Outcomes never modify the frozen shadow policy automatically.
"""
from __future__ import annotations

import json
import sqlite3
from database.runtime_store import connect as runtime_connect
from datetime import timedelta

from analysis import forward_shadow_validator

from decision_engine import decision_engine


DB_NAME = decision_engine.DB_NAME
ENGINE_VERSION = decision_engine.ENGINE_VERSION
POLICY_VERSION = decision_engine.POLICY_VERSION
HORIZON_HOURS = 1


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS decision_shadow_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_snapshot_id INTEGER NOT NULL UNIQUE,
            engine_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            symbol TEXT NOT NULL,
            coin TEXT NOT NULL,
            action TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            due_time TEXT NOT NULL,
            entry_price REAL NOT NULL,
            max_position_pct REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_time TEXT,
            exit_price REAL,
            return_pct REAL,
            directional_return REAL,
            hit INTEGER,
            created_at TEXT NOT NULL,
            settled_at TEXT,
            detail_json TEXT NOT NULL,
            FOREIGN KEY(decision_snapshot_id)
                REFERENCES decision_engine_snapshots(id)
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_decision_shadow_open_due
        ON decision_shadow_predictions(status, due_time)
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS ix_decision_shadow_symbol_time
        ON decision_shadow_predictions(symbol, entry_time)
    """)
    con.commit()


def open_symbol(con, symbol):
    return con.execute("""
        SELECT 1
        FROM decision_shadow_predictions
        WHERE engine_version=?
          AND policy_version=?
          AND symbol=?
          AND status='OPEN'
        LIMIT 1
    """, (ENGINE_VERSION, POLICY_VERSION, symbol)).fetchone() is not None


def decision_snapshot_id(con, run_id, symbol):
    row = con.execute("""
        SELECT id
        FROM decision_engine_snapshots
        WHERE run_id=? AND symbol=?
        LIMIT 1
    """, (run_id, symbol)).fetchone()
    return int(row[0]) if row else None


def record_new_predictions(con, run_id, decisions, now=None):
    now = now or forward_shadow_validator.utcnow()
    created = 0
    skipped_open = 0
    source_errors = {}
    for item in decisions:
        if item["shadow_action"] not in {"AL", "SAT"}:
            continue
        if item["risk_decision"] != "ALLOW":
            continue
        symbol = item["symbol"]
        if open_symbol(con, symbol):
            skipped_open += 1
            continue
        snapshot_id = decision_snapshot_id(con, run_id, symbol)
        if snapshot_id is None:
            source_errors[symbol] = "DECISION_SNAPSHOT_MISSING"
            continue
        try:
            entry_price = forward_shadow_validator.current_price(symbol)
        except Exception as exc:
            source_errors[symbol] = str(exc)
            continue
        if entry_price <= 0:
            source_errors[symbol] = "INVALID_ENTRY_PRICE"
            continue

        due = now + timedelta(hours=HORIZON_HOURS)
        detail = {
            "decision_snapshot_id": snapshot_id,
            "run_id": run_id,
            "symbol": symbol,
            "coin": item["coin"],
            "action": item["shadow_action"],
            "entry_time": forward_shadow_validator.iso(now),
            "due_time": forward_shadow_validator.iso(due),
            "entry_price": entry_price,
            "risk_decision": item["risk_decision"],
            "max_position_pct": item["max_position_pct"],
            "decision_reasons": item["reasons"],
            "policy_note": (
                "Research-only shadow prediction. It is not an alert, trade "
                "instruction or production decision."
            ),
        }
        con.execute("""
            INSERT OR IGNORE INTO decision_shadow_predictions (
                decision_snapshot_id, engine_version, policy_version,
                symbol, coin, action, entry_time, due_time, entry_price,
                max_position_pct, status, created_at, detail_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
        """, (
            snapshot_id,
            ENGINE_VERSION,
            POLICY_VERSION,
            symbol,
            item["coin"],
            item["shadow_action"],
            forward_shadow_validator.iso(now),
            forward_shadow_validator.iso(due),
            entry_price,
            item["max_position_pct"],
            forward_shadow_validator.iso(now),
            json.dumps(detail, ensure_ascii=False),
        ))
        if con.execute("SELECT changes()").fetchone()[0]:
            created += 1
    con.commit()
    return {
        "created": created,
        "skipped_open": skipped_open,
        "source_errors": source_errors,
    }


def settle_due(con, now=None):
    now = now or forward_shadow_validator.utcnow()
    rows = con.execute("""
        SELECT id, symbol, action, entry_price, due_time
        FROM decision_shadow_predictions
        WHERE engine_version=?
          AND policy_version=?
          AND status='OPEN'
          AND due_time<=?
        ORDER BY due_time
    """, (
        ENGINE_VERSION,
        POLICY_VERSION,
        forward_shadow_validator.iso(now),
    )).fetchall()
    settled = 0
    source_errors = {}
    for prediction_id, symbol, action, entry_price, due_raw in rows:
        due = forward_shadow_validator.parse_dt(due_raw)
        try:
            exit_price, exit_time = forward_shadow_validator.price_at_due(
                symbol,
                due,
            )
        except Exception as exc:
            source_errors[symbol] = str(exc)
            continue
        if exit_price is None or exit_price <= 0:
            source_errors[symbol] = "EXIT_PRICE_MISSING"
            continue
        return_pct = (
            (float(exit_price) / float(entry_price)) - 1.0
        ) * 100.0
        directional_return = (
            return_pct if action == "AL" else -return_pct
        )
        hit = int(directional_return > 0.0)
        con.execute("""
            UPDATE decision_shadow_predictions
            SET status='SETTLED', exit_time=?, exit_price=?, return_pct=?,
                directional_return=?, hit=?, settled_at=?
            WHERE id=? AND status='OPEN'
        """, (
            forward_shadow_validator.iso(exit_time),
            exit_price,
            return_pct,
            directional_return,
            hit,
            forward_shadow_validator.iso(now),
            prediction_id,
        ))
        settled += 1
    con.commit()
    return {
        "settled": settled,
        "source_errors": source_errors,
    }


def performance(con):
    rows = {}
    for label, condition in (
        ("ALL", "1=1"),
        ("AL", "action='AL'"),
        ("SAT", "action='SAT'"),
    ):
        settled, hits, avg_return = con.execute(f"""
            SELECT COUNT(*), COALESCE(SUM(hit), 0),
                   COALESCE(AVG(directional_return), 0)
            FROM decision_shadow_predictions
            WHERE engine_version=?
              AND policy_version=?
              AND status='SETTLED'
              AND {condition}
        """, (ENGINE_VERSION, POLICY_VERSION)).fetchone()
        open_count = con.execute(f"""
            SELECT COUNT(*)
            FROM decision_shadow_predictions
            WHERE engine_version=?
              AND policy_version=?
              AND status='OPEN'
              AND {condition}
        """, (ENGINE_VERSION, POLICY_VERSION)).fetchone()[0]
        settled = int(settled or 0)
        hits = int(hits or 0)
        rows[label] = {
            "settled": settled,
            "open": int(open_count or 0),
            "hits": hits,
            "hit_rate": hits / settled * 100.0 if settled else 0.0,
            "directional_return": float(avg_return or 0.0),
        }
    return rows


def process(run_result, now=None):
    now = now or forward_shadow_validator.utcnow()
    con = runtime_connect(DB_NAME)
    try:
        ensure_table(con)
        settled = settle_due(con, now)
        recorded = record_new_predictions(
            con,
            run_result["run_id"],
            run_result["decisions"],
            now,
        )
        scores = performance(con)
    finally:
        con.close()

    print("DECISION ENGINE SHADOW VALIDATION | horizon=1h | overlap=BLOCKED")
    print(
        f"Bu çalışma: yeni={recorded['created']} | "
        f"sonuçlanan={settled['settled']} | "
        f"açık nedeniyle atlanan={recorded['skipped_open']} | "
        f"kaynak hatası={len(recorded['source_errors']) + len(settled['source_errors'])}"
    )
    for label in ("ALL", "AL", "SAT"):
        row = scores[label]
        print(
            f"{label:<3}: settled={row['settled']} | open={row['open']} | "
            f"hit={row['hit_rate']:.1f}% | "
            f"dir_return={row['directional_return']:+.3f}%"
        )
    print(
        "Not: Aynı coin için bir tahmin sonuçlanmadan yenisi açılmaz. "
        "Bu sonuçlar SHADOW_ONLY'dır ve ürünü otomatik olarak açmaz."
    )
    return {
        "recorded": recorded,
        "settled": settled,
        "performance": scores,
    }


if __name__ == "__main__":
    result = decision_engine.main()
    process(result)
