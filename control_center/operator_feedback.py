"""Operator feedback log for AGCA Control Center.

This is not trading authority. Feedback records suspected market-data mismatch
for later diagnostics and never mutates positions, policy, learning, or orders.
"""
from __future__ import annotations

import math
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

FEEDBACK_PATH = Path(os.getenv(
    "AGCA_CONTROL_CENTER_FEEDBACK",
    "/var/lib/agca/control-center-data-feedback.jsonl",
))


def record_data_mismatch(payload, path=FEEDBACK_PATH):
    symbol = str(payload.get("symbol") or "").strip().upper()
    market_type = str(payload.get("market_type") or "").strip()
    if not symbol or market_type not in {"SPOT", "USD_M_PERPETUAL"}:
        raise ValueError("invalid_market_identity")
    observed = payload.get("operator_price")
    try:
        operator_price = float(observed)
    except (TypeError, ValueError):
        raise ValueError("invalid_operator_price")
    if not math.isfinite(operator_price) or operator_price <= 0:
        raise ValueError("invalid_operator_price")
    for key in ("agent_price", "agent_bid", "agent_ask"):
        if payload.get(key) is not None:
            try:
                value = float(payload[key])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError("invalid_agent_price")
                payload = {**payload, key: value}
            except (TypeError, ValueError):
                raise ValueError("invalid_agent_price")
    record = {
        "feedback_id": uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "type": "MARKET_DATA_MISMATCH",
        "symbol": symbol,
        "market_type": market_type,
        "operator_price": operator_price,
        "agent_price": payload.get("agent_price"),
        "agent_bid": payload.get("agent_bid"),
        "agent_ask": payload.get("agent_ask"),
        "agent_quote_source": payload.get("agent_quote_source"),
        "agent_freshness": payload.get("agent_freshness"),
        "note": str(payload.get("note") or "")[:500],
        "authority": "DIAGNOSTIC_ONLY",
        "trading_mutation_allowed": False,
        "learning_mutation_allowed": False,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record
