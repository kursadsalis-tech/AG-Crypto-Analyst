"""Analyze on-chain metric changes without overstating immature history."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


DB_NAME = "crypto_data.db"
TARGET_LOOKBACK = timedelta(hours=24)
MIN_READY_HOURS = 18.0
CHANGE_THRESHOLDS = {
    "defi_tvl_usd": 1.0,
    "mempool_tx_count": 25.0,
    "mempool_vsize": 25.0,
    "mempool_total_fee": 25.0,
    "fastest_fee": 25.0,
    "half_hour_fee": 25.0,
    "hour_fee": 25.0,
    "economy_fee": 25.0,
}


def parse_time(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def metric_keys(con):
    return con.execute("""
        SELECT DISTINCT chain, metric, unit
        FROM onchain_observations
        ORDER BY chain, metric
    """).fetchall()


def latest_row(con, chain, metric):
    return con.execute("""
        SELECT collected_at, value
        FROM onchain_observations
        WHERE chain=? AND metric=?
        ORDER BY collected_at DESC LIMIT 1
    """, (chain, metric)).fetchone()


def baseline_row(con, chain, metric, latest_at):
    target = latest_at - TARGET_LOOKBACK
    candidates = con.execute("""
        SELECT collected_at, value
        FROM onchain_observations
        WHERE chain=? AND metric=? AND collected_at<?
        ORDER BY collected_at DESC
    """, (chain, metric, latest_at.isoformat())).fetchall()
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda row: abs((parse_time(row[0]) - target).total_seconds()),
    )


def classify(metric, change_pct, ready):
    if not ready or change_pct is None:
        return "WARMING_UP"
    threshold = CHANGE_THRESHOLDS.get(metric, 1.0)
    if change_pct >= threshold:
        return "RISING"
    if change_pct <= -threshold:
        return "FALLING"
    return "STABLE"


def analyze(con):
    results = []
    for chain, metric, unit in metric_keys(con):
        latest = latest_row(con, chain, metric)
        if not latest:
            continue
        latest_at = parse_time(latest[0])
        latest_value = float(latest[1])
        baseline = baseline_row(con, chain, metric, latest_at)
        if not baseline:
            span_hours = 0.0
            baseline_value = None
            change_pct = None
        else:
            baseline_at = parse_time(baseline[0])
            baseline_value = float(baseline[1])
            span_hours = (latest_at - baseline_at).total_seconds() / 3600.0
            change_pct = (
                ((latest_value / baseline_value) - 1.0) * 100.0
                if baseline_value != 0 else None
            )
        valid_values = (
            baseline_value is not None
            and baseline_value > 0
            and latest_value > 0
            and change_pct is not None
        )
        ready = valid_values and MIN_READY_HOURS <= span_hours <= 30.0
        status = "NO_DATA" if not valid_values else classify(metric, change_pct, ready)
        results.append({
            "chain": chain,
            "metric": metric,
            "unit": unit,
            "latest": latest_value,
            "baseline": baseline_value,
            "change_pct": change_pct,
            "span_hours": span_hours,
            "status": status,
            "ready": ready,
        })
    return results


def shown_value(value, unit):
    if unit == "USD":
        return f"${value:,.0f}"
    return f"{value:,.0f} {unit}"


def main():
    con = sqlite3.connect(DB_NAME)
    try:
        results = analyze(con)
    finally:
        con.close()

    print("AG Crypto Analyst - On-chain Analyzer | hedef: ~24 saat (18-30h kabul)")
    if not results:
        print("Analiz edilecek on-chain gözlem yok.")
        return []

    for row in results:
        change = "n/a" if row["change_pct"] is None else f"{row['change_pct']:+.3f}%"
        print(
            f"{row['chain']:<10} | {row['metric']:<22} | "
            f"{shown_value(row['latest'], row['unit']):>18} | "
            f"span={row['span_hours']:.2f}h | change={change:>9} | {row['status']}"
        )
    ready_count = sum(row["ready"] for row in results)
    print(f"24h hazır metrik: {ready_count}/{len(results)}")
    if ready_count < len(results):
        print("Not: 18-30h karşılaştırma aralığına ulaşmayan metrikler karar/alarm sinyali değildir.")
    return results


if __name__ == "__main__":
    main()
