import contextlib
import io
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime import paper_realtime_health_monitor as monitor


class RealtimeHealthMonitorTests(unittest.TestCase):
    def test_transitions_are_deduplicated_without_mirror_bootstrap(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "runtime.db"
            state = Path(directory) / "health-state.json"
            with sqlite3.connect(db) as con:
                con.execute("CREATE TABLE paper_agent_positions(status TEXT)")
                con.execute("INSERT INTO paper_agent_positions VALUES('OPEN')")
            statements = []
            original_connect = sqlite3.connect

            def traced_connect(*args, **kwargs):
                con = original_connect(*args, **kwargs)
                con.set_trace_callback(statements.append)
                return con

            with patch.object(monitor, "DB_PATH", str(db)), \
                 patch.object(monitor, "STATE_PATH", state), \
                 patch.object(monitor.sqlite3, "connect", side_effect=traced_connect), \
                 patch.object(monitor, "paper_realtime_health") as health, \
                 contextlib.redirect_stdout(io.StringIO()):
                health.return_value = {"state": "CURRENT", "status": "CONNECTED"}
                monitor.main()
                statements.clear()
                monitor.main()
                self.assertFalse(any(sql.startswith(("CREATE", "INSERT", "UPDATE"))
                                     for sql in statements))
                health.return_value = {"state": "STALE", "status": "CONNECTED"}
                monitor.main()
                health.assert_called_with(open_positions=1)
            with original_connect(db) as con:
                self.assertEqual(con.execute(
                    "SELECT state FROM paper_operator_alerts ORDER BY alert_id"
                ).fetchall(), [("RECOVERED",), ("FAULT",)])
                tables = {row[0] for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                self.assertNotIn("remote_mirror_outbox", tables)

    def test_polling_import_does_not_load_trading_engine(self):
        result = subprocess.run([
            sys.executable, "-c",
            "import sys; import runtime.paper_realtime_health_monitor; "
            "assert 'paper_trading.crypto_profit_agent' not in sys.modules",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
