"""Repository contract: exactly one component owns the PAPER trading lifecycle."""
from pathlib import Path
import sqlite3
import unittest

from runtime import paper_realtime_worker

ROOT = Path(__file__).resolve().parents[1]

class SingleRuntimeAuthorityTests(unittest.TestCase):
    def test_only_realtime_worker_is_deployable_paper_lifecycle_authority(self):
        lifecycle=[]
        for path in sorted((ROOT/"deploy").glob("*.service")):
            text=path.read_text(encoding="utf-8")
            if any(token in text for token in ("runtime.paper_realtime_worker","runtime.paper_selection_cycle","runtime.cloud_worker")):
                lifecycle.append(path.name)
        self.assertEqual(lifecycle,["agca-paper-realtime-worker.service"])

    def test_retired_parallel_runtime_entrypoints_do_not_exist(self):
        retired=["runtime/cloud_worker.py","runtime/paper_selection_cycle.py","deploy/agca-cloud-worker.service","deploy/agca-paper-selection.service","deploy/agca-paper-selection.timer","deploy/run_cloud_worker.sh"]
        self.assertEqual([p for p in retired if (ROOT/p).exists()],[])

    def test_deployment_files_do_not_reference_retired_authorities(self):
        forbidden=("runtime.cloud_worker","runtime.paper_selection_cycle","agca-cloud-worker.service","agca-paper-selection.service","agca-paper-selection.timer")
        offenders=[]
        for path in (ROOT/"deploy").rglob("*"):
            if path.is_file():
                text=path.read_text(encoding="utf-8",errors="ignore")
                offenders.extend(f"{path.relative_to(ROOT)}:{token}" for token in forbidden if token in text)
        self.assertEqual(offenders,[])

    def test_pending_spot_entry_is_part_of_realtime_subscription_universe(self):
        con = sqlite3.connect(":memory:")
        try:
            con.executescript("""
                CREATE TABLE paper_agent_positions(
                    symbol TEXT,market_type TEXT,status TEXT
                );
                CREATE TABLE market_behavior_minute_features(
                    instrument TEXT,minute_end_timestamp_ms INTEGER,
                    eligible_for_learning INTEGER
                );
                INSERT INTO paper_agent_positions VALUES(
                    'ETHUSDT','SPOT','PENDING_ENTRY'
                );
                INSERT INTO paper_agent_positions VALUES(
                    'BTCUSDT','SPOT','CLOSED'
                );
                INSERT INTO market_behavior_minute_features VALUES(
                    'ETH-USDT-SWAP',1000,1
                );
            """)
            symbols = set(paper_realtime_worker.subscription_symbols(con))
        finally:
            con.close()
        self.assertIn(("ETHUSDT", "SPOT"), symbols)
        self.assertIn(("ETHUSDT", "USD_M_PERPETUAL"), symbols)
        self.assertNotIn(("BTCUSDT", "SPOT"), symbols)

    def test_realtime_worker_remains_paper_only(self):
        text=(ROOT/"deploy/agca-paper-realtime-worker.service").read_text(encoding="utf-8")
        self.assertIn("AGCA_EXECUTION_MODE=PAPER_ONLY",text)
        self.assertIn("-m runtime.paper_realtime_worker",text)

if __name__=="__main__":
    unittest.main()
