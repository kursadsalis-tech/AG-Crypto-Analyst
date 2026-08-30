import sqlite3
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from data import okx_copy_trader_collector as collector
from paper_trading import external_trader_learning as external


class OkxCopyTraderCollectorTests(unittest.TestCase):
    def test_history_refresh_prioritizes_known_open_traders(self):
        con = sqlite3.connect(":memory:")
        external.ensure_tables(con)
        ranks = {f"trader-{index}": index + 1 for index in range(40)}
        for index in range(35):
            external.ingest_trade(con, {
                "exchange": "OKX", "source_url": collector.SOURCE_URL,
                "source_trader_id": f"trader-{index}",
                "source_trade_id": f"open-{index}",
                "sampling_frame": "FULL_LEADERBOARD",
                "leaderboard_rank": index + 1, "leaderboard_size": 40,
                "symbol": "BTCUSDT", "direction": "LONG", "status": "OPEN",
                "entry_time": "2026-08-25T00:00:00+00:00",
                "entry_price": 100.0, "notional": 100.0, "leverage": 2.0,
                "reported_pnl": 0.0, "fees": 0.1, "funding": 0.0,
            }, first_observed_at="2026-08-26T00:00:00+00:00")
            # Existing retrospective history must not suppress closure refresh.
            external.ingest_trade(con, {
                "exchange": "OKX", "source_url": collector.SOURCE_URL,
                "source_trader_id": f"trader-{index}",
                "source_trade_id": f"old-{index}",
                "sampling_frame": "FULL_LEADERBOARD",
                "leaderboard_rank": index + 1, "leaderboard_size": 40,
                "symbol": "ETHUSDT", "direction": "LONG", "status": "CLOSED",
                "entry_time": "2026-08-20T00:00:00+00:00",
                "exit_time": "2026-08-21T00:00:00+00:00",
                "entry_price": 100.0, "exit_price": 101.0, "notional": 100.0,
                "leverage": 2.0, "reported_pnl": 1.0,
                "fees": 0.1, "funding": 0.0,
            }, first_observed_at="2026-08-26T00:00:00+00:00")
        selected = collector._history_profiles_for_run(
            con, ranks, datetime(2026, 8, 27, tzinfo=timezone.utc)
        )
        batches = [selected]
        for offset in range(1, 70):
            batches.append(collector._history_profiles_for_run(
                con, ranks,
                datetime(2026, 8, 27, tzinfo=timezone.utc) + timedelta(minutes=30 * offset),
            ))
        con.close()
        self.assertEqual(len(selected), collector.MAX_HISTORY_PROFILES_PER_RUN)
        self.assertTrue(any(
            int(trader.split("-")[1]) < 35
            for batch in batches for trader, _ in batch
        ))
        self.assertEqual(
            {trader for batch in batches for trader, _ in batch},
            set(ranks),
        )

    def test_history_refresh_uses_remaining_capacity_for_new_backfill(self):
        con = sqlite3.connect(":memory:")
        external.ensure_tables(con)
        ranks = {"open": 1, "new-a": 2, "new-b": 3}
        external.ingest_trade(con, {
            "exchange": "OKX", "source_url": collector.SOURCE_URL,
            "source_trader_id": "open", "source_trade_id": "position",
            "sampling_frame": "FULL_LEADERBOARD", "leaderboard_rank": 1,
            "leaderboard_size": 3, "symbol": "BTCUSDT", "direction": "LONG",
            "status": "OPEN", "entry_time": "2026-08-25T00:00:00+00:00",
            "entry_price": 100.0, "notional": 100.0, "leverage": 2.0,
            "reported_pnl": 0.0, "fees": 0.1, "funding": 0.0,
        }, first_observed_at="2026-08-26T00:00:00+00:00")
        selected = []
        for offset in range(4):
            selected.extend(collector._history_profiles_for_run(
                con, ranks,
                datetime(2026, 8, 27, tzinfo=timezone.utc) + timedelta(minutes=30 * offset),
            ))
        con.close()
        self.assertEqual({trader for trader, _ in selected}, set(ranks))

    def page(self, number, *, version="v1", total=7):
        start = (number - 1) * collector.PAGE_SIZE
        count = min(collector.PAGE_SIZE, total - start)
        return {
            "dataVersion": version, "pages": 2, "total": total,
            "ranks": [{"uniqueName": f"trader-{start + i}"} for i in range(count)],
        }

    def test_full_cohort_requires_every_unique_trader(self):
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), self.page(2)]), \
             patch.object(collector.time, "sleep"):
            result = collector.fetch_full_cohort()
        self.assertEqual(result["total"], 7)
        self.assertEqual(len(result["rows"]), 7)

    def test_version_change_fails_closed(self):
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), self.page(2, version="v2")]), \
             patch.object(collector.time, "sleep"):
            with self.assertRaises(collector.PublicCopyTraderDataError):
                collector.fetch_full_cohort()

    def test_duplicate_trader_fails_closed(self):
        second = self.page(2)
        second["ranks"][0]["uniqueName"] = "trader-0"
        with patch.object(collector, "_fetch_page", side_effect=[self.page(1), second]), \
             patch.object(collector.time, "sleep"):
            with self.assertRaises(collector.PublicCopyTraderDataError):
                collector.fetch_full_cohort()

    def test_history_normalizes_costs_and_linear_notional(self):
        row = {
            "instId": "BTC-USDT-SWAP", "posSide": "long",
            "cTime": "1000", "uTime": "2000", "openAvgPx": "100",
            "closeAvgPx": "110", "openMaxAmount": "2", "ctVal": "0.01",
            "quoteCcy": "USDT", "lever": "3", "realizedPnl": "1.5",
            "pnlRatio": "0.1", "fee": "-0.2", "fundingFee": "-0.1",
        }
        result = collector._history_payload("trader", row, 1, 255)
        self.assertEqual(result["symbol"], "BTCUSDT")
        self.assertEqual(result["direction"], "LONG")
        self.assertEqual(result["notional"], 2.0)
        self.assertEqual(result["fees"], 0.2)
        self.assertEqual(result["reported_roi_pct"], 10.0)

    def test_history_keeps_known_open_closure_and_bounds_retrospective_rows(self):
        con = sqlite3.connect(":memory:")
        external.ensure_tables(con)
        known_row = {
            "instId": "BTC-USDT-SWAP", "posSide": "long", "cTime": "1000",
            "uTime": "2000", "openAvgPx": "100", "closeAvgPx": "101",
            "openMaxAmount": "1", "ctVal": "0.01", "quoteCcy": "USDT",
            "lever": "2", "realizedPnl": "1", "pnlRatio": "0.01",
            "fee": "-0.1", "fundingFee": "0",
        }
        opened = collector._current_payload("trader", {
            "instId": known_row["instId"], "posSide": "long",
            "cTime": known_row["cTime"], "avgPx": "100", "notionalUsd": "100",
            "lever": "2", "realizedPnl": "0", "fee": "-0.1", "fundingFee": "0",
        }, 1, 100)
        external.ingest_trade(con, opened, first_observed_at="2026-08-26T00:00:00+00:00")
        rows = []
        for index in range(50):
            item = dict(known_row)
            item["cTime"] = str(3000 + index)
            rows.append(item)
        rows.append(known_row)
        payloads = collector._bounded_history_payloads(con, "trader", rows, 1, 100)
        con.close()
        ids = {payload["source_trade_id"] for payload in payloads}
        self.assertIn(opened["source_trade_id"], ids)
        self.assertEqual(
            len(payloads), collector.MAX_RETROSPECTIVE_HISTORY_TRADES_PER_PROFILE + 1
        )

    def test_profile_fetch_is_bounded_and_isolates_one_failure(self):
        def fetch(_endpoint, trader):
            if trader == "bad":
                raise collector.PublicCopyTraderDataError("blocked")
            return [{"trader": trader}]

        with patch.object(collector, "_fetch_profile", side_effect=fetch):
            profiles, errors = collector._fetch_profiles_bounded(
                "endpoint", [("good", 1), ("bad", 2)], workers=1
            )
        self.assertEqual(profiles["good"], (1, [{"trader": "good"}]))
        self.assertEqual(errors, [{
            "trader": "bad", "error": "PublicCopyTraderDataError",
        }])

    def test_profile_request_has_short_bounded_timeout(self):
        response = io.BytesIO(b'{"code":"0","data":[]}')
        response.__enter__ = lambda value: value
        response.__exit__ = lambda *_args: None
        with patch.object(collector.urllib.request, "urlopen", return_value=response) as opened:
            collector._fetch_profile("https://example.test/profile", "trader")
        self.assertEqual(
            opened.call_args.kwargs["timeout"],
            collector.PROFILE_REQUEST_TIMEOUT_SECONDS,
        )

    def test_persisted_current_queue_covers_full_cohort_without_duplicates(self):
        ranks = {f"trader-{index}": index + 1 for index in range(260)}
        con = sqlite3.connect(":memory:")
        selected = []
        now = datetime(2026, 8, 30, tzinfo=timezone.utc)
        batch_count = 260 // collector.MAX_CURRENT_PROFILES_PER_RUN + 1
        for _ in range(batch_count):
            batch, next_index = collector._current_profiles_for_run(
                con, ranks, now.isoformat(), now
            )
            selected.extend(batch)
            collector._advance_current_queue(con, now.isoformat(), next_index, now)
        traders = [trader for trader, _ in selected]
        con.close()
        self.assertEqual(len(traders), 260)
        self.assertEqual(len(set(traders)), 260)

    def test_collector_does_not_duplicate_hourly_improvement_work(self):
        self.assertNotIn("continuous_improvement", collector.__dict__)


if __name__ == "__main__":
    unittest.main()
