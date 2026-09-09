import json
import hashlib
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paper_trading.exchange_metadata import (
    ExchangeMetadataError, OFFICIAL_ENDPOINTS, load_cache, parse_exchange_info,
    refresh_cache_via_postgres, rules_for,
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


if __name__ == "__main__":
    unittest.main()
