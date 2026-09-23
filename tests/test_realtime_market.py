import unittest
import asyncio
import json
import inspect
import threading
import time
from collections import deque
from unittest.mock import patch

from paper_trading import realtime_market
from paper_trading.realtime_market import LiveState, apply_message, subscription_args


NOW = 1_788_000_000_000


class RealtimeMarketTests(unittest.TestCase):
    def test_spot_quote_transport_prefers_current_official_binance_endpoint(self):
        self.assertEqual(
            realtime_market.BINANCE_SPOT_WS_URLS[0],
            "wss://stream.binance.com:9443/ws",
        )
        self.assertIn(
            "wss://data-stream.binance.vision/ws",
            realtime_market.BINANCE_SPOT_WS_URLS,
        )
        self.assertEqual(
            realtime_market.binance_subscription_params(
                [("BTCUSDT", "SPOT")], "SPOT", channels=("ticker",)
            ),
            ["btcusdt@ticker"],
        )

    def test_spot_stream_cannot_wait_forever_without_market_data(self):
        source = inspect.getsource(realtime_market._binance_stream)
        self.assertIn("BINANCE_SPOT_NO_MARKET_DATA", source)
        self.assertIn("BINANCE_SPOT_SILENCE_TIMEOUT_SECONDS", source)
        self.assertIn("spot_endpoint_index", source)

    def test_subscribes_execution_channels_for_dynamic_symbols(self):
        args = subscription_args(["CRVUSDT", "SOLUSDT"])
        self.assertEqual(len(args), 6)
        self.assertIn({"channel": "trades", "instId": "CRV-USDT-SWAP"}, args)

    def test_spot_position_subscribes_to_spot_instrument(self):
        args = subscription_args([("BTCUSDT", "SPOT")])
        self.assertEqual(len(args), 3)
        self.assertIn({"channel": "books5", "instId": "BTC-USDT"}, args)
        states = {}
        message = {"arg": {"channel": "tickers", "instId": "BTC-USDT"},
                   "data": [{"ts": str(NOW), "last": "100", "bidPx": "99.9",
                             "askPx": "100.1"}]}
        state = apply_message(states, message, NOW)[0]
        self.assertEqual((state.symbol, state.market_type), ("BTCUSDT", "SPOT"))

    def test_stale_event_is_rejected(self):
        states = {}
        message = {"arg": {"channel": "tickers", "instId": "CRV-USDT-SWAP"},
                   "data": [{"ts": str(NOW - 3000), "last": "1", "bidPx": ".9",
                             "askPx": "1.1"}]}
        self.assertEqual(apply_message(states, message, NOW), [])

    def test_book_and_trades_form_live_thesis_state(self):
        states = {}
        book = {"arg": {"channel": "books5", "instId": "CRV-USDT-SWAP"},
                "data": [{"ts": str(NOW), "bids": [["10", "3", "0", "1"]],
                          "asks": [["11", "1", "0", "1"]]}]}
        trades = {"arg": {"channel": "trades", "instId": "CRV-USDT-SWAP"},
                  "data": [{"ts": str(NOW), "px": "10", "sz": "2", "side": "buy"}]}
        apply_message(states, book, NOW)
        apply_message(states, trades, NOW)
        self.assertTrue(states["CRVUSDT"].thesis_supported("LONG"))
        self.assertFalse(states["CRVUSDT"].thesis_supported("SHORT"))

    def test_missing_flow_never_invents_thesis_support(self):
        self.assertIsNone(LiveState("BTCUSDT", last_price=100).thesis_supported("LONG"))

    def test_one_subscription_error_does_not_drop_other_symbols(self):
        class Socket:
            def __init__(self):
                self.messages = iter((
                    json.dumps({"event": "error", "code": "51001"}),
                    json.dumps({
                        "arg": {"channel": "tickers", "instId": "BTC-USDT-SWAP"},
                        "data": [{"ts": str(NOW), "last": "100", "bidPx": "99.9",
                                  "askPx": "100.1"}],
                    }),
                ))

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def send(self, _):
                return None

            async def recv(self):
                try:
                    return next(self.messages)
                except StopIteration:
                    stop.set()
                    await asyncio.sleep(0)
                    return json.dumps({})

        stop = asyncio.Event()
        seen, health = [], []

        async def on_state(state, _):
            seen.append(state.symbol)
            stop.set()

        with patch.object(realtime_market.websockets, "connect", return_value=Socket()), \
                patch.object(realtime_market.time, "time", return_value=NOW / 1000):
            asyncio.run(realtime_market._okx_stream(
                lambda: [("LAUSDT", "USD_M_PERPETUAL"), ("BTCUSDT", "USD_M_PERPETUAL")],
                on_state, stop, lambda state, error: health.append((state, error)),
            ))
        self.assertEqual(seen, ["BTCUSDT"])
        self.assertIn(("DEGRADED", "OKX_SUBSCRIPTION_ERROR_51001"), health)

    def test_binance_trade_does_not_refresh_quote_or_liquidity_timestamp(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        state = realtime_market.apply_binance_message(states, message, NOW)[0]
        self.assertEqual((state.bid_quote_volume, state.ask_quote_volume), (198, 303))
        realtime_market.apply_binance_message(states, {
            "e": "aggTrade", "s": "DYNAMICUSDT", "E": NOW+1000,
            "p": "778", "q": "1", "m": False,
        }, NOW+1000)
        self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(state.liquidity_timestamp_ms, NOW)
        self.assertEqual(state.market_timestamp_ms, NOW+1000)
        self.assertEqual(state.trade_timestamp_ms, NOW+1000)

    def test_futures_partial_depth_requires_provenance_and_complete_ordered_book(self):
        def book(**changes):
            value = {
                "e": "depthUpdate", "s": "BTCUSDT", "E": NOW,
                "st": 1, "ps": "BTCUSDT",
                "b": [[str(100 - i), "2"] for i in range(5)],
                "a": [[str(101 + i), "3"] for i in range(5)],
            }
            value.update(changes)
            return value

        states = {}
        self.assertEqual(realtime_market.apply_binance_message(
            states, book(), NOW, "USD_M_PERPETUAL",
        ), [])
        self.assertFalse(states)
        good = realtime_market.apply_binance_message(
            states, book(), NOW, "USD_M_PERPETUAL", partial_depth=True,
        )
        self.assertEqual(len(good), 1)
        state = good[0]
        self.assertEqual((state.best_bid, state.best_ask), (100, 101))
        self.assertEqual((state.bid_quote_volume, state.ask_quote_volume), (200, 303))
        self.assertEqual(state.visible_quote_volume, 503)
        self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(state.liquidity_timestamp_ms, NOW)
        self.assertEqual(state.quote_source, "BINANCE_USD_M_WS_PARTIAL_DEPTH5")
        self.assertEqual(state.last_price, 100.5)

        invalid = (
            book(b=[["100", "2"]]),
            book(a=[["101", "3"]]),
            book(b=[["100", "0"]] + [[str(100 - i), "2"] for i in range(1, 5)]),
            book(a=[["101", "3"], ["100", "3"], ["103", "3"], ["104", "3"], ["105", "3"]]),
            book(b=[["102", "2"]] + [[str(100 - i), "2"] for i in range(1, 5)]),
            book(st=2),
            book(ps="ETHUSDT"),
            book(E=NOW - 3000),
        )
        for bad in invalid:
            self.assertEqual(
                realtime_market.apply_binance_message(
                    states, bad, NOW, "USD_M_PERPETUAL", partial_depth=True,
                ), [],
            )
            self.assertEqual(state.best_bid, 100)
            self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(realtime_market.apply_binance_message(
            states, book(E=NOW + 600), NOW,
            "USD_M_PERPETUAL", partial_depth=True,
        ), [])

        # Same event and 5-level shape cannot be interpreted as Spot or diff
        # depth without the explicitly subscribed Futures partial channel.
        self.assertEqual(realtime_market.apply_binance_message(
            {}, book(), NOW, "SPOT", partial_depth=True,
        ), [])

    def test_live_futures_quote_subscription_uses_bounded_partial_depth(self):
        source = inspect.getsource(realtime_market.stream)
        self.assertIn('channels=("depth5@500ms",)', source)
        self.assertEqual(
            realtime_market.binance_subscription_params(
                ["BTCUSDT"], "USD_M_PERPETUAL", channels=("depth5@500ms",),
            ),
            ["btcusdt@depth5@500ms"],
        )
        self.assertEqual(
            realtime_market.BINANCE_USDM_WS_URL,
            "wss://fstream.binance.com/public/ws",
        )
        self.assertEqual(
            realtime_market.BINANCE_USDM_TRADE_WS_URL,
            "wss://fstream.binance.com/market/stream",
        )

    def test_incremental_depth_cannot_be_mistaken_for_best_book(self):
        states = {"DYNAMICUSDT": LiveState("DYNAMICUSDT", "SPOT", best_bid=99, best_ask=101,
                                        quote_timestamp_ms=NOW-3000)}
        self.assertEqual(realtime_market.apply_binance_message(states, {
            "e": "depthUpdate", "s": "DYNAMICUSDT", "E": NOW,
            "b": [["90", "2"]], "a": [["110", "3"]],
        }, NOW), [])
        self.assertEqual(states["DYNAMICUSDT"].quote_timestamp_ms, NOW-3000)

    def test_older_ticker_cannot_overwrite_newer_quote(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        realtime_market.apply_binance_message(states, message, NOW)
        self.assertEqual(realtime_market.apply_binance_message(
            states, {**message, "E": NOW-1, "a": "90"}, NOW), [])
        self.assertEqual(states["DYNAMICUSDT"].best_ask, 101)

    def test_missing_binance_side_clears_previous_side_instead_of_reusing_it(self):
        states = {}
        message = {"e": "24hrTicker", "s": "DYNAMICUSDT", "E": NOW,
                   "c": "777", "b": "99", "a": "101", "B": "2", "A": "3"}
        realtime_market.apply_binance_message(states, message, NOW)
        message.pop("a")
        message["E"] += 1
        state = realtime_market.apply_binance_message(states, message, NOW+1)[0]
        self.assertIsNone(state.best_ask)
        self.assertIsNone(state.ask_quote_volume)
        self.assertIsNone(state.visible_quote_volume)

    def test_binance_usdm_ticker_authorizes_side_specific_executable_liquidity(self):
        states = {}
        message = {"e": "24hrTicker", "s": "SOLUSDT", "E": NOW,
                   "c": "150", "b": "149.9", "a": "150.1", "B": "4", "A": "3"}
        state = realtime_market.apply_binance_message(
            states, message, NOW, market_type="USD_M_PERPETUAL"
        )[0]
        self.assertEqual(state.market_type, "USD_M_PERPETUAL")
        self.assertAlmostEqual(state.bid_quote_volume, 149.9 * 4)
        self.assertAlmostEqual(state.ask_quote_volume, 150.1 * 3)
        self.assertEqual(state.quote_timestamp_ms, NOW)
        self.assertEqual(state.liquidity_timestamp_ms, NOW)
        self.assertEqual(state.quote_source, "BINANCE_USD_M_WS_TICKER_BID_ASK")

    def test_binance_usdm_book_ticker_and_trade_are_normalized(self):
        states = {}
        state = realtime_market.apply_binance_message(states, {
            "e": "bookTicker", "s": "BTCUSDT", "E": NOW,
            "b": "99", "B": "2", "a": "101", "A": "3",
        }, NOW, market_type="USD_M_PERPETUAL")[0]
        self.assertEqual(state.last_price, 100)
        self.assertEqual(state.best_bid, 99)
        self.assertEqual(state.best_ask, 101)
        self.assertEqual((state.bid_quote_volume, state.ask_quote_volume), (198, 303))

        state = realtime_market.apply_binance_message(states, {
            "e": "aggTrade", "s": "BTCUSDT", "E": NOW + 1,
            "p": "100", "q": "2", "m": False,
        }, NOW + 1, market_type="USD_M_PERPETUAL")[0]
        self.assertEqual(state.trade_buy_notional, 200)
        self.assertEqual(state.market_timestamp_ms, NOW + 1)
        self.assertEqual(state.trade_timestamp_ms, NOW + 1)

    def test_cm_trade_payload_cannot_enter_usdm_flow(self):
        states = {}
        self.assertEqual(realtime_market.apply_binance_message(
            states,
            {
                "e": "aggTrade", "s": "BTCUSDT", "E": NOW, "st": 2,
                "p": "100", "q": "1", "m": False,
            },
            NOW,
            market_type="USD_M_PERPETUAL",
        ), [])
        self.assertFalse(states)

    def test_usdm_subscription_is_dynamic_and_does_not_include_spot(self):
        params = realtime_market.binance_subscription_params(
            [("SOLUSDT", "USD_M_PERPETUAL"), ("BTCUSDT", "SPOT")],
            "USD_M_PERPETUAL",
        )
        self.assertEqual(params, ["solusdt@bookTicker", "solusdt@aggTrade"])

    def test_usdm_quote_and_flow_channels_can_be_isolated(self):
        symbols = [("BTCUSDT", "USD_M_PERPETUAL")]
        quotes = realtime_market.binance_subscription_params(
            symbols, "USD_M_PERPETUAL", channels=("bookTicker",)
        )
        flow = realtime_market.binance_subscription_params(
            symbols, "USD_M_PERPETUAL", channels=("aggTrade",)
        )
        self.assertEqual(quotes, ["btcusdt@bookTicker"])
        self.assertEqual(flow, ["btcusdt@aggTrade"])


    def test_latest_state_queue_coalesces_hot_symbol_and_snapshots_state(self):
        pending, order, queued = {}, deque(), set()
        state = LiveState(
            "BTCUSDT", "USD_M_PERPETUAL", last_price=100,
            market_timestamp_ms=NOW, quote_timestamp_ms=NOW,
        )
        realtime_market._queue_latest_state(
            pending, order, queued, state, NOW
        )
        state.last_price = 101
        state.market_timestamp_ms = NOW + 1
        state.quote_timestamp_ms = NOW + 1
        realtime_market._queue_latest_state(
            pending, order, queued, state, NOW + 1
        )
        snapshot, observed_ms = realtime_market._pop_latest_state(
            pending, order, queued
        )
        self.assertEqual(snapshot.last_price, 101)
        self.assertEqual(snapshot.market_timestamp_ms, NOW + 1)
        self.assertEqual(observed_ms, NOW + 1)
        state.last_price = 999
        self.assertEqual(snapshot.last_price, 101)
        self.assertFalse(pending)
        self.assertFalse(order)
        self.assertFalse(queued)

    def test_dispatch_batch_drains_distinct_symbols_without_twenty_per_second_cap(self):
        pending, order, queued = {}, deque(), set()
        for i in range(12):
            state = LiveState(
                f"COIN{i}USDT", "USD_M_PERPETUAL",
                market_timestamp_ms=NOW, quote_timestamp_ms=NOW,
            )
            realtime_market._queue_latest_state(
                pending, order, queued, state, NOW,
            )
        batch = realtime_market._pop_latest_batch(pending, order, queued)
        self.assertEqual(len(batch), 12)
        self.assertFalse(pending)
        self.assertFalse(order)
        self.assertFalse(queued)
        # Bounded dispatch: a large universe cannot monopolize the receiver.
        for i in range(70):
            realtime_market._queue_latest_state(
                pending, order, queued,
                LiveState(f"COIN{i}USDT", market_timestamp_ms=NOW), NOW,
            )
        self.assertEqual(
            len(realtime_market._pop_latest_batch(pending, order, queued)), 64,
        )
        self.assertEqual(
            len(realtime_market._pop_latest_batch(pending, order, queued)), 6,
        )

    def test_latest_state_queue_is_fair_across_symbols(self):
        pending, order, queued = {}, deque(), set()
        btc = LiveState(
            "BTCUSDT", "USD_M_PERPETUAL", last_price=100,
            market_timestamp_ms=NOW,
        )
        eth = LiveState(
            "ETHUSDT", "USD_M_PERPETUAL", last_price=200,
            market_timestamp_ms=NOW,
        )
        realtime_market._queue_latest_state(pending, order, queued, btc, NOW)
        realtime_market._queue_latest_state(pending, order, queued, eth, NOW)
        btc.last_price = 101
        btc.market_timestamp_ms = NOW + 1
        realtime_market._queue_latest_state(
            pending, order, queued, btc, NOW + 1
        )
        first, _ = realtime_market._pop_latest_state(
            pending, order, queued
        )
        second, _ = realtime_market._pop_latest_state(
            pending, order, queued
        )
        self.assertEqual((first.symbol, first.last_price), ("BTCUSDT", 101))
        self.assertEqual((second.symbol, second.last_price), ("ETHUSDT", 200))


class FlowIsolationTests(unittest.TestCase):
    def test_cache_snapshots_flow_without_refreshing_quote_and_expires_support(self):
        cache = realtime_market.LatestFlowCache()
        cache.set_symbols(["BTCUSDT", ("BTCUSDT", "SPOT")])
        flow = LiveState("BTCUSDT", trade_timestamp_ms=NOW,
                         taker_imbalance=1, trade_buy_notional=200)
        cache.publish(flow)
        flow.trade_buy_notional = 999
        quote = LiveState("BTCUSDT", quote_timestamp_ms=NOW-100,
                          market_timestamp_ms=NOW-100, book_imbalance=0.5)
        cache.apply_to(quote, NOW)
        self.assertEqual(quote.trade_buy_notional, 200)
        self.assertEqual(quote.quote_timestamp_ms, NOW-100)
        self.assertEqual(quote.market_timestamp_ms, NOW-100)
        self.assertEqual(quote.trade_timestamp_ms, NOW)
        self.assertTrue(quote.thesis_supported("LONG"))
        cache.apply_to(quote, NOW+3000)
        self.assertIsNone(quote.thesis_supported("LONG"))
        spot = LiveState("BTCUSDT", "SPOT")
        cache.apply_to(spot, NOW)
        self.assertIsNone(spot.trade_timestamp_ms)
        cache.set_symbols([])
        cache.publish(flow)
        cache.apply_to(quote, NOW)
        self.assertIsNone(quote.trade_timestamp_ms)

    def test_process_flow_sink_coalesces_latest_snapshot(self):
        sink = realtime_market._ProcessFlowSink()
        first = LiveState(
            "BTCUSDT", "USD_M_PERPETUAL",
            trade_timestamp_ms=NOW,
            taker_imbalance=1,
            trade_buy_notional=100,
            trade_sell_notional=0,
        )
        sink.publish(first)
        second = LiveState(
            "BTCUSDT", "USD_M_PERPETUAL",
            trade_timestamp_ms=NOW + 1,
            taker_imbalance=-0.5,
            trade_buy_notional=100,
            trade_sell_notional=300,
        )
        sink.publish(second)
        snapshot = sink.snapshot()
        self.assertEqual(
            snapshot[("BTCUSDT", "USD_M_PERPETUAL")],
            (NOW + 1, -0.5, 100, 300),
        )

    def test_latest_flow_cache_accepts_process_batch(self):
        cache = realtime_market.LatestFlowCache()
        cache.set_symbols(["BTCUSDT"])
        cache.update_batch({
            ("BTCUSDT", "USD_M_PERPETUAL"): (NOW, 0.75, 700, 100),
            ("ETHUSDT", "USD_M_PERPETUAL"): (NOW, -0.5, 100, 300),
        })
        quote = LiveState("BTCUSDT", quote_timestamp_ms=NOW)
        cache.apply_to(quote, NOW)
        self.assertEqual(quote.trade_timestamp_ms, NOW)
        self.assertEqual(quote.taker_imbalance, 0.75)
        self.assertEqual(quote.trade_buy_notional, 700)
        self.assertEqual(quote.trade_sell_notional, 100)

    def test_flow_only_socket_never_calls_decision_callback(self):
        async def scenario():
            stop = asyncio.Event()
            cache = realtime_market.LatestFlowCache()
            cache.set_symbols(["BTCUSDT"])

            class Socket:
                count = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

                async def send(self, _):
                    pass

                async def recv(self):
                    self.count += 1
                    if self.count == 100:
                        stop.set()
                    return json.dumps({
                        "stream": "btcusdt@aggTrade",
                        "data": {"e": "aggTrade", "s": "BTCUSDT", "E": NOW,
                                 "st": 1, "p": "100", "q": "2", "m": False},
                    })

            async def forbidden(*_):
                self.fail("raw trade reached decision dispatcher")

            with patch.object(realtime_market.websockets, "connect", return_value=Socket()), \
                    patch.object(realtime_market.time, "time", return_value=NOW/1000):
                await realtime_market._binance_stream(
                    cache.symbols, forbidden, stop, market_type="USD_M_PERPETUAL",
                    channels=("aggTrade",), flow_sink=cache,
                )
            quote = LiveState("BTCUSDT")
            cache.apply_to(quote, NOW)
            self.assertEqual(quote.trade_buy_notional, 20000)
        asyncio.run(scenario())

    def test_receiver_metrics_distinguish_stale_source_from_dispatch(self):
        async def scenario():
            stop = asyncio.Event()
            metrics = realtime_market.new_quote_metrics()
            cache = realtime_market.LatestFlowCache()
            cache.set_symbols(["BTCUSDT"])
            seen = []

            class Socket:
                count = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    return False

                async def send(self, _):
                    pass

                async def recv(self):
                    self.count += 1
                    if self.count > 2:
                        await asyncio.sleep(0.12)
                        stop.set()
                        return "{}"
                    timestamp = NOW - 3000 if self.count == 1 else NOW
                    return json.dumps({
                        "e": "bookTicker", "s": "BTCUSDT", "E": timestamp,
                        "b": "99", "B": "2", "a": "101", "A": "3",
                    })

            async def on_state(state, _):
                seen.append(state.symbol)

            with patch.object(realtime_market.websockets, "connect", return_value=Socket()), \
                    patch.object(realtime_market.time, "time", return_value=NOW/1000), \
                    patch.object(realtime_market.asyncio, "wait_for",
                                 side_effect=AssertionError("per-frame wait_for task")):
                await realtime_market._binance_stream(
                    cache.symbols, on_state, stop,
                    market_type="USD_M_PERPETUAL",
                    channels=("bookTicker",), flow_cache=cache,
                    metrics=metrics,
                )
            self.assertEqual(seen, ["BTCUSDT"])
            self.assertEqual(metrics["received_total"], 2)
            self.assertEqual(metrics["accepted_total"], 1)
            self.assertEqual(metrics["stale_received_total"], 1)
            self.assertEqual(metrics["stale_dispatch_total"], 0)
            self.assertEqual(metrics["dispatched_total"], 1)
            self.assertEqual(metrics["received_by_symbol_ms"]["BTCUSDT"], NOW)
            self.assertEqual(metrics["last_receive_lag_ms"], 0)
            self.assertEqual(metrics["max_receive_lag_ms"], 3000)

        asyncio.run(scenario())

    def test_queued_quote_is_rejected_when_it_becomes_stale(self):
        async def scenario():
            stop = asyncio.Event()
            clock = [NOW/1000]
            cache = realtime_market.LatestFlowCache()
            cache.set_symbols(["BTCUSDT"])

            class Socket:
                count = 0

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_):
                    pass

                async def send(self, _):
                    pass

                async def recv(self):
                    self.count += 1
                    if self.count > 1:
                        clock[0] += 3
                        await asyncio.sleep(0.15)
                        stop.set()
                        return "{}"
                    return json.dumps({"e": "bookTicker", "s": "BTCUSDT", "E": NOW,
                                       "b": "99", "B": "2", "a": "101", "A": "3"})

            async def forbidden(*_):
                self.fail("stale queued quote reached decision dispatcher")

            with patch.object(realtime_market.websockets, "connect", return_value=Socket()), \
                    patch.object(realtime_market.time, "time", side_effect=lambda: clock[0]):
                await realtime_market._binance_stream(
                    cache.symbols, forbidden, stop, market_type="USD_M_PERPETUAL",
                    channels=("bookTicker",), flow_cache=cache,
                )
        asyncio.run(scenario())

    def test_replace_queue_item_keeps_only_latest_value(self):
        class FakeQueue:
            def __init__(self):
                self.items = []
            def get_nowait(self):
                if not self.items:
                    raise realtime_market.queue.Empty
                return self.items.pop(0)
            def put_nowait(self, value):
                self.items.append(value)

        q = FakeQueue()
        q.items.extend([1, 2])
        realtime_market._replace_queue_item(q, 3)
        self.assertEqual(q.items, [3])


if __name__ == "__main__":
    unittest.main()
