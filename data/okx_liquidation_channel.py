"""Audit the public OKX liquidation stream without persisting raw messages."""
from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path

import certifi
import websockets


VERSION = "OKX_LIQUIDATION_CHANNEL_V1"
WS_URL = "wss://ws.okx.com:8443/ws/v5/public"


def classify(messages):
    subscribed = any(row.get("event") == "subscribe" for row in messages)
    errors = [row for row in messages if row.get("event") == "error"]
    data_messages = [row for row in messages if row.get("data")]
    event_rows = sum(len(row.get("data", [])) for row in data_messages)
    if errors:
        state = "CHANNEL_ERROR"
    elif subscribed and event_rows:
        state = "AVAILABLE_EVENTS_OBSERVED"
    elif subscribed:
        state = "AVAILABLE_NO_EVENT_IN_WINDOW"
    else:
        state = "SUBSCRIPTION_NOT_CONFIRMED"
    return {
        "state": state,
        "subscription_confirmed": subscribed,
        "error_count": len(errors),
        "data_message_count": len(data_messages),
        "event_row_count": event_rows,
    }


def normalize_events(data_rows, contract_values=None):
    """Flatten OKX liquidation groups into compact, attributable evidence."""
    contract_values = contract_values or {}
    normalized = []
    invalid_count = 0
    for group in data_rows:
        instrument = group.get("instId")
        for detail in group.get("details", []):
            try:
                price = float(detail["bkPx"])
                size = float(detail["sz"])
                timestamp_ms = int(detail["ts"])
                if not instrument or price <= 0 or size <= 0 or timestamp_ms <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                invalid_count += 1
                continue
            position_side = detail.get("posSide")
            order_side = detail.get("side")
            if position_side == "long" or (not position_side and order_side == "sell"):
                liquidated_side = "LONG"
            elif position_side == "short" or (not position_side and order_side == "buy"):
                liquidated_side = "SHORT"
            else:
                liquidated_side = "UNKNOWN"
            contract_value = float(contract_values.get(instrument, 1.0))
            normalized.append({
                "instrument": instrument,
                "timestamp_ms": timestamp_ms,
                "liquidated_side": liquidated_side,
                "bankruptcy_price": price,
                "contracts": size,
                "notional_usd_estimate": price * size * contract_value,
            })
    normalized.sort(key=lambda row: (row["timestamp_ms"], row["instrument"]))
    return normalized, invalid_count


async def probe(duration_seconds=8, contract_values=None):
    context = ssl.create_default_context(cafile=certifi.where())
    request_id = str(int(time.time() * 1000))[-12:]
    messages = []
    events = []
    invalid_event_count = 0
    async with websockets.connect(WS_URL, open_timeout=10, ping_interval=None,
                                  ssl=context) as socket:
        await socket.send(json.dumps({
            "id": request_id,
            "op": "subscribe",
            "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
        }))
        deadline = time.monotonic() + duration_seconds
        while time.monotonic() < deadline:
            try:
                raw = await asyncio.wait_for(
                    socket.recv(), timeout=min(3, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                await socket.send("ping")
                continue
            if raw == "pong":
                continue
            row = json.loads(raw)
            normalized, invalid = normalize_events(row.get("data", []), contract_values)
            events.extend(normalized)
            invalid_event_count += invalid
            # Keep only the contract fields and counts; do not persist raw rows.
            messages.append({
                "event": row.get("event"),
                "code": row.get("code"),
                "arg": row.get("arg"),
                "data": [{} for _ in row.get("data", [])],
            })
            if row.get("event") == "error":
                break
    result = classify(messages)
    return {
        "version": VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": "OKX",
        "channel": "liquidation-orders",
        "instrument_type": "SWAP",
        "observation_window_seconds": duration_seconds,
        **result,
        "events": events,
        "normalized_event_count": len(events),
        "invalid_event_count": invalid_event_count,
        "raw_messages_persisted": False,
        "trade_signal_generated": False,
        "paper_only_changed": False,
        "trading_authority_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=8)
    parser.add_argument("--output", required=True)
    parser.add_argument("--universe")
    args = parser.parse_args()
    contract_values = {}
    if args.universe:
        universe = json.loads(Path(args.universe).read_text(encoding="utf-8"))
        contract_values = {row["instrument"]: row.get("contract_value", 1.0)
                           for row in universe.get("selected", [])}
    result = asyncio.run(probe(args.duration_seconds, contract_values))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
