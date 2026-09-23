"""Aggregate in-memory live evidence into causal, closed-minute features."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "ROLLING_BEHAVIOR_WINDOW_V1"
MINUTE_MS = 60_000


def _average(values):
    return sum(values) / len(values) if values else 0.0


def _mid_price(row):
    book = row.get("order_book") or {}
    bid = float(book.get("best_bid") or 0.0)
    ask = float(book.get("best_ask") or 0.0)
    return (bid + ask) / 2.0 if bid > 0 and ask > 0 and ask >= bid else None


def aggregate_closed_minute(bundles, minute_end_ms, min_samples=4, max_gap_ms=25_000):
    minute_start_ms = int(minute_end_ms) - MINUTE_MS
    samples = defaultdict(list)
    liquidation_events = {}
    channel_states = []
    for bundle in bundles:
        channel_states.append(bool(bundle.get("liquidation_channel_healthy")))
        for row in bundle.get("instruments", []):
            timestamp_ms = int(row["collected_timestamp_ms"])
            if minute_start_ms <= timestamp_ms < minute_end_ms:
                samples[row["instrument"]].append(row)
                for event in row.get("liquidations", {}).get("events", []):
                    event_ts = int(event["timestamp_ms"])
                    if minute_start_ms <= event_ts < minute_end_ms:
                        key = (event["instrument"], event_ts, event["liquidated_side"],
                               event["bankruptcy_price"], event["contracts"])
                        liquidation_events[key] = event
        for event in bundle.get("outside_deep_stream_liquidation_events", []):
            event_ts = int(event["timestamp_ms"])
            if minute_start_ms <= event_ts < minute_end_ms:
                key = (event["instrument"], event_ts, event["liquidated_side"],
                       event["bankruptcy_price"], event["contracts"])
                liquidation_events[key] = event

    rows = []
    for instrument, instrument_samples in sorted(samples.items()):
        instrument_samples.sort(key=lambda row: row["collected_timestamp_ms"])
        timestamps = [row["collected_timestamp_ms"] for row in instrument_samples]
        gaps = [later - earlier for earlier, later in zip(timestamps, timestamps[1:])]
        complete = [row for row in instrument_samples if row.get("required_sources_available")]
        events = [event for event in liquidation_events.values()
                  if event["instrument"] == instrument]
        first_oi = instrument_samples[0]["open_interest"]["usd"]
        last_oi = instrument_samples[-1]["open_interest"]["usd"]
        first_mid = _mid_price(instrument_samples[0])
        last_mid = _mid_price(instrument_samples[-1])
        gates = {
            "minimum_samples": len(instrument_samples) >= min_samples,
            "maximum_gap": not gaps or max(gaps) <= max_gap_ms,
            "required_sources": len(complete) == len(instrument_samples),
            "liquidation_channel": bool(channel_states) and all(channel_states),
            "valid_mid_price": first_mid is not None and last_mid is not None,
            "open_interest_available": all(
                bool((row.get("open_interest") or {}).get("available"))
                for row in instrument_samples
            ),
        }
        mandatory_quality = (
            gates["minimum_samples"]
            and gates["maximum_gap"]
            and gates["required_sources"]
            and gates["liquidation_channel"]
            and gates["valid_mid_price"]
        )
        rows.append({
            "instrument": instrument,
            "minute_start_timestamp_ms": minute_start_ms,
            "minute_end_timestamp_ms": int(minute_end_ms),
            "sample_count": len(instrument_samples),
            "max_sample_gap_ms": max(gaps) if gaps else 0,
            "book_imbalance_20_mean": _average([
                row["order_book"]["depths"]["20"]["imbalance"] for row in instrument_samples]),
            "book_imbalance_20_last": instrument_samples[-1]["order_book"]["depths"]["20"]["imbalance"],
            "taker_imbalance_snapshot_mean": _average([
                row["recent_trades"]["taker_imbalance"] for row in instrument_samples]),
            "taker_imbalance_snapshot_last": instrument_samples[-1]["recent_trades"]["taker_imbalance"],
            "spread_bps_mean": _average([
                row["order_book"]["spread_bps"] for row in instrument_samples
                if row["order_book"].get("spread_bps") is not None
            ]),
            "mid_price_first": first_mid,
            "mid_price_last": last_mid,
            "open_interest_usd_first": first_oi,
            "open_interest_usd_last": last_oi,
            "open_interest_usd_change": last_oi - first_oi,
            "funding_rate_last": instrument_samples[-1]["funding"]["rate"],
            "long_liquidation_notional_usd_estimate": sum(
                event["notional_usd_estimate"] for event in events
                if event["liquidated_side"] == "LONG"),
            "short_liquidation_notional_usd_estimate": sum(
                event["notional_usd_estimate"] for event in events
                if event["liquidated_side"] == "SHORT"),
            "liquidation_event_count": len(events),
            "quality_gates": gates,
            "eligible_for_learning": mandatory_quality,
        })
    outside_instruments = sorted({event["instrument"] for event in liquidation_events.values()
                                  if event["instrument"] not in samples})
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "minute_start_timestamp_ms": minute_start_ms,
        "minute_end_timestamp_ms": int(minute_end_ms),
        "minute_is_closed": True,
        "rows": rows,
        "row_count": len(rows),
        "eligible_row_count": sum(row["eligible_for_learning"] for row in rows),
        "attention_escalation_instruments": outside_instruments,
        "raw_snapshots_persisted": False,
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", action="append", required=True)
    parser.add_argument("--minute-end-ms", type=int, required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    bundles = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.bundle]
    result = aggregate_closed_minute(bundles, args.minute_end_ms)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
