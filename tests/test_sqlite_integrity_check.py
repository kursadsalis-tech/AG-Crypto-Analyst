import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from runtime import cloud_attestation
from runtime import sqlite_integrity_check


class SqliteIntegrityCheckTests(unittest.TestCase):
    def test_full_check_writes_hash_bound_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = os.path.join(tmp, "runtime.db")
            health_path = os.path.join(tmp, "health.json")
            con = sqlite3.connect(database)
            con.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
            con.commit()
            con.close()
            with patch.dict(os.environ, {"AGCA_SQLITE_INTEGRITY_CACHE_PATH": health_path}):
                self.assertEqual(sqlite_integrity_check.run(database), "ok")
                result = cloud_attestation.sqlite_integrity_cache(
                    datetime.now(timezone.utc), health_path
                )
            self.assertEqual(result["state"], "CURRENT")
            self.assertEqual(result["result"], "ok")


if __name__ == "__main__":
    unittest.main()
