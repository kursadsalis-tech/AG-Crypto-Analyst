import unittest
from unittest import mock

from decision_engine import hourly_forecast_job
from paper_trading.exchange_metadata import ExchangeMetadataError


class HourlyMetadataFallbackTests(unittest.TestCase):
    def test_live_failure_uses_only_validated_fresh_cache(self):
        cached = mock.sentinel.cached
        with mock.patch.object(
            hourly_forecast_job,
            "refresh_cache_via_postgres",
            side_effect=ExchangeMetadataError("remote unavailable"),
        ):
            with mock.patch.object(
                hourly_forecast_job, "load_cache", return_value=cached
            ) as load:
                result, transport = hourly_forecast_job._refresh_exchange_metadata("cache.json")
        self.assertIs(result, cached)
        self.assertEqual(transport, "FRESH_VERIFIED_CACHE")
        load.assert_called_once_with(
            "cache.json", "USD_M_PERPETUAL", max_age_seconds=7200.0
        )

    def test_stale_or_invalid_cache_remains_fail_closed(self):
        with mock.patch.object(
            hourly_forecast_job,
            "refresh_cache_via_postgres",
            side_effect=ExchangeMetadataError("remote unavailable"),
        ):
            with mock.patch.object(
                hourly_forecast_job,
                "load_cache",
                side_effect=ExchangeMetadataError("STALE_METADATA_CACHE"),
            ):
                with self.assertRaisesRegex(ExchangeMetadataError, "STALE_METADATA_CACHE"):
                    hourly_forecast_job._refresh_exchange_metadata("cache.json")


if __name__ == "__main__":
    unittest.main()
