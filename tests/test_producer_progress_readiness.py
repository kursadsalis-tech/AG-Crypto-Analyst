"""Readiness requires current-process completed work, never a health heartbeat."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from runtime import technical_kline_worker as technical
from runtime.process_identity import WorkerHealthState
from runtime.worker_progress import WorkProgress
from scripts import collect_closed_behavior_minute as observer


class ProducerProgressReadinessTests(unittest.TestCase):
    def technical_health(self, metrics, **details):
        with patch.dict(technical._LAST_PERSISTENCE, metrics, clear=True), \
             patch.object(technical, '_HEALTH_STATE', WorkerHealthState()), \
             patch.object(technical.time, 'time', return_value=200), \
             patch.object(technical, '_atomic_json_write') as write:
            technical.write_health('CONNECTED', **details)
        return write.call_args.args[1]

    def test_technical_radar_without_current_process_closed_bar_is_not_ready(self):
        health = self.technical_health({'broad_radar_snapshot_timestamp_ms': 200_000})
        self.assertEqual(health['status'], 'RUNNING')
        self.assertFalse(health['closed_bar_fresh'])
        self.assertIsNone(health['last_success_at'])

    def test_technical_success_requires_persisted_fresh_bar_and_fresh_radar(self):
        for metrics in (
            {'last_closed_minute_ms': 199_000, 'last_flush_upserted': 1},
            {'last_closed_minute_ms': 1, 'last_flush_upserted': 1,
             'broad_radar_snapshot_timestamp_ms': 200_000},
            {'last_closed_minute_ms': 199_000, 'last_flush_upserted': 0,
             'broad_radar_snapshot_timestamp_ms': 200_000},
        ):
            health = self.technical_health(metrics, success=True)
            self.assertNotEqual(health['status'], 'CONNECTED')
            self.assertIsNone(health['last_success_at'])

    def test_technical_heartbeat_does_not_create_or_renew_actual_success(self):
        metrics = {'last_closed_minute_ms': 199_000, 'last_flush_upserted': 4,
                   'broad_radar_snapshot_timestamp_ms': 200_000}
        with patch.dict(technical._LAST_PERSISTENCE, metrics, clear=True), \
             patch.object(technical, '_HEALTH_STATE', WorkerHealthState()), \
             patch.object(technical.time, 'time', return_value=200), \
             patch.object(technical, '_atomic_json_write') as write:
            technical.write_health('CONNECTED', at='heartbeat-before-persist')
            self.assertIsNone(write.call_args.args[1]['last_success_at'])
            technical.write_health('CONNECTED', at='committed', success=True)
            technical.write_health('CONNECTED', at='heartbeat-after-persist')
            health = write.call_args.args[1]
        self.assertEqual(health['status'], 'CONNECTED')
        self.assertEqual(health['last_success_at'], 'committed')

    def test_technical_fatal_database_error_is_explicit_and_not_retried(self):
        state = technical.UniverseState(['BTCUSDT'])
        pending = {('BTCUSDT', 1): {'close_time_ms': 59_999}}
        with patch.object(technical.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(technical, 'persist_bars', side_effect=sqlite3.DatabaseError('database disk image is malformed')) as persist, \
             patch.object(technical, 'write_health') as health:
            with self.assertRaises(sqlite3.DatabaseError):
                asyncio.run(technical.persist_pending(pending, state, asyncio.Event()))
        self.assertEqual(persist.call_count, 1)
        self.assertTrue(health.call_args.kwargs['fatal_error'])
        self.assertTrue(pending)

    def test_technical_busy_keeps_buffer_and_only_committed_retry_advances(self):
        state = technical.UniverseState(['BTCUSDT'])
        pending = {('BTCUSDT', 1): {'symbol': 'BTCUSDT', 'close_time_ms': 59_999}}
        stop = asyncio.Event()
        calls, phases = [], []
        def health(status, **details):
            calls.append((status, details))
            if details.get('success'):
                stop.set()
        with patch.object(technical.asyncio, 'sleep', new=AsyncMock()), \
             patch.object(technical, 'persist_bars', side_effect=[
                 sqlite3.OperationalError('database is locked'),
                 {'upserted': 1, 'pruned': 0, 'row_count': None}]), \
             patch.object(technical, '_PROGRESS', WorkProgress(phases.append)), \
             patch.object(technical, 'write_health', side_effect=health):
            asyncio.run(technical.persist_pending(pending, state, stop))
        self.assertFalse(pending)
        self.assertFalse(calls[0][1]['fatal_error'])
        self.assertTrue(calls[1][1]['success'])
        self.assertEqual([item['phase'] for item in phases], ['CLOSED_BARS_PERSISTED'])

    def test_observer_progress_before_first_commit_is_pending_not_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'observer.json'
            args = SimpleNamespace(health_output=str(path))
            with patch.object(observer, '_HEALTH_STATE', WorkerHealthState()), \
                 patch.object(observer, '_LAST_COMPLETION_HEALTHY', False), \
                 patch.object(observer, '_PROGRESS', WorkProgress(lambda progress: None)):
                observer.advance_progress(args, 'MARKET_SAMPLE_COMPLETED')
                first = json.loads(path.read_text())
                observer.advance_progress(args, 'COLLECTION_COMPLETED')
                second = json.loads(path.read_text())
        self.assertEqual(second['status'], 'RUNNING')
        self.assertIsNone(second['last_success_at'])
        self.assertEqual(second['progress']['sequence'], first['progress']['sequence'] + 1)

    def test_observer_refresh_preserves_actual_success_until_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'observer.json'
            args = SimpleNamespace(health_output=str(path))
            committed = datetime.now(timezone.utc).isoformat()
            with patch.object(observer, '_HEALTH_STATE', WorkerHealthState()), \
                 patch.object(observer, '_LAST_COMPLETION_HEALTHY', False):
                observer.write_health(path, {'status': 'HEALTHY', 'at': committed}, success=True)
                observer.advance_progress(args, 'MARKET_SAMPLE_COMPLETED')
                refresh = json.loads(path.read_text())
                observer.write_health(path, {'status': 'DEGRADED', 'error': 'SQLITE_BUSY'})
                observer.advance_progress(args, 'ROUTE_RESOLVED')
                failed_refresh = json.loads(path.read_text())
        self.assertEqual(refresh['status'], 'HEALTHY')
        self.assertEqual(refresh['last_success_at'], committed)
        self.assertEqual(failed_refresh['status'], 'RUNNING')
        self.assertEqual(failed_refresh['last_success_at'], committed)

    def test_observer_distinguishes_busy_continuation_from_fatal_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'observer.json'
            args = SimpleNamespace(health_output=str(path), cycle_timeout_seconds=240)
            with patch.object(observer.signal, 'signal'), patch.object(observer.signal, 'alarm'), \
                 patch.object(observer, 'run_cycle', side_effect=sqlite3.OperationalError('database is locked')):
                self.assertIsNone(observer.run_bounded_cycle(args))
            busy = json.loads(path.read_text())
            with patch.object(observer.signal, 'signal'), patch.object(observer.signal, 'alarm'), \
                 patch.object(observer, 'run_cycle', side_effect=sqlite3.OperationalError('no such table: history')):
                with self.assertRaises(sqlite3.OperationalError):
                    observer.run_bounded_cycle(args)
            fatal = json.loads(path.read_text())
        self.assertEqual(busy['status'], 'DEGRADED')
        self.assertFalse(busy['fatal_error'])
        self.assertTrue(fatal['fatal_error'])


if __name__ == '__main__':
    unittest.main()
