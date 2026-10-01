import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime import paper_realtime_worker as worker


class RealtimeHardeningHealthTests(unittest.TestCase):
    def test_startup_failure_overwrites_old_stopped_health_with_exact_error(self):
        with tempfile.TemporaryDirectory() as directory:
            health = Path(directory) / 'health.json'
            health.write_text('{"status":"STOPPED","at":"old"}')
            with patch.object(worker,'HEALTH_PATH',health), patch.object(
                worker,'validate_agent_authority',side_effect=ValueError('STARTUP_AUTHORITY_INVALID')):
                with self.assertRaisesRegex(ValueError,'STARTUP_AUTHORITY_INVALID'):
                    asyncio.run(worker.run())
            payload=json.loads(health.read_text())
        self.assertEqual(payload['status'],'FAILED')
        self.assertEqual(payload['error_detail'],'STARTUP_AUTHORITY_INVALID')
        self.assertEqual(payload['ENTRY_PATH']['status'],'UNAVAILABLE')
        self.assertEqual(payload['OPEN_POSITION_MANAGEMENT']['status'],'UNAVAILABLE')
        self.assertFalse(payload['real_order_allowed'])
        self.assertEqual(payload['health_contract_version'],worker.HEALTH_CONTRACT_VERSION)
        self.assertGreater(payload['process_source']['pid'],0)

    def test_entry_exception_rolls_back_but_does_not_skip_protective_stage(self):
        import sqlite3
        con=sqlite3.connect(":memory:")
        self.addCleanup(con.close)
        con.execute("CREATE TABLE entry_attempt(value INTEGER)")
        con.commit()
        with worker.isolate_entry_failure(con) as result:
            con.execute("INSERT INTO entry_attempt VALUES(1)")
            raise ValueError("entry risk cache missing")
        self.assertEqual(con.execute("SELECT COUNT(*) FROM entry_attempt").fetchone()[0],0)
        self.assertEqual(result['error'],'ValueError')
        self.assertIn('entry risk cache missing',result['error_detail'])
        # Connection and control flow are usable by the following manager stage.
        con.execute("INSERT INTO entry_attempt VALUES(2)")
        con.commit()
        self.assertEqual(con.execute("SELECT value FROM entry_attempt").fetchone()[0],2)

    def test_two_missing_route_caches_are_health_states_not_startup_exceptions(self):
        with patch.dict('os.environ',{
            'AGCA_EXCHANGE_METADATA_CACHE':'/unavailable-agca-usdm.json',
            'AGCA_SPOT_EXCHANGE_METADATA_CACHE':'/unavailable-agca-spot.json'}):
            caches,health=worker.load_runtime_metadata()
        self.assertEqual(caches,{})
        self.assertEqual(set(health),{'SPOT','USD_M_PERPETUAL'})
        self.assertTrue(all(value['status']=='UNAVAILABLE' for value in health.values()))
