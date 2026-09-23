"""Immutable local PAPER outcomes for exact-minute behavior/event matches."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from database.market_behavior_store import EXTERNAL_EVIDENCE_TABLE, TABLE


OUTCOME_TABLE = "behavior_event_forward_outcomes"
OUTCOME_CONTRACT_VERSION = "BEHAVIOR_EVENT_FORWARD_OUTCOME_V2_MARKET_STRUCTURE"
FIXED_HORIZON_MINUTES = (1, 5, 15, 60, 240, 1440)
MINUTE_MS = 60_000


def ensure_outcome_table(connection):
    connection.execute(f"""
        CREATE TABLE IF NOT EXISTS {OUTCOME_TABLE} (
            event_id TEXT NOT NULL,
            event_minute_end_timestamp_ms INTEGER NOT NULL,
            independence_unit TEXT NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            event_occurred_at TEXT NOT NULL,
            event_available_at TEXT NOT NULL,
            event_direction TEXT NOT NULL,
            instrument TEXT NOT NULL,
            horizon_minutes INTEGER NOT NULL
                CHECK(horizon_minutes IN (1,5,15,60,240,1440)),
            baseline_minute_end_timestamp_ms INTEGER NOT NULL,
            outcome_minute_end_timestamp_ms INTEGER NOT NULL,
            outcome_available_at TEXT NOT NULL,
            pre_event_mid_price REAL NOT NULL,
            event_minute_mid_price REAL NOT NULL,
            outcome_mid_price REAL NOT NULL,
            return_from_pre_event_pct REAL NOT NULL,
            book_imbalance_20_mean REAL NOT NULL,
            taker_imbalance_snapshot_mean REAL NOT NULL,
            spread_bps_mean REAL NOT NULL,
            open_interest_usd_change REAL NOT NULL,
            funding_rate_last REAL NOT NULL,
            long_liquidation_notional_usd_estimate REAL NOT NULL,
            short_liquidation_notional_usd_estimate REAL NOT NULL,
            liquidation_event_count INTEGER NOT NULL,
            contract_version TEXT NOT NULL,
            settled_at TEXT NOT NULL,
            PRIMARY KEY(
                event_id,event_minute_end_timestamp_ms,instrument,horizon_minutes
            )
        )
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_behavior_event_outcome_horizon
        ON {OUTCOME_TABLE}(kind,horizon_minutes,outcome_minute_end_timestamp_ms)
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_behavior_event_outcome_independence
        ON {OUTCOME_TABLE}(independence_unit,horizon_minutes)
    """)
    connection.execute(f"""
        CREATE INDEX IF NOT EXISTS ix_behavior_event_outcome_learning_cutoff
        ON {OUTCOME_TABLE}(
            horizon_minutes,outcome_available_at,kind,independence_unit
        )
    """)


def _applies(symbols_json, instrument):
    try:
        symbols = [str(value).upper() for value in json.loads(symbols_json or "[]")]
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    base_symbol = str(instrument).split("-")[0].upper()
    return not symbols or base_symbol in symbols


def _settle_market_structure_outcome(connection, event_minute, outcome_minute,
                                     outcome_available_at, settled_at):
    """Persist one non-overlapping, outcome-blind endogenous 4h setup sample."""
    horizon = 240
    if (event_minute // MINUTE_MS) % horizon != 0:
        return 0
    baseline_minute = event_minute - MINUTE_MS
    row = connection.execute(f"""
        SELECT event.instrument,event.mid_price_last,
               event.book_imbalance_20_mean,
               event.taker_imbalance_snapshot_mean,event.spread_bps_mean,
               event.open_interest_usd_change,event.funding_rate_last,
               event.long_liquidation_notional_usd_estimate,
               event.short_liquidation_notional_usd_estimate,
               event.liquidation_event_count,baseline.mid_price_last,
               outcome.mid_price_last
        FROM {TABLE} event
        JOIN {TABLE} baseline
          ON baseline.instrument=event.instrument
         AND baseline.minute_end_timestamp_ms=?
        JOIN {TABLE} outcome
          ON outcome.instrument=event.instrument
         AND outcome.minute_end_timestamp_ms=?
        WHERE event.minute_end_timestamp_ms=?
          AND event.eligible_for_learning=1
          AND baseline.eligible_for_learning=1
          AND outcome.eligible_for_learning=1
          AND event.mid_price_last>0 AND baseline.mid_price_last>0
          AND outcome.mid_price_last>0
          AND event.book_imbalance_20_mean!=0
          AND event.taker_imbalance_snapshot_mean!=0
        ORDER BY (ABS(event.book_imbalance_20_mean)
                  + ABS(event.taker_imbalance_snapshot_mean)) DESC,
                 event.instrument
        LIMIT 1
    """, (baseline_minute, outcome_minute, event_minute)).fetchone()
    if not row:
        return 0
    (instrument, event_price, book_imbalance, taker_imbalance, spread_bps,
     oi_change, funding_rate, long_liquidation, short_liquidation,
     liquidation_count, baseline_price, outcome_price) = row
    event_time = datetime.fromtimestamp(event_minute / 1000, tz=timezone.utc).isoformat()
    event_id = f"market_structure:{event_minute}"
    result = connection.execute(f"""
        INSERT INTO {OUTCOME_TABLE}(
            event_id,event_minute_end_timestamp_ms,independence_unit,
            kind,source,event_occurred_at,event_available_at,event_direction,
            instrument,horizon_minutes,baseline_minute_end_timestamp_ms,
            outcome_minute_end_timestamp_ms,outcome_available_at,
            pre_event_mid_price,event_minute_mid_price,outcome_mid_price,
            return_from_pre_event_pct,book_imbalance_20_mean,
            taker_imbalance_snapshot_mean,spread_bps_mean,
            open_interest_usd_change,funding_rate_last,
            long_liquidation_notional_usd_estimate,
            short_liquidation_notional_usd_estimate,liquidation_event_count,
            contract_version,settled_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(event_id,event_minute_end_timestamp_ms,instrument,horizon_minutes)
        DO NOTHING
    """, (
        event_id, event_minute, event_id, "MARKET_STRUCTURE",
        "AGCA_MARKET_BEHAVIOR", event_time, event_time, "OBSERVED",
        instrument, horizon, baseline_minute, outcome_minute,
        outcome_available_at, baseline_price, event_price, outcome_price,
        (outcome_price / baseline_price - 1.0) * 100.0,
        book_imbalance, taker_imbalance, spread_bps, oi_change, funding_rate,
        long_liquidation, short_liquidation, liquidation_count,
        OUTCOME_CONTRACT_VERSION, settled_at,
    ))
    return max(0, result.rowcount)


def backfill_market_structure_outcomes(connection):
    """Reproduce all causally closed non-overlapping samples in retained history."""
    ensure_outcome_table(connection)
    settled_at = datetime.now(timezone.utc).isoformat()
    outcome_minutes = [row[0] for row in connection.execute(f"""
        SELECT DISTINCT outcome.minute_end_timestamp_ms FROM {TABLE} outcome
        WHERE outcome.eligible_for_learning=1
          AND ((outcome.minute_end_timestamp_ms / {MINUTE_MS}) - 239) % 240 = 0
          AND EXISTS (SELECT 1 FROM {TABLE} event
              WHERE event.instrument=outcome.instrument
                AND event.minute_end_timestamp_ms=
                    outcome.minute_end_timestamp_ms - 239 * {MINUTE_MS}
                AND event.eligible_for_learning=1)
          AND EXISTS (SELECT 1 FROM {TABLE} baseline
              WHERE baseline.instrument=outcome.instrument
                AND baseline.minute_end_timestamp_ms=
                    outcome.minute_end_timestamp_ms - 240 * {MINUTE_MS}
                AND baseline.eligible_for_learning=1)
        ORDER BY outcome.minute_end_timestamp_ms
    """).fetchall()]
    inserted = 0
    for outcome_minute in outcome_minutes:
        event_minute = int(outcome_minute) - 239 * MINUTE_MS
        available_at = datetime.fromtimestamp(
            int(outcome_minute) / 1000, tz=timezone.utc
        ).isoformat()
        inserted += _settle_market_structure_outcome(
            connection, event_minute, int(outcome_minute), available_at, settled_at
        )
    connection.commit()
    return {"examined": len(outcome_minutes), "inserted": inserted}


def settle_outcomes_for_closed_minute(connection, closed_minute_end_timestamp_ms):
    """Settle only horizons becoming causally available at this closed minute."""
    ensure_outcome_table(connection)
    outcome_minute = int(closed_minute_end_timestamp_ms)
    outcome_available_at = datetime.fromtimestamp(
        outcome_minute / 1000, tz=timezone.utc
    ).isoformat()
    settled_at = datetime.now(timezone.utc).isoformat()
    inserted = 0
    by_horizon = {}
    for horizon in FIXED_HORIZON_MINUTES:
        event_minute = outcome_minute - (horizon - 1) * MINUTE_MS
        baseline_minute = event_minute - MINUTE_MS
        rows = connection.execute(f"""
            SELECT e.event_id,e.kind,e.source,e.occurred_at,e.available_at,
                   e.direction,e.symbols_json,
                   event.instrument,event.mid_price_last,
                   event.book_imbalance_20_mean,
                   event.taker_imbalance_snapshot_mean,event.spread_bps_mean,
                   event.open_interest_usd_change,event.funding_rate_last,
                   event.long_liquidation_notional_usd_estimate,
                   event.short_liquidation_notional_usd_estimate,
                   event.liquidation_event_count,baseline.mid_price_last,
                   outcome.mid_price_last
            FROM {EXTERNAL_EVIDENCE_TABLE} e
            JOIN {TABLE} event
              ON event.minute_end_timestamp_ms=e.minute_end_timestamp_ms
            JOIN {TABLE} baseline
              ON baseline.instrument=event.instrument
             AND baseline.minute_end_timestamp_ms=?
            JOIN {TABLE} outcome
              ON outcome.instrument=event.instrument
             AND outcome.minute_end_timestamp_ms=?
            WHERE e.minute_end_timestamp_ms=?
              AND event.eligible_for_learning=1
              AND baseline.eligible_for_learning=1
              AND outcome.eligible_for_learning=1
              AND event.mid_price_last>0
              AND baseline.mid_price_last>0
              AND outcome.mid_price_last>0
        """, (baseline_minute, outcome_minute, event_minute)).fetchall()
        horizon_inserted = 0
        for row in rows:
            (event_id, kind, source, event_occurred_at, event_available_at,
             event_direction, symbols_json,
             instrument, event_price, book_imbalance, taker_imbalance,
             spread_bps, oi_change, funding_rate, long_liquidation,
             short_liquidation, liquidation_count, baseline_price,
             outcome_price) = row
            if not _applies(symbols_json, instrument):
                continue
            independence_unit = f"{event_id}:{event_minute}"
            result = connection.execute(f"""
                INSERT INTO {OUTCOME_TABLE}(
                    event_id,event_minute_end_timestamp_ms,independence_unit,
                    kind,source,event_occurred_at,event_available_at,
                    event_direction,instrument,horizon_minutes,
                    baseline_minute_end_timestamp_ms,
                    outcome_minute_end_timestamp_ms,outcome_available_at,
                    pre_event_mid_price,event_minute_mid_price,outcome_mid_price,
                    return_from_pre_event_pct,book_imbalance_20_mean,
                    taker_imbalance_snapshot_mean,spread_bps_mean,
                    open_interest_usd_change,funding_rate_last,
                    long_liquidation_notional_usd_estimate,
                    short_liquidation_notional_usd_estimate,
                    liquidation_event_count,contract_version,settled_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(
                    event_id,event_minute_end_timestamp_ms,instrument,horizon_minutes
                ) DO NOTHING
            """, (
                event_id, event_minute, independence_unit, kind, source,
                event_occurred_at, event_available_at, event_direction,
                instrument, horizon, baseline_minute,
                outcome_minute, outcome_available_at, baseline_price, event_price,
                outcome_price, (outcome_price / baseline_price - 1.0) * 100.0,
                book_imbalance, taker_imbalance, spread_bps, oi_change,
                funding_rate, long_liquidation, short_liquidation,
                liquidation_count, OUTCOME_CONTRACT_VERSION, settled_at,
            ))
            horizon_inserted += max(0, result.rowcount)
        if horizon == 240:
            horizon_inserted += _settle_market_structure_outcome(
                connection, event_minute, outcome_minute,
                outcome_available_at, settled_at,
            )
        if horizon_inserted:
            by_horizon[str(horizon)] = horizon_inserted
            inserted += horizon_inserted
    total = connection.execute(
        f"SELECT COUNT(*) FROM {OUTCOME_TABLE}"
    ).fetchone()[0]
    independent_units = connection.execute(
        f"SELECT COUNT(DISTINCT independence_unit) FROM {OUTCOME_TABLE}"
    ).fetchone()[0]
    return {
        "contract_version": OUTCOME_CONTRACT_VERSION,
        "settled": inserted,
        "settled_by_horizon": by_horizon,
        "outcome_row_count": total,
        "independent_event_count": independent_units,
        "remote_mirror_enabled_for_table": False,
    }
