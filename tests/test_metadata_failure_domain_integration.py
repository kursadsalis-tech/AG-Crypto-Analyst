"""Offline integration: actual fill contract survives independent route failures."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from paper_trading import crypto_profit_agent as agent, position_management
from paper_trading.exchange_metadata import load_market_caches, OFFICIAL_ENDPOINTS, rules_for
from tests import test_behavior_paper_integration as fixtures


class MetadataFailureDomainIntegrationTests(unittest.TestCase):
    def setUp(self):
        # Reuse causal technical-plan fixtures without inheriting/re-running
        # unrelated integration tests. The real metadata loader/rule lookup and
        # complete selection -> actual fill -> manager accounting run below.
        self.fixture = fixtures.BehaviorPaperIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.con = self.fixture.con
        self.con.execute("""CREATE TABLE IF NOT EXISTS asset_risk_snapshots(
            id INTEGER PRIMARY KEY,symbol TEXT,created_at TEXT,
            volatility_stop_reference_pct REAL)""")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.paths = {market: Path(self.directory.name) / (market + ".json")
                      for market in OFFICIAL_ENDPOINTS}
        for market in self.paths:
            self.write_cache(market)
        self.rule_lookup = patch.object(agent, "rules_for", wraps=rules_for)
        self.rule_lookup.start()
        self.addCleanup(self.rule_lookup.stop)

    def write_cache(self, market, *, stale=False, malformed=False):
        payload = {"symbols": [{
            "symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL",
            "quoteAsset": "USDT", "liquidationFee": "0.0125", "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.000001"},
                {"filterType": "LOT_SIZE", "stepSize": "0.000001"},
                {"filterType": "MIN_NOTIONAL", "notional": "5"},
            ],
        }]}
        if malformed:
            payload["symbols"][0]["filters"] = [None]
        self.paths[market].write_text(json.dumps({
            "schema_version": 1, "market": market, "source_url": OFFICIAL_ENDPOINTS[market],
            "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=3 if stale else 0)).isoformat(),
            "payload": payload,
            "payload_sha256": hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest(),
        }))

    def open_actual_position(self, market):
        metadata, health = load_market_caches(self.paths)
        self.assertEqual(health[market]["status"], "READY")
        candidate = self.fixture.candidate()
        if market == "USD_M_PERPETUAL":
            candidate.update(direction="SHORT", target_price=99.7)
        with patch.object(agent.behavior_decision_core, "evaluate", return_value={
            "status": "ACTIONABLE", "candidates": [candidate], "review": {},
        }):
            self.assertEqual(agent.select_new_positions(self.con, fixtures.NOW, metadata), 1)
        selected = self.con.execute("SELECT market_type FROM paper_agent_positions").fetchone()[0]
        self.assertEqual(selected, market)
        at = fixtures.NOW + timedelta(seconds=1)
        live = self.fixture.live_state("BTCUSDT", at, market_type=market)
        self.assertEqual(agent.fill_pending_from_live_state(self.con, at, live, metadata), (1, 0))
        self.entry_evidence = self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='ENTRY_FILLED'").fetchone()[0]
        evidence = json.loads(self.entry_evidence)
        frozen = evidence["execution_rules"]
        position = self.con.execute("SELECT notional,quantity,entry_fee,entry_price FROM paper_agent_positions").fetchone()
        self.assertEqual(evidence["notional"], position[0])
        self.assertEqual(evidence["quantity"], position[1])
        self.assertEqual(evidence["fee"], position[2])
        self.assertEqual(evidence["final_simulated_fill_price"], position[3])
        self.assertEqual(frozen["market"], market)
        self.assertEqual(frozen["rules"]["liquidation_fee_rate"], .0125)
        self.assertEqual(frozen["metadata"]["source_url"], OFFICIAL_ENDPOINTS[market])
        self.assertGreaterEqual(self.con.execute("SELECT notional FROM paper_agent_positions").fetchone()[0], 100)
        initialized = position_management.initialize_open_positions(self.con, at)
        self.assertEqual(initialized["initialized"], 1)
        self.assertEqual(initialized["blocked"], [])
        return at

    def assert_other_route_stale_then_own_route_missing_exit(self, market, *, liquidation=False):
        at = self.open_actual_position(market)
        other_market = "SPOT" if market == "USD_M_PERPETUAL" else "USD_M_PERPETUAL"
        self.write_cache(other_market, stale=True)
        metadata, health = load_market_caches(self.paths)
        self.assertEqual(health[other_market]["reason"], "STALE_METADATA_CACHE")
        self.assertEqual(health[market]["status"], "READY")
        # Healthy route management continues while the unrelated route is stale.
        at += timedelta(seconds=1)
        hold = position_management.process_market_state(self.con,
            self.fixture.live_state("BTCUSDT", at, market_type=market), at,
            rules_for(metadata[market], "BTCUSDT"))
        self.assertEqual(hold["status"], "HOLD")
        # Then even the position's own helper disappears: immutable actual-fill
        # rules still protect it, using a fresh exact-route executable quote.
        self.paths[market].unlink()
        metadata, health = load_market_caches(self.paths)
        self.assertEqual(metadata, {})
        at += timedelta(seconds=1)
        if liquidation:
            threshold = self.con.execute("SELECT liquidation_price FROM paper_agent_positions").fetchone()[0]
            price = threshold * 1.01
        else:
            price = 103 if market == "USD_M_PERPETUAL" else 97
        result = position_management.process_market_state(self.con,
            self.fixture.live_state("BTCUSDT", at, market_type=market, price=price), at, None)
        self.assertEqual(result["status"], "POSITION_CLOSED")
        self.assertEqual(result["reason"], "PAPER_1X_LIQUIDATION" if liquidation else "PROTECTIVE_STOP")
        self.assertEqual(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='ENTRY_FILLED'").fetchone()[0], self.entry_evidence)
        exit_evidence = json.loads(self.con.execute("SELECT evidence_json FROM paper_position_events WHERE event_type='POSITION_CLOSED'").fetchone()[0])
        self.assertEqual(exit_evidence["execution_rules_source"], "FROZEN_ENTRY_RULES")
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM paper_position_events WHERE real_order_allowed<>0").fetchone()[0], 0)
        if liquidation:
            fee, exit_price, quantity = self.con.execute("SELECT liquidation_fee,exit_price,quantity FROM paper_agent_positions").fetchone()
            self.assertAlmostEqual(fee, exit_price * quantity * .0125)

    def test_actual_usdm_short_fill_survives_stale_spot_then_missing_own_rules(self):
        self.assert_other_route_stale_then_own_route_missing_exit("USD_M_PERPETUAL")

    def test_actual_spot_fill_survives_stale_usdm_then_missing_own_rules(self):
        self.assert_other_route_stale_then_own_route_missing_exit("SPOT")

    def test_actual_usdm_liquidation_keeps_frozen_fee_when_both_helpers_fail(self):
        self.assert_other_route_stale_then_own_route_missing_exit("USD_M_PERPETUAL", liquidation=True)

    def test_malformed_symbol_schema_disables_only_its_route(self):
        self.write_cache("SPOT", malformed=True)
        metadata, health = load_market_caches(self.paths)
        self.assertEqual(set(metadata), {"USD_M_PERPETUAL"})
        self.assertEqual(health["SPOT"]["reason"], "INVALID_EXCHANGE_INFO_FILTERS")
        self.assertEqual(health["USD_M_PERPETUAL"]["status"], "READY")


if __name__ == "__main__":
    unittest.main()
