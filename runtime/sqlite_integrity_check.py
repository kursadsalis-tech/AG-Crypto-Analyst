"""Independent, low-priority full SQLite integrity evidence producer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from runtime.cloud_attestation import SQLITE_INTEGRITY_CACHE_VERSION


DB_NAME = "crypto_data.db"


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def write_health(result, checked_at=None, path=None):
    checked_at = checked_at or datetime.now(timezone.utc)
    path = Path(path or os.getenv(
        "AGCA_SQLITE_INTEGRITY_CACHE_PATH", "/var/lib/agca/sqlite-integrity-health.json"
    ))
    payload = {
        "health_contract_version": SQLITE_INTEGRITY_CACHE_VERSION,
        "checked_at": checked_at.astimezone(timezone.utc).isoformat(),
        "result": str(result),
    }
    payload["health_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)
    return payload


def run(database=DB_NAME):
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    try:
        result = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    write_health(result)
    if result != "ok":
        raise RuntimeError(f"SQLITE_QUICK_CHECK_{result}")
    return result


def seed_from_attestation(database=DB_NAME):
    """Bootstrap only from a hash-valid prior full quick-check attestation."""
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True, timeout=30)
    try:
        row = con.execute("""
            SELECT created_at,payload_json,payload_sha256,sqlite_quick_check
            FROM cloud_runtime_attestations ORDER BY attestation_id DESC LIMIT 1
        """).fetchone()
    finally:
        con.close()
    if not row or row[3] != "ok" or hashlib.sha256(row[1].encode()).hexdigest() != row[2]:
        raise RuntimeError("NO_HASH_VALID_QUICK_CHECK_ATTESTATION")
    checked_at = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    write_health("ok", checked_at=checked_at)
    return "ok"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed-from-attestation", action="store_true")
    args = parser.parse_args()
    result = seed_from_attestation() if args.seed_from_attestation else run()
    print(result)


if __name__ == "__main__":
    main()
