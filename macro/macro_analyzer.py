"""Build a transparent, provisional macro risk regime from stored FRED data."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone


DB_NAME = "crypto_data.db"


def load_series(con, series_id):
    return con.execute("""
        SELECT observation_date, value
        FROM macro_observations
        WHERE series_id=?
        ORDER BY observation_date ASC
    """, (series_id,)).fetchall()


def parse_date(value):
    return date.fromisoformat(value)


def at_or_before(rows, target):
    candidates = [row for row in rows if parse_date(row[0]) <= target]
    return candidates[-1] if candidates else None


def change_over_days(rows, days, basis_points=False):
    if not rows:
        return None
    latest_date, latest_value = rows[-1]
    baseline = at_or_before(rows, parse_date(latest_date) - timedelta(days=days))
    if not baseline:
        return None
    delta = float(latest_value) - float(baseline[1])
    if basis_points:
        return delta * 100.0
    return ((float(latest_value) / float(baseline[1])) - 1.0) * 100.0 if baseline[1] else None


def cpi_metrics(rows):
    if len(rows) < 14:
        return None
    latest_date, latest_value = rows[-1]
    latest_date = parse_date(latest_date)
    previous = rows[-2]
    year_ago = at_or_before(rows, latest_date - timedelta(days=365))
    prior_date = parse_date(previous[0])
    prior_year_ago = at_or_before(rows[:-1], prior_date - timedelta(days=365))
    if not year_ago or not prior_year_ago:
        return None
    yoy = ((float(latest_value) / float(year_ago[1])) - 1.0) * 100.0
    prior_yoy = ((float(previous[1]) / float(prior_year_ago[1])) - 1.0) * 100.0
    mom = ((float(latest_value) / float(previous[1])) - 1.0) * 100.0
    return {
        "date": latest_date.isoformat(),
        "value": float(latest_value),
        "mom_pct": mom,
        "yoy_pct": yoy,
        "yoy_change_pp": yoy - prior_yoy,
    }


def component_score(value, positive_threshold, negative_threshold):
    if value is None:
        return None
    if value >= positive_threshold:
        return 1
    if value <= negative_threshold:
        return -1
    return 0


def regime_label(score):
    if score >= 2:
        return "RISK_OFF"
    if score <= -2:
        return "RISK_ON"
    return "MIXED"


def analyze(con):
    data = {series_id: load_series(con, series_id) for series_id in (
        "DFF", "DGS10", "DTWEXBGS", "VIXCLS", "CPIAUCSL"
    )}
    if any(not rows for rows in data.values()):
        missing = [series_id for series_id, rows in data.items() if not rows]
        raise RuntimeError(f"eksik makro seriler: {', '.join(missing)}")

    metrics = {
        "fed_funds": {
            "date": data["DFF"][-1][0],
            "value": float(data["DFF"][-1][1]),
            "change_90d_bps": change_over_days(data["DFF"], 90, True),
        },
        "us10y": {
            "date": data["DGS10"][-1][0],
            "value": float(data["DGS10"][-1][1]),
            "change_30d_bps": change_over_days(data["DGS10"], 30, True),
        },
        "dollar": {
            "date": data["DTWEXBGS"][-1][0],
            "value": float(data["DTWEXBGS"][-1][1]),
            "change_30d_pct": change_over_days(data["DTWEXBGS"], 30),
        },
        "vix": {
            "date": data["VIXCLS"][-1][0],
            "value": float(data["VIXCLS"][-1][1]),
            "change_30d_pct": change_over_days(data["VIXCLS"], 30),
        },
        "cpi": cpi_metrics(data["CPIAUCSL"]),
    }

    components = {
        "fed_funds_90d": component_score(metrics["fed_funds"]["change_90d_bps"], 25.0, -25.0),
        "us10y_30d": component_score(metrics["us10y"]["change_30d_bps"], 25.0, -25.0),
        "dollar_30d": component_score(metrics["dollar"]["change_30d_pct"], 1.0, -1.0),
        "vix_level": component_score(metrics["vix"]["value"], 25.0, 18.0),
        "cpi_yoy_trend": component_score(
            metrics["cpi"]["yoy_change_pp"] if metrics["cpi"] else None,
            0.10,
            -0.10,
        ),
    }
    available = [value for value in components.values() if value is not None]
    score = sum(available)
    return {
        "metrics": metrics,
        "components": components,
        "score": score,
        "regime": regime_label(score),
        "coverage": len(available),
        "provisional": True,
    }


def ensure_snapshot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS macro_regime_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            regime TEXT NOT NULL,
            score INTEGER NOT NULL,
            component_coverage INTEGER NOT NULL,
            provisional INTEGER NOT NULL,
            detail_json TEXT NOT NULL
        )
    """)
    con.commit()


def save_snapshot(con, result):
    con.execute("""
        INSERT INTO macro_regime_snapshots (
            created_at, regime, score, component_coverage, provisional, detail_json
        ) VALUES (?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(timezone.utc).isoformat(),
        result["regime"],
        result["score"],
        result["coverage"],
        int(result["provisional"]),
        json.dumps(result, ensure_ascii=False),
    ))
    con.commit()


def fmt(value, suffix=""):
    return "n/a" if value is None else f"{value:+.2f}{suffix}"


def main():
    con = sqlite3.connect(DB_NAME)
    try:
        ensure_snapshot_table(con)
        result = analyze(con)
        save_snapshot(con, result)
    finally:
        con.close()

    m = result["metrics"]
    print("AG Crypto Analyst - Macro Analyzer v0.8.0")
    print(f"Fed Funds : {m['fed_funds']['value']:.2f}% ({m['fed_funds']['date']}) | 90d {fmt(m['fed_funds']['change_90d_bps'], ' bp')}")
    print(f"US 10Y    : {m['us10y']['value']:.2f}% ({m['us10y']['date']}) | 30d {fmt(m['us10y']['change_30d_bps'], ' bp')}")
    print(f"Broad USD : {m['dollar']['value']:.3f} ({m['dollar']['date']}) | 30d {fmt(m['dollar']['change_30d_pct'], '%')}")
    print(f"VIX       : {m['vix']['value']:.2f} ({m['vix']['date']}) | 30d {fmt(m['vix']['change_30d_pct'], '%')}")
    if m["cpi"]:
        print(
            f"CPI       : {m['cpi']['value']:.3f} ({m['cpi']['date']}) | "
            f"MoM {fmt(m['cpi']['mom_pct'], '%')} | YoY {m['cpi']['yoy_pct']:.2f}% | "
            f"YoY trend {fmt(m['cpi']['yoy_change_pp'], ' pp')}"
        )
    print(f"Bileşenler: {result['components']}")
    print(
        f"PROVISIONAL MACRO REGIME: {result['regime']} | "
        f"score={result['score']:+d} | coverage={result['coverage']}/5"
    )
    print("Not: Bu rejim tarihsel/forward doğrulama olmadan alarm veya işlem sinyali değildir.")
    return result


if __name__ == "__main__":
    main()
