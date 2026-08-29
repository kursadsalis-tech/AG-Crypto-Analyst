"""Join compact market snapshots and liquidation events without inventing alignment."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


VERSION = "LIVE_BEHAVIOR_EVIDENCE_BUNDLE_V1"


def build_bundle(market_evidence, liquidation_evidence):
    by_instrument = defaultdict(list)
    for event in liquidation_evidence.get("events", []):
        by_instrument[event["instrument"]].append(event)
    instruments = []
    deep_names = set()
    for row in market_evidence.get("instruments", []):
        instrument = row["instrument"]
        deep_names.add(instrument)
        events = by_instrument.pop(instrument, [])
        long_value = sum(event["notional_usd_estimate"] for event in events
                         if event["liquidated_side"] == "LONG")
        short_value = sum(event["notional_usd_estimate"] for event in events
                          if event["liquidated_side"] == "SHORT")
        instruments.append({
            **row,
            "liquidations": {
                "channel_state": liquidation_evidence.get("state"),
                "event_count": len(events),
                "long_liquidation_notional_usd_estimate": long_value,
                "short_liquidation_notional_usd_estimate": short_value,
                "events": events,
            },
        })
    outside_events = [event for events in by_instrument.values() for event in events]
    outside_events.sort(key=lambda row: (row["timestamp_ms"], row["instrument"]))
    channel_healthy = liquidation_evidence.get("state") in {
        "AVAILABLE_NO_EVENT_IN_WINDOW", "AVAILABLE_EVENTS_OBSERVED",
    }
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "instrument_count": len(instruments),
        "instruments": instruments,
        "liquidation_channel_healthy": channel_healthy,
        "outside_deep_stream_liquidation_events": outside_events,
        "attention_escalation_instruments": sorted({event["instrument"] for event in outside_events}),
        "alignment_contract": (
            "REST snapshots and WebSocket liquidation observation windows are recorded separately; "
            "no event is claimed to precede a snapshot unless its timestamp proves it."
        ),
        "raw_market_payloads_persisted": False,
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market-evidence", required=True)
    parser.add_argument("--liquidation-evidence", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    market = json.loads(Path(args.market_evidence).read_text(encoding="utf-8"))
    liquidation = json.loads(Path(args.liquidation_evidence).read_text(encoding="utf-8"))
    result = build_bundle(market, liquidation)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
