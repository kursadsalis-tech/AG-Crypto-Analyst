import sqlite3
import tempfile
import unittest
from unittest import mock

from news_intelligence import news_repository


class IncrementalSourceWriteTests(unittest.TestCase):
    def test_unchanged_news_analysis_does_not_requeue_remote_row(self):
        analysis = {
            "news_id": 1,
            "coins": "BTC",
            "event_type": "market",
            "direction": "neutral",
            "impact_score": 25,
            "confidence_score": 50,
            "matched_keywords": "[]",
        }
        with tempfile.NamedTemporaryFile(suffix=".db") as handle:
            with mock.patch.object(news_repository, "DB_NAME", handle.name):
                news_repository.create_news_analysis_table()
                news_repository.save_news_analysis_batch([analysis])
                raw = sqlite3.connect(handle.name)
                raw.execute("DELETE FROM remote_mirror_outbox")
                raw.commit()
                raw.close()
                news_repository.save_news_analysis_batch([analysis])
            raw = sqlite3.connect(handle.name)
            queued = raw.execute("""
                SELECT count(*) FROM remote_mirror_outbox
                WHERE table_name='crypto_news_analysis'
            """).fetchone()[0]
            raw.close()
        self.assertEqual(queued, 0)


if __name__ == "__main__":
    unittest.main()
