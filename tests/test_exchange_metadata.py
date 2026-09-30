import json
import hashlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_trading.exchange_metadata import (
    ExchangeMetadataError, OFFICIAL_ENDPOINTS, load_cache, parse_exchange_info,
    refresh_cache_via_postgres, rules_for, load_market_caches, refresh_cache_bounded,
    freeze_execution_rules, rules_from_frozen, CachedExchangeMetadata,
)


def payload(status="TRADING", contract_type="PERPETUAL"):
    return {"symbols": [{
        "symbol": "BTCUSDT", "status": status, "contractType": contract_type,
        "quoteAsset": "USDT", "liquidationFee": "0.0125", "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
            {"filterType": "LOT_SIZE", "stepSize": "0.001"},
            {"filterType": "MIN_NOTIONAL", "notional": "100"},
        ],
    }]}


class ExchangeMetadataTests(unittest.TestCase):
    def test_extracts_exact_perpetual_rules(self):
        rule = rules_for(type("Cache", (), {"symbols": parse_exchange_info(payload(), "USD_M_PERPETUAL")})(), "btcusdt")
        self.assertEqual(rule.price_tick, 0.1)
        self.assertEqual(rule.quantity_step, 0.001)
        self.assertEqual(rule.min_notional, 100.0)
        self.assertEqual(rule.liquidation_fee_rate, 0.0125)

    def test_rejects_wrong_contract_and_missing_filter(self):
        with self.assertRaisesRegex(ExchangeMetadataError, "NO_ELIGIBLE"):
            parse_exchange_info(payload(contract_type="CURRENT_QUARTER"), "USD_M_PERPETUAL")
        broken = payload()
        broken["symbols"][0]["filters"].pop()
        with self.assertRaisesRegex(ExchangeMetadataError, "MISSING_REQUIRED_FILTER"):
            parse_exchange_info(broken, "USD_M_PERPETUAL")

    def test_cache_is_fail_closed_on_stale_or_wrong_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exchange.json"
            base = {
                "schema_version": 1, "market": "USD_M_PERPETUAL",
                "source_url": OFFICIAL_ENDPOINTS["USD_M_PERPETUAL"],
                "fetched_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                "payload_sha256": hashlib.sha256(
                    json.dumps(payload(), separators=(",", ":")).encode()
                ).hexdigest(), "payload": payload(),
            }
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(ExchangeMetadataError, "STALE"):
                load_cache(path, "USD_M_PERPETUAL", max_age_seconds=60)
            base["fetched_at"] = datetime.now(timezone.utc).isoformat()
            base["source_url"] = OFFICIAL_ENDPOINTS["SPOT"]
            path.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(ExchangeMetadataError, "PROVENANCE"):
                load_cache(path, "USD_M_PERPETUAL")

    def test_cache_rejects_payload_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "exchange.json"
            document = {
                "schema_version": 1, "market": "USD_M_PERPETUAL",
                "source_url": OFFICIAL_ENDPOINTS["USD_M_PERPETUAL"],
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "payload_sha256": "0" * 64, "payload": payload(),
            }
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ExchangeMetadataError, "DIGEST_MISMATCH"):
                load_cache(path, "USD_M_PERPETUAL")

    def test_supabase_transport_writes_validated_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = MagicMock()
            cursor.fetchone.return_value = (200, "application/json", json.dumps(payload()))
            connection = MagicMock()
            connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
            fake_psycopg = MagicMock()
            fake_psycopg.connect.return_value = connection
            with patch.dict("sys.modules", {"psycopg": fake_psycopg}):
                cache = refresh_cache_via_postgres(
                    Path(directory) / "cache.json", "USD_M_PERPETUAL", "postgres://example",
                )
            self.assertEqual(rules_for(cache, "BTCUSDT").min_notional, 100)
            self.assertEqual(cursor.execute.call_count, 4)
            self.assertIn("statement_timeout", cursor.execute.call_args_list[0].args[0])
            option_calls = cursor.execute.call_args_list[1:3]
            self.assertIn("CURLOPT_CONNECTTIMEOUT_MS", option_calls[0].args[0])
            self.assertIn("CURLOPT_TIMEOUT_MS", option_calls[1].args[0])
            sql, parameters = cursor.execute.call_args.args
            self.assertNotIn("postgres://example", sql)
            self.assertEqual(parameters, (OFFICIAL_ENDPOINTS["USD_M_PERPETUAL"],))
            self.assertEqual(
                fake_psycopg.connect.call_args.kwargs["tcp_user_timeout"], 20000
            )

    def test_supabase_transport_accepts_json_charset_parameter(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = MagicMock()
            cursor.fetchone.return_value = (
                200, "application/json;charset=UTF-8", json.dumps(payload()),
            )
            connection = MagicMock()
            connection.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
            fake_psycopg = MagicMock()
            fake_psycopg.connect.return_value = connection
            with patch.dict("sys.modules", {"psycopg": fake_psycopg}):
                cache = refresh_cache_via_postgres(
                    Path(directory) / "cache.json", "SPOT", "postgres://example",
                )
            self.assertEqual(rules_for(cache, "BTCUSDT").min_notional, 100)


class IsolatedMetadataTests(unittest.TestCase):
    def write_cache(self, path, market, age_seconds=0):
        data = payload()
        path.write_text(json.dumps({
            "schema_version": 1, "market": market,
            "source_url": OFFICIAL_ENDPOINTS[market],
            "fetched_at": (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat(),
            "payload_sha256": hashlib.sha256(
                json.dumps(data, separators=(",", ":")).encode()
            ).hexdigest(), "payload": data,
        }))

    def test_stale_market_never_removes_other_route(self):
        for stale, healthy in (("SPOT", "USD_M_PERPETUAL"), ("USD_M_PERPETUAL", "SPOT")):
            with self.subTest(stale=stale), tempfile.TemporaryDirectory() as directory:
                paths = {market: Path(directory) / (market + ".json") for market in OFFICIAL_ENDPOINTS}
                for market, path in paths.items():
                    self.write_cache(path, market, age_seconds=7201 if market == stale else 0)
                available, health = load_market_caches(paths)
                self.assertEqual(set(available), {healthy})
                self.assertEqual(health[healthy]["status"], "READY")
                self.assertEqual(health[stale]["reason"], "STALE_METADATA_CACHE")
                self.assertEqual(rules_for(available[healthy], "BTCUSDT").min_notional, 100)

    def test_corrupt_or_missing_market_is_bounded_route_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt.json"
            path.write_text("[]")
            available, health = load_market_caches({"SPOT": path, "USD_M_PERPETUAL": path.parent / "missing"})
            self.assertEqual(available, {})
            self.assertEqual(health["SPOT"]["reason"], "INVALID_METADATA_SCHEMA")
            self.assertEqual(health["USD_M_PERPETUAL"]["reason"], "METADATA_CACHE_UNAVAILABLE")

    def test_in_memory_rules_expire_without_process_restart(self):
        cache = CachedExchangeMetadata(
            "SPOT", OFFICIAL_ENDPOINTS["SPOT"],
            (datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),
            "a" * 64, parse_exchange_info(payload(), "SPOT"), max_age_seconds=1,
        )
        with self.assertRaisesRegex(ExchangeMetadataError, "STALE_METADATA_CACHE"):
            rules_for(cache, "BTCUSDT")

    def test_direct_primary_does_not_depend_on_database_transport(self):
        with patch("paper_trading.exchange_metadata.refresh_cache", return_value="VALID") as direct, patch(
            "paper_trading.exchange_metadata.refresh_cache_via_postgres"
        ) as fallback:
            self.assertEqual(refresh_cache_bounded("cache", "SPOT"), "VALID")
            direct.assert_called_once_with("cache", "SPOT", timeout_seconds=10)
            fallback.assert_not_called()

    def test_direct_failure_uses_one_bounded_optional_database_fallback(self):
        with patch("paper_trading.exchange_metadata.refresh_cache", side_effect=ExchangeMetadataError("DOWN")), patch(
            "paper_trading.exchange_metadata.refresh_cache_via_postgres", return_value="VALID"
        ) as fallback:
            self.assertEqual(refresh_cache_bounded("cache", "SPOT", "private-test-dsn"), "VALID")
            fallback.assert_called_once_with("cache", "SPOT", "private-test-dsn", timeout_seconds=10)

    def test_all_transports_fail_without_overwriting_previous_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            path.write_text("existing-cache")
            with patch("paper_trading.exchange_metadata.refresh_cache", side_effect=ExchangeMetadataError("DOWN")), patch(
                "paper_trading.exchange_metadata.refresh_cache_via_postgres", side_effect=ExchangeMetadataError("DOWN")
            ):
                with self.assertRaisesRegex(ExchangeMetadataError, "ALL_METADATA_TRANSPORTS_UNAVAILABLE"):
                    refresh_cache_bounded(path, "SPOT", "private-test-dsn")
            self.assertEqual(path.read_text(), "existing-cache")

    def test_frozen_contract_roundtrip_and_wrong_route_or_schema_fail(self):
        rules = parse_exchange_info(payload(), "USD_M_PERPETUAL")["BTCUSDT"]
        frozen = freeze_execution_rules(rules, "BTCUSDT", "USD_M_PERPETUAL", maintenance_margin_rate=.005)
        self.assertEqual(rules_from_frozen(frozen, "BTCUSDT", "USD_M_PERPETUAL"), rules)
        self.assertEqual(frozen["maintenance_margin_rate"], .005)
        for invalid in ({**frozen, "market": "SPOT"}, {**frozen, "contract_version": "OLD"},
                        {**frozen, "rules": {**frozen["rules"], "liquidation_fee_rate": float("nan")}},
                        {**frozen, "rules": {**frozen["rules"], "quantity_step": -1}}):
            with self.subTest(invalid=invalid), self.assertRaises(ExchangeMetadataError):
                rules_from_frozen(invalid, "BTCUSDT", "USD_M_PERPETUAL")

    def test_refresh_failure_does_not_skip_other_market(self):
        from scripts import refresh_exchange_metadata as refresh
        with tempfile.TemporaryDirectory() as directory:
            health_path = Path(directory) / "health.json"
            fake = CachedExchangeMetadata("SPOT", OFFICIAL_ENDPOINTS["SPOT"],
                datetime.now(timezone.utc).isoformat(), "a" * 64,
                parse_exchange_info(payload(), "SPOT"))
            with patch.dict("os.environ", {
                "AGCA_EXCHANGE_METADATA_HEALTH": str(health_path),
                "AGCA_EXCHANGE_METADATA_CACHE": str(Path(directory) / "usdm.json"),
                "AGCA_SPOT_EXCHANGE_METADATA_CACHE": str(Path(directory) / "spot.json"),
            }), patch.object(refresh, "refresh_cache_bounded", side_effect=[ExchangeMetadataError("DOWN"), fake]) as fetch, patch("builtins.print"):
                self.assertEqual(refresh.main(), 1)
            health = json.loads(health_path.read_text())
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(health["markets"]["SPOT"]["status"], "READY")
            self.assertEqual(health["markets"]["USD_M_PERPETUAL"]["refresh_status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
