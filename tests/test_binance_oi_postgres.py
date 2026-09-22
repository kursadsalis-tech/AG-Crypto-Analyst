"""No-network validation for bounded official Binance OI via existing DB egress."""
import unittest

from data.binance_oi_postgres import (
    BinanceOIError, MAX_BATCH, validate_response, fetch_batch,
)


NOW = 1_800_000_000_000


class BinanceOIValidationTests(unittest.TestCase):
    def test_exact_contract_and_time_are_required(self):
        result = validate_response({
            "symbol": "1000PEPEUSDT", "openInterest": "26512013833",
            "time": NOW-500,
        }, symbol="1000PEPEUSDT", received_ms=NOW, rtt_ms=800)
        self.assertEqual(result["symbol"], "1000PEPEUSDT")
        self.assertEqual(result["open_interest_native"], 26512013833.)
        self.assertEqual(result["age_ms"], 500)
        self.assertEqual(result["source_exchange"], "BINANCE_USD_M")

    def test_stale_future_nan_alias_and_slow_oi_fail_closed(self):
        good = {"symbol": "1000PEPEUSDT", "openInterest": "10", "time": NOW-200}
        for update, stamp, rtt in (
            ({"symbol": "PEPEUSDT"}, NOW, 300),
            ({"time": NOW-15001}, NOW, 300),
            ({"time": NOW+1}, NOW, 300),
            ({"openInterest": "nan"}, NOW, 300),
            ({"openInterest": "-1"}, NOW, 300),
            ({}, NOW, 12001),
        ):
            with self.subTest(update=update):
                response = {**good, **update}
                with self.assertRaises(BinanceOIError):
                    validate_response(response, symbol="1000PEPEUSDT",
                                      received_ms=stamp, rtt_ms=rtt)

    def test_batch_checks_bound_and_authoritative_ids_before_connect(self):
        allowed = {"BTCUSDT", "1000PEPEUSDT"}
        for symbols in (
            ("PEPEUSDT",),
            ("BTCUSDT", "BTCUSDT"),
            tuple(f"{i}USDT" for i in range(MAX_BATCH+1)),
            ("BTC-USDT-SWAP",),
            ("BTCUSD",),
            (),
        ):
            with self.subTest(symbols=symbols):
                with self.assertRaises(BinanceOIError):
                    fetch_batch("not-a-database-url", symbols, allowed)

    def test_empty_dsn_or_oversized_timeout_fails_before_db(self):
        with self.assertRaisesRegex(BinanceOIError, "DATABASE_URL_UNAVAILABLE"):
            fetch_batch("", ("BTCUSDT",), {"BTCUSDT"})
        with self.assertRaisesRegex(BinanceOIError, "UNBOUNDED_OI_TIMEOUT"):
            fetch_batch("not-a-database-url", ("BTCUSDT",), {"BTCUSDT"},
                        timeout_seconds=120)


if __name__ == "__main__":
    unittest.main()
