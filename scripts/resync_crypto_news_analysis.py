"""One-off exact repair of the bloated Supabase news-analysis mirror."""
from __future__ import annotations

import json
import os
import sqlite3


TABLE = "crypto_news_analysis"
COLUMNS = (
    "id", "news_id", "coins", "event_type", "direction", "impact_score",
    "confidence_score", "matched_keywords", "analyzed_at",
)


def main():
    database = os.getenv("AGCA_STATE_DATABASE", "/var/lib/agca/crypto_data.db")
    local = sqlite3.connect(database, timeout=30)
    try:
        rows = local.execute(
            f"SELECT {','.join(COLUMNS)} FROM {TABLE} ORDER BY id"
        ).fetchall()
        distinct = local.execute(
            f"SELECT count(DISTINCT news_id) FROM {TABLE}"
        ).fetchone()[0]
        if not rows or distinct != len(rows):
            raise RuntimeError("LOCAL_NEWS_ANALYSIS_NOT_UNIQUE")

        import psycopg
        remote_url = os.environ["AGCA_REMOTE_DATABASE_URL"]
        with psycopg.connect(
            remote_url, connect_timeout=20, tcp_user_timeout=120000,
            prepare_threshold=None, autocommit=True,
        ) as remote:
            with remote.cursor() as cursor:
                cursor.execute(
                    "SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE"
                )
            with remote.transaction():
                with remote.cursor() as cursor:
                    cursor.execute("SET LOCAL statement_timeout = '120s'")
                    cursor.execute("TRUNCATE TABLE agca_runtime.crypto_news_analysis")
                    placeholders = ",".join(["%s"] * len(COLUMNS))
                    cursor.executemany(
                        "INSERT INTO agca_runtime.crypto_news_analysis ("
                        + ",".join(COLUMNS) + ") VALUES (" + placeholders + ")",
                        rows,
                    )
                    cursor.execute(
                        "SELECT count(*),count(DISTINCT news_id) "
                        "FROM agca_runtime.crypto_news_analysis"
                    )
                    remote_count, remote_distinct = cursor.fetchone()
                    if remote_count != len(rows) or remote_distinct != len(rows):
                        raise RuntimeError("REMOTE_NEWS_ANALYSIS_RESYNC_MISMATCH")
            with remote.cursor() as cursor:
                cursor.execute("VACUUM (ANALYZE) agca_runtime.crypto_news_analysis")
                cursor.execute("SET default_transaction_read_only = 'off'")

        local.execute(
            "DELETE FROM remote_mirror_outbox WHERE table_name=?", (TABLE,)
        )
        local.commit()
        print(json.dumps({
            "table": TABLE,
            "local_rows": len(rows),
            "remote_rows": remote_count,
            "remote_distinct_news_ids": remote_distinct,
            "status": "EXACT_RESYNC_COMPLETE",
        }, sort_keys=True))
    finally:
        local.close()


if __name__ == "__main__":
    main()
