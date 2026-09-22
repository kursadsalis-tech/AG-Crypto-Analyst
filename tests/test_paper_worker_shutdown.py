"""Offline regressions for actual task cancellation and SQLite stop handling."""
import asyncio
import sqlite3
import threading
import unittest
from unittest.mock import patch

from runtime import paper_realtime_worker as worker


class WorkerShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_cancels_idle_socket_and_reconnect_backoff(self):
        stop = asyncio.Event()
        entered = asyncio.Event()
        cancelled = []
        async def socket():
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.append(True)
        task = asyncio.create_task(worker.run_until_stopped(stop, socket()))
        await entered.wait()
        stop.set()
        await asyncio.wait_for(task, .5)
        self.assertEqual(cancelled, [True])

    async def test_failure_cancels_siblings_before_propagating(self):
        stop = asyncio.Event()
        closed = []
        async def sibling():
            try:
                await asyncio.sleep(30)
            finally:
                closed.append(True)
        async def broken():
            await asyncio.sleep(0)
            raise ValueError("broken feed")
        with self.assertRaisesRegex(ValueError, "broken feed"):
            await worker.run_until_stopped(stop, sibling(), broken())
        self.assertTrue(stop.is_set())
        self.assertEqual(closed, [True])

    def test_shutdown_interrupts_sql_and_rolls_back_uncommitted_write(self):
        stop = threading.Event()
        with patch.object(worker, "DB_NAME", ":memory:"):
            db = worker.worker_connect(stop)
        try:
            db.execute("CREATE TABLE pending(value)")
            db.execute("INSERT INTO pending VALUES(1)")
            stop.set()
            with self.assertRaisesRegex(sqlite3.OperationalError, "interrupted"):
                db.execute("""WITH RECURSIVE n(x) AS
                    (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000)
                    SELECT sum(x) FROM n""").fetchone()
            db.rollback()
            stop.clear()
            self.assertEqual(db.execute("SELECT count(*) FROM pending").fetchone()[0], 0)
        finally:
            db.close()


class WorkerProcessShutdownTests(unittest.TestCase):
    def test_sigterm_exits_process_with_idle_feed_and_active_sql_thread(self):
        import os
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        script = r"""
import asyncio, os, signal, sqlite3, threading
from pathlib import Path
from runtime import paper_realtime_worker as w
root = Path(os.environ['WORKER_TEST_ROOT'])
w.DB_NAME = str(root / 'runtime.db')
w.HEALTH_PATH = root / 'health.json'
w.BRAIN_STATE_PATH = root / 'brain.json'
w.BRAIN_TIMELINE_PATH = root / 'timeline.json'
w.load_cache = lambda *a, **kw: None
w.validate_agent_authority = lambda **kw: None
w.cancel_expired_pending_entries = lambda *a: 0
w.position_management.ensure_tables = lambda *a: None
w.position_management.initialize_open_positions = lambda *a: None
def schema(db):
    db.executescript('CREATE TABLE IF NOT EXISTS paper_agent_positions(symbol, market_type, status);'
                     'CREATE TABLE IF NOT EXISTS market_behavior_minute_features('
                     'minute_end_timestamp_ms,instrument,eligible_for_learning);')
w.ensure_tables = schema
started = threading.Event()
def funding(db, *a, **kw):
    started.set()
    db.execute('WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n').fetchone()
    raise AssertionError('SQL should have been interrupted')
w.position_management.reconcile_pending_funding = funding
async def stream(*a, **kw):
    while not started.is_set():
        await asyncio.sleep(.01)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.sleep(30)
w.realtime_market.stream = stream
w.main()
assert started.is_set()
assert not any(t.name.startswith('agca-') for t in threading.enumerate())
import json
assert json.loads(w.HEALTH_PATH.read_text())['status'] == 'STOPPED'
print('clean process exit')
"""
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, "WORKER_TEST_ROOT": tmp,
                     "AGCA_REMOTE_MIRROR_INLINE": "0"},
                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean process exit", result.stdout)
