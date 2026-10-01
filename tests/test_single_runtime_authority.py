"""Repository contract: exactly one component owns the PAPER trading lifecycle."""
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

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

    def test_shared_candidate_orchestrator_has_no_trade_execution_authority(self):
        text=(ROOT/"decision_engine/paper_candidate_orchestrator.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "INSERT INTO paper_agent_positions",
            "UPDATE paper_agent_positions",
            "execute_paper(",
            "simulated_fill(",
            "process_market_state(",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("real-order authority", text)

    def test_controlled_challenger_has_no_trade_execution_authority(self):
        text=(ROOT/"paper_trading/controlled_challenger.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "INSERT INTO paper_agent_positions",
            "UPDATE paper_agent_positions",
            "execute_paper(",
            "simulated_fill(",
            "process_market_state(",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("promotion_allowed INTEGER NOT NULL DEFAULT 0", text)
        self.assertIn("policy_mutation_allowed INTEGER NOT NULL DEFAULT 0", text)
        self.assertIn("real_order_allowed INTEGER NOT NULL DEFAULT 0", text)

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

    def test_cloud_installer_reexecs_from_refreshed_checkout(self):
        text=(ROOT/"deploy/install_gce_free_worker.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "${AGCA_DEPLOY_REEXEC:-0}" != "1" ]]', text)
        self.assertIn('git pull --ff-only origin main', text)
        self.assertIn(
            'exec env AGCA_DEPLOY_REEXEC=1 bash "$DEPLOY/deploy/install_gce_free_worker.sh"',
            text,
        )
        self.assertLess(
            text.index('exec env AGCA_DEPLOY_REEXEC=1'),
            text.index('canonical_units=('),
        )

    def test_worker_process_source_is_captured_from_process_checkout(self):
        clean = Mock(returncode=0, stdout="e" * 40 + "\n", stderr="")
        dirty = Mock(returncode=0, stdout="", stderr="")
        with patch.object(
            paper_realtime_worker.subprocess,
            "run",
            side_effect=[clean, dirty],
        ):
            source = paper_realtime_worker.worker_process_source_state("/tmp/repo")
        self.assertEqual(source["git_sha"], "e" * 40)
        self.assertFalse(source["git_dirty"])

    def test_realtime_subscription_refresh_is_read_only_and_expiry_is_isolated(self):
        text=(ROOT/"runtime/paper_realtime_worker.py").read_text(encoding="utf-8")
        load=text[text.index("    def load_symbols("):text.index("    def symbols():")]
        self.assertIn('return subscription_symbols(db)', load)
        self.assertNotIn('cancel_expired_pending_entries(', load)
        self.assertNotIn('initialize_open_positions(', load)
        maintenance=text[text.index('    def maintenance_once():'):text.index('    async def maintenance():')]
        self.assertIn('sqlite_path(maintenance_con, "PENDING_EXPIRY"', maintenance)
        self.assertIn('cancel_expired_pending_entries(maintenance_con, now())', maintenance)

    def test_pending_spot_entry_is_part_of_realtime_subscription_universe(self):
        con = sqlite3.connect(":memory:")
        try:
            con.executescript("""
                CREATE TABLE paper_agent_positions(
                    symbol TEXT,market_type TEXT,status TEXT
                );
                CREATE TABLE market_behavior_minute_features(
                    instrument TEXT,market_data_provider TEXT,
                    minute_end_timestamp_ms INTEGER,
                    eligible_for_learning INTEGER
                );
                INSERT INTO paper_agent_positions VALUES(
                    'ETHUSDT','SPOT','PENDING_ENTRY'
                );
                INSERT INTO paper_agent_positions VALUES(
                    'BTCUSDT','SPOT','CLOSED'
                );
                INSERT INTO market_behavior_minute_features VALUES(
                    'ETH-USDT-SWAP','BINANCE',1000,1
                );
            """)
            symbols = set(paper_realtime_worker.subscription_symbols(
                con, datetime.fromtimestamp(2, timezone.utc)))
        finally:
            con.close()
        self.assertIn(("ETHUSDT", "SPOT"), symbols)
        self.assertIn(("ETHUSDT", "USD_M_PERPETUAL"), symbols)
        self.assertNotIn(("BTCUSDT", "SPOT"), symbols)

    def test_legacy_binance_risk_refresh_units_are_retired(self):
        installer=(ROOT/"deploy/install_gce_free_worker.sh").read_text(encoding="utf-8")
        self.assertIn(
            "agca-binance-risk-refresh.service agca-binance-risk-refresh.timer",
            installer,
        )
        canonical = installer.split("canonical_units=(", 1)[1].split(")", 1)[0]
        active = installer.split("active_timers=(", 1)[1].split(")", 1)[0]
        self.assertNotIn("agca-binance-risk-refresh.service", canonical)
        self.assertNotIn("agca-binance-risk-refresh.timer", active)

    def test_noncanonical_copy_trader_runtime_is_retired(self):
        installer=(ROOT/"deploy/install_gce_free_worker.sh").read_text(encoding="utf-8")
        self.assertIn(
            "agca-okx-copy-trader-collector.service "
            "agca-okx-copy-trader-collector.timer",
            installer,
        )
        canonical=installer.split("canonical_units=(",1)[1].split(")",1)[0]
        active=installer.split("active_timers=(",1)[1].split(")",1)[0]
        self.assertNotIn("agca-okx-copy-trader-collector.service", canonical)
        self.assertNotIn("agca-okx-copy-trader-collector.timer", active)

    def test_active_behavior_and_risk_paths_do_not_use_restricted_futures_rest(self):
        for relative in (
            "scripts/collect_closed_behavior_minute.py",
            "data/binance_live_evidence.py",
            "risk_manager/risk_manager.py",
            "runtime/paper_realtime_worker.py",
        ):
            text=(ROOT/relative).read_text(encoding="utf-8")
            self.assertNotIn("fapi.binance.com", text, relative)
            self.assertNotIn("/fapi/v1/", text, relative)

    def test_active_binance_paths_do_not_use_retired_usdm_websocket_route(self):
        retired = "wss://fstream.binance.com/ws"
        for relative in (
            "data/binance_live_evidence.py",
            "data/binance_liquidation_channel.py",
            "paper_trading/realtime_market.py",
        ):
            text=(ROOT/relative).read_text(encoding="utf-8")
            self.assertNotIn(retired, text, relative)

    def test_behavior_observer_service_declares_canonical_binance_source(self):
        service=(ROOT/"deploy/agca-market-behavior-observer.service").read_text(
            encoding="utf-8"
        )
        self.assertIn("AGCA_BINANCE_WS_OPEN_TIMEOUT_SECONDS=10", service)
        self.assertNotIn("AGCA_BINANCE_REQUEST_TIMEOUT_SECONDS", service)

    def test_risk_refresh_is_support_only_and_uses_canonical_database(self):
        service=(ROOT/"deploy/agca-risk-refresh.service").read_text(encoding="utf-8")
        timer=(ROOT/"deploy/agca-risk-refresh.timer").read_text(encoding="utf-8")
        installer=(ROOT/"deploy/install_gce_free_worker.sh").read_text(encoding="utf-8")
        self.assertIn("-m risk_manager.risk_cycle", service)
        self.assertIn("AGCA_DATABASE_PATH=/var/lib/agca/crypto_data.db", service)
        self.assertIn("AGCA_EXECUTION_MODE=PAPER_ONLY", service)
        self.assertIn("AGCA_REMOTE_MIRROR_INLINE=0", service)
        self.assertIn("OnUnitActiveSec=15min", timer)
        self.assertIn("agca-risk-refresh.service", installer)
        self.assertIn("agca-risk-refresh.timer", installer)
        self.assertNotIn("runtime.paper_realtime_worker", service)

    def test_risk_manager_database_path_is_environment_bound(self):
        text=(ROOT/"risk_manager/risk_manager.py").read_text(encoding="utf-8")
        self.assertIn(
            'DB_NAME = os.getenv("AGCA_DATABASE_PATH", "crypto_data.db")',
            text,
        )

    def test_realtime_worker_remains_paper_only(self):
        text=(ROOT/"deploy/agca-paper-realtime-worker.service").read_text(encoding="utf-8")
        self.assertIn("AGCA_EXECUTION_MODE=PAPER_ONLY",text)
        self.assertIn("-m runtime.paper_realtime_worker",text)

if __name__=="__main__":
    unittest.main()
