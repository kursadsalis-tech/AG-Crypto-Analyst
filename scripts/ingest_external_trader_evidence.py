"""Import normalized public copy-trader evidence into the research quarantine."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from database.runtime_store import connect
from paper_trading import external_trader_learning


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, help="JSON object or array of objects")
    parser.add_argument("--database", default="crypto_data.db")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    records = payload if isinstance(payload, list) else [payload]
    con = connect(args.database, timeout=30)
    results = []
    try:
        for record in records:
            kind = record.pop("record_type", None)
            if kind == "TRADER_SNAPSHOT":
                results.append(external_trader_learning.ingest_snapshot(con, record))
            elif kind == "TRADE_OBSERVATION":
                results.append(external_trader_learning.ingest_trade(con, record))
            else:
                raise ValueError("record_type must be TRADER_SNAPSHOT or TRADE_OBSERVATION")
        review = external_trader_learning.run_review(con)
    finally:
        con.close()
    print(json.dumps({"ingested": len(results), "results": results, "review": review},
                     ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
