"""Convert OKX L2 events into compact, causal one-minute microstructure rows.

Each output row uses only order-book messages observed strictly before or at
its minute boundary.  The extractor describes visible market behaviour; it
does not label an actor as human or bot and does not create a trading signal.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from data.okx_l2_archive_audit import sha256


VERSION = "OKX_L2_CAUSAL_FEATURES_V1"
LEVEL_COUNTS = (5, 20, 50)


def next_minute(timestamp_ms):
    return ((timestamp_ms // 60_000) + 1) * 60_000


def imbalance(bid, ask):
    total = bid + ask
    return (bid - ask) / total if total > 0 else 0.0


@dataclass
class BookState:
    bids: dict[float, tuple[float, int]] = field(default_factory=dict)
    asks: dict[float, tuple[float, int]] = field(default_factory=dict)
    event_count: int = 0
    snapshot_count: int = 0
    update_count: int = 0
    add_notional: float = 0.0
    remove_notional: float = 0.0
    bid_update_count: int = 0
    ask_update_count: int = 0

    def reset_interval(self):
        self.event_count = 0
        self.snapshot_count = 0
        self.update_count = 0
        self.add_notional = 0.0
        self.remove_notional = 0.0
        self.bid_update_count = 0
        self.ask_update_count = 0

    def apply_side(self, target, levels, side):
        for price_text, quantity_text, orders_text in levels:
            price = float(price_text)
            quantity = float(quantity_text)
            orders = int(orders_text)
            if price <= 0:
                continue
            old_quantity = target.get(price, (0.0, 0))[0]
            delta = quantity - old_quantity
            if delta > 0:
                self.add_notional += price * delta
            elif delta < 0:
                self.remove_notional += price * -delta
            if quantity == 0:
                target.pop(price, None)
            else:
                target[price] = (quantity, orders)
            if side == "bid":
                self.bid_update_count += 1
            else:
                self.ask_update_count += 1

    def apply(self, event):
        action = event["action"]
        if action == "snapshot":
            self.bids.clear()
            self.asks.clear()
            for target, levels in ((self.bids, event["bids"]),
                                   (self.asks, event["asks"])):
                for price_text, quantity_text, orders_text in levels:
                    price = float(price_text)
                    quantity = float(quantity_text)
                    orders = int(orders_text)
                    if price > 0 and quantity > 0:
                        target[price] = (quantity, orders)
            self.event_count += 1
            self.snapshot_count += 1
            return
        elif action != "update":
            raise ValueError("unsupported L2 action")
        self.event_count += 1
        if action == "update":
            self.update_count += 1
        self.apply_side(self.bids, event["bids"], "bid")
        self.apply_side(self.asks, event["asks"], "ask")

    def feature_row(self, boundary_ms, instrument):
        bid_prices = sorted(self.bids, reverse=True)
        ask_prices = sorted(self.asks)
        if not bid_prices or not ask_prices:
            return None
        best_bid, best_ask = bid_prices[0], ask_prices[0]
        midpoint = (best_bid + best_ask) / 2.0
        if midpoint <= 0 or best_bid >= best_ask:
            return None
        row = {
            "version": VERSION,
            "instrument": instrument,
            "boundary_time": datetime.fromtimestamp(
                boundary_ms / 1000.0, tz=timezone.utc
            ).isoformat(),
            "best_bid": best_bid,
            "best_ask": best_ask,
            "midpoint": midpoint,
            "spread_bps": (best_ask - best_bid) / midpoint * 10_000.0,
            "visible_bid_levels": len(bid_prices),
            "visible_ask_levels": len(ask_prices),
            "events_1m": self.event_count,
            "snapshots_1m": self.snapshot_count,
            "updates_1m": self.update_count,
            "bid_level_updates_1m": self.bid_update_count,
            "ask_level_updates_1m": self.ask_update_count,
            "added_notional_1m": self.add_notional,
            "removed_notional_1m": self.remove_notional,
            "net_visible_notional_change_1m": (
                self.add_notional - self.remove_notional
            ),
        }
        for count in LEVEL_COUNTS:
            bid_notional = sum(
                price * self.bids[price][0] for price in bid_prices[:count]
            )
            ask_notional = sum(
                price * self.asks[price][0] for price in ask_prices[:count]
            )
            row[f"bid_notional_{count}"] = bid_notional
            row[f"ask_notional_{count}"] = ask_notional
            row[f"imbalance_{count}"] = imbalance(bid_notional, ask_notional)
        return row


def iter_archive_events(path):
    with tarfile.open(path, mode="r|gz") as archive:
        members = 0
        for member in archive:
            if not member.isfile():
                continue
            members += 1
            if members > 1:
                raise ValueError("archive must contain exactly one data file")
            handle = archive.extractfile(member)
            if handle is None:
                raise ValueError("cannot stream data member")
            for binary in handle:
                yield json.loads(binary)
        if members != 1:
            raise ValueError("archive contains no data file")


def extract(events, instrument):
    state = BookState()
    output = []
    boundary = None
    last_timestamp = None
    for event in events:
        if event.get("instId") != instrument:
            raise ValueError("unexpected instrument")
        timestamp = int(event["ts"])
        if last_timestamp is not None and timestamp < last_timestamp:
            raise ValueError("timestamps moved backwards")
        if boundary is None:
            if event.get("action") != "snapshot":
                raise ValueError("first event must be snapshot")
            boundary = next_minute(timestamp)
        while timestamp > boundary:
            row = state.feature_row(boundary, instrument)
            if row is not None:
                output.append(row)
            state.reset_interval()
            boundary += 60_000
        state.apply(event)
        last_timestamp = timestamp
    if boundary is not None:
        row = state.feature_row(boundary, instrument)
        if row is not None:
            output.append(row)
    return output


def write_csv(path, rows):
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("no causal feature rows produced")
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(rows[0]), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def summary(rows, instrument, archive_path=None, source_url=None, output_path=None):
    finite = all(
        not isinstance(value, float) or math.isfinite(value)
        for row in rows for value in row.values()
    )
    boundaries = [row["boundary_time"] for row in rows]
    gates = {
        "rows_between_1439_and_1441": 1439 <= len(rows) <= 1441,
        "all_values_finite": finite,
        "boundaries_unique": len(boundaries) == len(set(boundaries)),
        "spreads_positive": all(row["spread_bps"] > 0 for row in rows),
    }
    return {
        "version": VERSION,
        "instrument": instrument,
        "source_url": source_url,
        "source_archive_sha256": sha256(archive_path) if archive_path else None,
        "feature_csv_sha256": sha256(output_path) if output_path else None,
        "rows": len(rows),
        "first_boundary": boundaries[0] if boundaries else None,
        "last_boundary": boundaries[-1] if boundaries else None,
        "quality_gates": gates,
        "causal_feature_dataset_ready": all(gates.values()),
        "model_or_policy_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--instrument", required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--output", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()
    rows = extract(iter_archive_events(args.archive), args.instrument)
    write_csv(args.output, rows)
    result = summary(
        rows, args.instrument, args.archive, args.source_url, args.output
    )
    summary_path = Path(args.summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
