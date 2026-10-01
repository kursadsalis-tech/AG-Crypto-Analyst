"""Disposable-file contention across real processes; no runtime DB or network."""
import fcntl
import gc
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from database.sqlite_policy import connect_local, is_sqlite_busy
from database import runtime_store
from runtime.worker_progress import WorkProgress
from runtime.process_identity import WorkerHealthState


class SQLiteWriterCoordinationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name) / 'test.db')
        con = connect_local(self.path)
        con.execute('CREATE TABLE ledger(id INTEGER PRIMARY KEY, value INTEGER)')
        con.close()

    def test_only_busy_codes_are_transient_including_extended_codes(self):
        for code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, 517):
            exc = sqlite3.OperationalError('numeric code is authoritative')
            exc.sqlite_errorcode = code
            self.assertTrue(is_sqlite_busy(exc))
        for message in ('database is locked', 'database table is locked: ledger'):
            self.assertTrue(is_sqlite_busy(sqlite3.OperationalError(message)))
        for exc in (sqlite3.OperationalError('no such table: ledger'),
                    sqlite3.DatabaseError('database disk image is malformed'),
                    sqlite3.ProgrammingError('closed connection')):
            self.assertFalse(is_sqlite_busy(exc))
        exc = sqlite3.OperationalError('database is locked')
        exc.sqlite_errorcode = sqlite3.SQLITE_IOERR
        self.assertFalse(is_sqlite_busy(exc))

    def test_connection_policy_preserves_journal_and_caps_busy_wait(self):
        for mode in ('WAL', 'DELETE'):
            raw = sqlite3.connect(self.path)
            raw.execute('PRAGMA journal_mode=' + mode)
            raw.close()
            con = connect_local(self.path, timeout=30)
            self.assertEqual(con.execute('PRAGMA journal_mode').fetchone()[0], mode.lower())
            con.execute('PRAGMA busy_timeout=30000')
            self.assertEqual(con.execute('PRAGMA busy_timeout').fetchone()[0], 250)
            con.configure_writer(priority='realtime', timeout=.05)
            self.assertEqual(con.execute('PRAGMA busy_timeout').fetchone()[0], 50)
            con.close()

    def test_rollback_releases_writer_and_does_not_publish_partial_rows(self):
        first, second = connect_local(self.path), connect_local(self.path, timeout=0)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.execute('INSERT INTO ledger VALUES(1, 7)')
        with self.assertRaises(sqlite3.OperationalError) as error:
            second.execute('INSERT INTO ledger VALUES(2, 8)')
        self.assertTrue(is_sqlite_busy(error.exception))
        second.rollback()
        first.rollback()
        second.execute('INSERT INTO ledger VALUES(2, 8)')
        second.commit()
        self.assertEqual(first.execute('SELECT * FROM ledger').fetchall(), [(2, 8)])

    def test_runtime_context_commit_busy_rolls_back_and_releases_admission(self):
        writer = runtime_store.connect(self.path, timeout=.01)
        reader = sqlite3.connect(self.path)
        self.addCleanup(writer.close)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM ledger").fetchall()
        with self.assertRaises(sqlite3.OperationalError) as error:
            with writer:
                writer.execute("INSERT INTO ledger VALUES(1, 1)")
        self.assertTrue(is_sqlite_busy(error.exception))
        self.assertFalse(writer.in_transaction)
        self.assertFalse(writer._writer_owned)
        reader.rollback()
        contender = connect_local(self.path, timeout=0)
        self.addCleanup(contender.close)
        contender.execute("INSERT INTO ledger VALUES(2, 2)")
        contender.commit()
        self.assertEqual(writer.execute("SELECT * FROM ledger").fetchall(), [(2, 2)])

    def test_realtime_admission_blocks_new_background_writer(self):
        # Simulate the realtime waiter's admission claim while the SQLite slot
        # itself is free. A background connection must not jump that waiter.
        fd = os.open(self.path + '.agca-realtime.lock', os.O_RDWR)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        con = connect_local(self.path, timeout=0)
        self.addCleanup(con.close)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute('INSERT INTO ledger VALUES(1,1)')
        con.rollback()
        fcntl.flock(fd, fcntl.LOCK_UN)
        con.execute('INSERT INTO ledger VALUES(1,1)')
        con.commit()

    def test_cross_process_background_contention_completes_without_duplicates(self):
        script = '''import sqlite3, sys, time
from database.sqlite_policy import connect_local, is_sqlite_busy
con = connect_local(sys.argv[1], timeout=.02)
base = int(sys.argv[2])
for row in range(base, base + 20):
    for attempt in range(200):
        try:
            con.execute('INSERT OR IGNORE INTO ledger VALUES(?,?)', (row, row))
            time.sleep(.001)
            con.commit()
            break
        except sqlite3.OperationalError as exc:
            con.rollback()
            if not is_sqlite_busy(exc): raise
            time.sleep(.002)
    else: raise RuntimeError('writer did not make progress')
con.close()
'''
        processes = [subprocess.Popen([sys.executable, '-c', script, self.path, str(base)],
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for base in (0, 20)]
        for process in processes:
            self.addCleanup(lambda p=process: p.kill() if p.poll() is None else None)
        for process in processes:
            out, err = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, out + err)
        con = connect_local(self.path)
        self.addCleanup(con.close)
        self.assertEqual(con.execute('SELECT COUNT(*),SUM(value) FROM ledger').fetchone(), (40, 780))

    def test_collected_connection_closes_advisory_file_descriptors(self):
        con = connect_local(self.path)
        con.execute('INSERT INTO ledger VALUES(1,1)')
        con.commit()
        fds = (con._writer_fd, con._priority_fd)
        del con
        gc.collect()
        for fd in fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_work_progress_requires_actual_work_and_success_is_not_heartbeat(self):
        clock = [0.0]
        reports = []
        progress = WorkProgress(reports.append, clock=lambda: clock[0])
        progress.advance('SCHEMA_READY')
        clock[0] = 200
        self.assertEqual(len(reports), 1)  # Elapsed time cannot emit progress.
        progress.sqlite_step()            # SQLite has executed another chunk.
        self.assertEqual([r['sequence'] for r in reports], [1, 2])
        progress.sqlite_step()
        self.assertEqual(len(reports), 2)
        state = WorkerHealthState()
        self.assertIsNone(state.decorate({'status': 'CONNECTED'}, success=False)['last_success_at'])
        state.decorate({'status': 'HEALTHY', 'at': 'real-commit'}, success=True)
        self.assertEqual(state.decorate({'status': 'HEALTHY', 'at': 'heartbeat'}, success=False)['last_success_at'], 'real-commit')
