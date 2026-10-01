"""Contend only with disposable PAPER databases; never a live runtime/history."""
import asyncio
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from database.sqlite_policy import connect_local
from paper_trading import position_management as manager
from runtime import paper_realtime_worker as worker
from tests import test_position_management as fixture_module

ENTRY = fixture_module.ENTRY


class FailStatement:
    def __init__(self, con, statement, error=None):
        self.con, self.statement = con, statement
        self.error = error or sqlite3.OperationalError("database is locked")
        self.failed = False

    def __getattr__(self, name):
        return getattr(self.con, name)

    def execute(self, sql, parameters=()):
        if self.statement in sql and not self.failed:
            self.failed = True
            raise self.error
        return self.con.execute(sql, parameters)


class RealtimeSQLiteFailureDomainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.PositionManagementTests()
        self.fixture.setUp()
        self.con = self.fixture.con
        self.addCleanup(self.con.close)
        manager.initialize_open_positions(self.con, ENTRY + timedelta(seconds=1))
        self.states, self.entries = {}, {}
        self.key = 'BTCUSDT:USD_M_PERPETUAL'

    def tick(self, seconds, price=100, con=None):
        at = ENTRY + timedelta(seconds=seconds)
        with patch.object(worker, 'now', return_value=at), \
             patch.object(worker, 'rules_for', return_value=self.fixture.rules), \
             patch.object(worker, 'fill_pending_from_live_state', return_value=(0, 0)):
            return worker.protective_tick(
                con or self.con, self.fixture.live(price, seconds), at, {'USD_M_PERPETUAL': object()},
                market_snapshots=lambda at: {}, manager_states=self.states,
                entry_errors=self.entries)

    def test_manager_update_busy_rolls_back_then_fresh_tick_succeeds(self):
        before = self.con.execute('SELECT last_market_at FROM paper_position_management').fetchone()
        events_before = self.con.execute('SELECT COUNT(*) FROM paper_position_events').fetchone()
        failed = FailStatement(self.con, 'UPDATE paper_position_management SET')
        result = self.tick(2, con=failed)
        self.assertEqual(result['status'], 'DEGRADED')
        self.assertEqual(self.states[self.key]['path'], 'OPEN_POSITION_MANAGEMENT')
        self.assertEqual(self.states[self.key]['continuation'], 'NEXT_FRESH_TICK')
        self.assertFalse(self.con.in_transaction)
        self.assertEqual(self.con.execute('SELECT last_market_at FROM paper_position_management').fetchone(), before)
        # Legacy rules event written before UPDATE must roll back too.
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM paper_position_events').fetchone(), events_before)
        self.assertEqual(self.tick(3)['status'], 'HOLD')
        self.assertEqual(self.states[self.key]['status'], 'READY')
        self.assertEqual(self.states[self.key]['busy_count'], 1)
        self.assertEqual(self.tick(3)['status'], 'DUPLICATE_OR_OLD_EVENT')

    def test_busy_after_exit_update_cannot_double_exit_or_credit_cash(self):
        before = self.con.execute('SELECT cash,total_fees FROM paper_agent_portfolio').fetchone()
        failed = FailStatement(self.con, 'UPDATE paper_agent_portfolio SET cash=cash+?')
        self.assertEqual(self.tick(2, price=97, con=failed)['status'], 'DEGRADED')
        self.assertEqual(self.con.execute('SELECT status FROM paper_agent_positions').fetchone()[0], 'OPEN')
        self.assertEqual(self.con.execute('SELECT cash,total_fees FROM paper_agent_portfolio').fetchone(), before)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_events WHERE event_type='POSITION_CLOSED'").fetchone()[0], 0)
        self.assertEqual(self.tick(3, price=97)['status'], 'POSITION_CLOSED')
        after = self.con.execute('SELECT cash,total_fees FROM paper_agent_portfolio').fetchone()
        self.assertEqual(self.tick(4, price=97)['status'], 'NO_OPEN_POSITION')
        self.assertEqual(self.con.execute('SELECT cash,total_fees FROM paper_agent_portfolio').fetchone(), after)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_events WHERE event_type='POSITION_CLOSED'").fetchone()[0], 1)

    def test_pending_busy_does_not_skip_or_rollback_protective_manager(self):
        order = []
        at = ENTRY + timedelta(seconds=2)
        real_manager = manager.process_market_state
        def manage(*args):
            order.append('manager')
            return real_manager(*args)
        def pending(*args, **kwargs):
            order.append('pending')
            self.con.execute('UPDATE paper_agent_portfolio SET cash=cash-100')
            raise sqlite3.OperationalError('database is locked')
        with patch.object(worker, 'now', return_value=at), \
             patch.object(worker, 'rules_for', return_value=self.fixture.rules), \
             patch.object(manager, 'process_market_state', side_effect=manage), \
             patch.object(worker, 'fill_pending_from_live_state', side_effect=pending):
            result = worker.protective_tick(self.con, self.fixture.live(100, 2), at,
                {'USD_M_PERPETUAL': object()}, market_snapshots=lambda at: {},
                manager_states=self.states, entry_errors=self.entries)
        self.assertEqual(order, ['manager', 'pending'])
        self.assertEqual(result['status'], 'HOLD')
        self.assertEqual(self.entries[self.key]['status'], 'DEGRADED')
        self.assertEqual(self.entries[self.key]['path'], 'PENDING_FILL')
        self.assertEqual(self.con.execute('SELECT last_market_at FROM paper_position_management').fetchone()[0], at.isoformat())
        self.assertEqual(self.con.execute('SELECT cash FROM paper_agent_portfolio').fetchone()[0], 8999)

    def test_manager_busy_prevents_new_entry_work(self):
        at = ENTRY + timedelta(seconds=2)
        with patch.object(worker, 'now', return_value=at), \
             patch.object(manager, 'process_market_state', side_effect=sqlite3.OperationalError('database is locked')), \
             patch.object(worker, 'fill_pending_from_live_state') as pending:
            worker.protective_tick(self.con, self.fixture.live(100, 2), at, {},
                market_snapshots=lambda at: {}, manager_states=self.states, entry_errors=self.entries)
        pending.assert_not_called()

    def test_schema_and_corruption_fail_closed_instead_of_becoming_busy(self):
        for error in (sqlite3.OperationalError('no such table: paper_position_management'),
                      sqlite3.DatabaseError('database disk image is malformed'),
                      sqlite3.ProgrammingError('Incorrect number of bindings supplied')):
            with self.subTest(error=error):
                failed = FailStatement(self.con, 'UPDATE paper_position_management SET', error)
                with self.assertRaises(type(error)):
                    self.tick(2, con=failed)
                self.assertEqual(self.states[self.key]['status'], 'FAILED')
                self.assertEqual(self.states[self.key]['continuation'], 'FAIL_CLOSED')
                self.assertFalse(self.con.in_transaction)

    def test_stale_or_future_tick_cannot_be_applied_after_busy(self):
        at = ENTRY + timedelta(seconds=10)
        with patch.object(worker, 'now', return_value=at), \
             patch.object(manager, 'process_market_state') as apply:
            for seconds in (2, 12):
                result = worker.protective_tick(self.con, self.fixture.live(100, seconds), at,
                    {}, market_snapshots=lambda at: {}, manager_states=self.states, entry_errors=self.entries)
                self.assertEqual(result['status'], 'STALE_TICK_SKIPPED')
        apply.assert_not_called()

    def test_entry_rechecks_wall_clock_after_protection(self):
        at = ENTRY + timedelta(seconds=2)
        with patch.object(worker, 'now', side_effect=[at, at, at + timedelta(seconds=10)]), \
             patch.object(manager, 'process_market_state', return_value={'status': 'HOLD'}), \
             patch.object(worker, 'fill_pending_from_live_state') as pending:
            worker.protective_tick(self.con, self.fixture.live(100, 2), at, {},
                market_snapshots=lambda at: {}, manager_states=self.states, entry_errors=self.entries)
        pending.assert_not_called()

    def test_real_external_writer_lock_survives_gather_then_recovers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'paper.db'
            target = sqlite3.connect(path)
            self.con.backup(target)
            target.execute('PRAGMA journal_mode=WAL')
            target.close()
            blocker = sqlite3.connect(path)
            blocker.execute('BEGIN IMMEDIATE')
            blocker.execute('UPDATE paper_agent_portfolio SET cash=cash')
            def attempt(seconds):
                db = connect_local(path, timeout=0.01, priority='realtime')
                try:
                    return self.tick(seconds, con=db)
                finally:
                    db.close()
            async def cycle():
                received = []
                async def ingest():
                    received.append('fresh_quote')
                result, _ = await asyncio.gather(asyncio.to_thread(attempt, 2), ingest())
                self.assertEqual(received, ['fresh_quote'])
                return result
            try:
                self.assertEqual(asyncio.run(cycle())['status'], 'DEGRADED')
            finally:
                blocker.rollback()
                blocker.close()
            self.assertEqual(attempt(3)['status'], 'HOLD')

    def test_failed_manager_initialization_rolls_back_entire_new_fill(self):
        at = ENTRY + timedelta(seconds=2)
        def pending(*args, **kwargs):
            self.assertFalse(kwargs['commit'])
            self.con.execute('UPDATE paper_agent_portfolio SET cash=cash-100')
            return 1, 0
        with patch.object(worker, 'now', return_value=at), \
             patch.object(manager, 'process_market_state', return_value={'status': 'NO_OPEN_POSITION'}), \
             patch.object(worker, 'fill_pending_from_live_state', side_effect=pending), \
             patch.object(manager, 'initialize_open_positions', side_effect=sqlite3.OperationalError('database is locked')):
            worker.protective_tick(self.con, self.fixture.live(100, 2), at, {},
                market_snapshots=lambda at: {}, manager_states=self.states, entry_errors=self.entries)
        self.assertEqual(self.con.execute('SELECT cash FROM paper_agent_portfolio').fetchone()[0], 8999)
        self.assertFalse(self.con.in_transaction)
        self.assertEqual(self.entries[self.key]['error'], 'SQLITE_BUSY')


if __name__ == '__main__':
    unittest.main()
