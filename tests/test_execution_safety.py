import asyncio
import os
import tempfile
import time
import unittest

import auto_trader as auto_trader_module
from auto_trader import AutoTrader
import main
from backtest_engine import BacktestEngine
from liquidation_tracker import LiquidationTracker
from market_graph import MarketGraphScorer
from okx_client import OKXClient
from risk_manager import RiskManager
from signal_parser import TradeSignal
from trade_logger import TradeLogger


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def notify_reject(self, signal, msg):
        self.messages.append(("reject", signal.okx_symbol, msg))

    def notify_trade(self, signal, quantity, leverage, order_id):
        self.messages.append(("trade", signal.okx_symbol, quantity, leverage, order_id))


class ExecutionSafetyTests(unittest.TestCase):
    def test_market_graph_allows_aligned_liquid_long_cluster(self):
        market = {
            tf: {
                "price": 100,
                "ema20": 105,
                "ema50": 100,
                "macd": {"is_bullish": True},
                "supertrend": {"trend": "up"},
                "adx": {"adx": 32},
                "bb_lower": 90,
                "bb_upper": 120,
            }
            for tf in ("1H", "4H", "1D")
        }
        graph = MarketGraphScorer().score(
            "BTC-USDT-SWAP",
            "long",
            market,
            {"signal": "neutral", "funding_rate": 0, "oi_change_pct": 0},
            {"trend": "bullish"},
            "清算压力: bullish",
            {"spread": 0.01, "depth_1pct": 120000, "imbalance": 0.7, "buy_sell_ratio": 0.68, "cvd": 1000},
            {"trend_strength": "强多头", "range_bound": False},
        )

        self.assertTrue(graph["trade_allowed"])
        self.assertEqual(graph["cluster"], "BULL")
        self.assertGreaterEqual(graph["edge_score"], 60)
        self.assertLessEqual(graph["conflict_score"], 35)

    def test_market_graph_blocks_conflict_and_bad_liquidity(self):
        market = {
            tf: {
                "price": 100,
                "ema20": 105,
                "ema50": 100,
                "macd": {"is_bullish": True},
                "supertrend": {"trend": "up"},
                "adx": {"adx": 32},
                "bb_lower": 90,
                "bb_upper": 120,
            }
            for tf in ("1H", "4H", "1D")
        }
        graph = MarketGraphScorer().score(
            "BTC-USDT-SWAP",
            "short",
            market,
            {"signal": "danger", "funding_rate": 0.004, "oi_change_pct": 18},
            {"trend": "bullish"},
            "清算压力: bullish",
            {"spread": 0.25, "depth_1pct": 1000, "imbalance": 0.72, "buy_sell_ratio": 0.7, "cvd": 1500,
             "liquidity_gap": True},
            {"trend_strength": "强多头", "range_bound": True},
        )

        self.assertFalse(graph["trade_allowed"])
        self.assertGreater(graph["conflict_score"], 35)
        self.assertIn("图谱方向与交易方向不一致", graph["blockers"])

    def test_risk_manager_uses_contract_metadata(self):
        risk = RiskManager()
        qty, notional = risk.calculate_position_size(
            balance=1000,
            entry_price=100,
            stop_loss=90,
            contract_value=0.01,
            min_size=1,
            lot_size=1,
        )

        self.assertEqual(qty, 200)
        self.assertEqual(notional, 200.0)

    def test_okx_limit_order_requires_price_and_cancel_uses_cancel_endpoint(self):
        client = OKXClient.__new__(OKXClient)
        calls = []

        def fake_post(path, data):
            calls.append((path, data))
            return {"code": "0", "data": [{"ordId": "abc"}]}

        client._post = fake_post

        ok, msg, oid = client.place_order("BTC-USDT-SWAP", "long", 1, ord_type="limit")
        self.assertFalse(ok)
        self.assertEqual(oid, "")
        self.assertIn("limit_price", msg)

        ok, msg, oid = client.place_order(
            "BTC-USDT-SWAP",
            "long",
            1,
            stop_loss=90,
            take_profit=120,
            ord_type="market",
        )
        self.assertTrue(ok)
        self.assertEqual(oid, "abc")
        self.assertEqual(calls[-1][0], "/api/v5/trade/order")
        self.assertEqual(calls[-1][1]["ordType"], "market")
        self.assertIn("attachAlgoOrds", calls[-1][1])

        client._pos_mode = "long_short_mode"
        ok, msg, oid = client.place_order("BTC-USDT-SWAP", "long", 1, ord_type="market")
        self.assertTrue(ok)
        self.assertEqual(calls[-1][1]["posSide"], "long")

        client._post = lambda path, data: {
            "code": "1",
            "msg": "All operations failed",
            "data": [{"sCode": "51000", "sMsg": "Parameter posSide error"}],
        }
        ok, msg, oid = client.place_order("BTC-USDT-SWAP", "long", 1, ord_type="market")
        self.assertFalse(ok)
        self.assertIn("Parameter posSide error", msg)

        client._post = fake_post
        ok, msg = client.cancel_order("BTC-USDT-SWAP", "abc")
        self.assertTrue(ok)
        self.assertEqual(calls[-1], ("/api/v5/trade/cancel-order", {
            "instId": "BTC-USDT-SWAP",
            "ordId": "abc",
        }))

    def test_webhook_trade_path_places_market_order(self):
        class FakeOKX:
            def __init__(self):
                self.place_kwargs = None

            def get_balance(self):
                return {"equity": 1000}

            def get_positions(self):
                return []

            def has_position(self, symbol):
                return False

            def get_market_price(self, symbol):
                return 100

            def get_instrument_info(self, symbol):
                return {"ctVal": 0.01, "minSz": 1, "lotSz": 1}

            def set_leverage(self, symbol, leverage):
                return True, ""

            def place_order(self, *args, **kwargs):
                self.place_kwargs = kwargs
                return True, "", "order-1"

        class FakeLogger:
            def __init__(self):
                self.statuses = []

            def update_signal_status(self, signal_id, status):
                self.statuses.append((signal_id, status))

            def log_trade(self, signal_id, signal, quantity, leverage, order_id):
                return 42

        fake_okx = FakeOKX()
        old = (main.okx, main.telegram, main.qq_bot, main.logger)
        main.okx = fake_okx
        main.telegram = FakeNotifier()
        main.qq_bot = FakeNotifier()
        main.logger = FakeLogger()
        try:
            signal = TradeSignal(
                symbol="BTCUSDT",
                okx_symbol="BTC-USDT-SWAP",
                direction="long",
                price=101,
                stop_loss=90,
                take_profit=120,
                strategy="test",
                interval="1h",
                comment="",
                raw_data={},
            )
            result = asyncio.run(main.process_trade_signal(signal, 1))
        finally:
            main.okx, main.telegram, main.qq_bot, main.logger = old

        self.assertEqual(result["status"], "executed")
        self.assertEqual(fake_okx.place_kwargs["ord_type"], "market")

    def test_factor_expression_vectorizes_and_blocks_attribute_escape(self):
        engine = BacktestEngine.__new__(BacktestEngine)
        closes = [1.0, 2.0, 4.0, 8.0]
        opens = highs = lows = volumes = returns = closes

        result = engine._eval_factor(
            "(close - roll(close, 2)) / roll(close, 2)",
            closes,
            opens,
            highs,
            lows,
            volumes,
            returns,
        )

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[2], 3.0)

        unsafe = engine._eval_factor(
            "(1).__class__",
            closes,
            opens,
            highs,
            lows,
            volumes,
            returns,
        )
        self.assertIsNone(unsafe)

    def test_pending_limit_order_timeout_cancels_even_when_position_exists(self):
        class FakeOKX:
            def __init__(self):
                self.cancelled = []

            def get_positions(self):
                return [{"instId": "BTC-USDT-SWAP"}]

            def cancel_order(self, symbol, order_id):
                self.cancelled.append((symbol, order_id))
                return True, "ok"

        trader = AutoTrader.__new__(AutoTrader)
        trader.okx = FakeOKX()
        trader.pending_orders = {
            "order-1": {
                "symbol": "BTC-USDT-SWAP",
                "entry": 100,
                "qty": 1,
                "time": time.time() - 999,
                "timeout": 1,
                "notified": False,
            }
        }
        messages = []

        trader._check_pending_orders(messages.append)

        self.assertEqual(trader.okx.cancelled, [("BTC-USDT-SWAP", "order-1")])
        self.assertEqual(trader.pending_orders, {})
        self.assertIn("已撤剩余委托", messages[0])

    def test_session_adjust_never_relaxes_hard_rules(self):
        class FakeLogger:
            def get_session_stats(self, days=30):
                return {
                    "asia": {"total": 10, "win_rate": 0.9},
                    "eu": {"total": 10, "win_rate": 0.9},
                    "us": {"total": 10, "win_rate": 0.9},
                    "overlap": {"total": 10, "win_rate": 0.9},
                }

        trader = AutoTrader.__new__(AutoTrader)
        trader.logger = FakeLogger()
        trader.safety = AutoTrader.HARD_RULES["safety"]
        trader.scalp = {
            "leverage": 20,
            "risk_pct": 2.0,
            "min_confidence": 50,
            "min_rr": 1.2,
            "tp_sl_ratio": 1.5,
            "max_positions": 9,
        }

        adjusted = trader._session_adjust(trader.scalp)

        self.assertGreaterEqual(adjusted["leverage"], 10)
        self.assertLessEqual(adjusted["leverage"], 18)
        self.assertLessEqual(adjusted["risk_pct"], 0.25)
        self.assertGreaterEqual(adjusted["min_confidence"], 78)
        self.assertGreaterEqual(adjusted["min_rr"], 3.0)
        self.assertEqual(adjusted["max_positions"], 1)

    def test_trade_geometry_rejects_small_profit_large_loss(self):
        good = AutoTrader._trade_geometry(100, 98, 106, "long")
        self.assertTrue(good["ok"])
        self.assertEqual(good["rr"], 3.0)
        self.assertLess(good["net_rr"], good["rr"])

        bad_tp = AutoTrader._trade_geometry(100, 98, 99, "long")
        self.assertFalse(bad_tp["ok"])
        self.assertIn("止盈方向错误", bad_tp["reason"])

        small_profit = AutoTrader._trade_geometry(100, 95, 102, "long")
        self.assertTrue(small_profit["ok"])
        self.assertLess(small_profit["rr"], 1.0)

    def test_adaptive_leverage_stays_within_10_to_25(self):
        trader = AutoTrader.__new__(AutoTrader)
        trader.safety = AutoTrader.HARD_RULES["safety"]

        high_quality = trader._adaptive_leverage(
            requested=25,
            mode="scalp",
            confidence=92,
            rr=3.8,
            tf_edge=0.2,
            sl_pct=0.8,
            risk_level="low",
            chain_signal="neutral",
        )
        high_risk = trader._adaptive_leverage(
            requested=25,
            mode="scalp",
            confidence=92,
            rr=3.8,
            tf_edge=0.2,
            sl_pct=3.0,
            risk_level="high",
            chain_signal="danger",
        )

        self.assertGreaterEqual(high_quality, 10)
        self.assertLessEqual(high_quality, 25)
        self.assertGreater(high_quality, high_risk)
        self.assertEqual(high_risk, 10)

    def test_daily_optimize_reports_but_does_not_mutate_hard_params(self):
        class FakeLogger:
            def get_today_trades(self):
                row_win = [None] * 14
                row_win[11] = "closed"
                row_win[13] = 3.0
                row_loss = [None] * 14
                row_loss[11] = "closed"
                row_loss[13] = -1.0
                return [row_win, row_loss]

        class FakePaulWei:
            def get_summary(self):
                return "ok"

        trader = AutoTrader.__new__(AutoTrader)
        trader.logger = FakeLogger()
        trader.paul_wei = FakePaulWei()
        trader.safety = AutoTrader.HARD_RULES["safety"]
        trader.scalp = dict(AutoTrader.HARD_RULES["scalp"])
        trader.swing = dict(AutoTrader.HARD_RULES["swing"])
        trader._call_ds = lambda *args, **kwargs: '{"scalp_leverage":99,"advice":"保持纪律"}'

        before = (dict(trader.scalp), dict(trader.swing))
        messages = []
        trader.daily_optimize(messages.append)

        self.assertEqual((trader.scalp, trader.swing), before)
        self.assertIn("硬风控未自动改动", messages[0])

    def test_conflicting_market_score_returns_wait_instead_of_short(self):
        trader = AutoTrader.__new__(AutoTrader)
        market = {
            "1H": {
                "price": 100,
                "ema20": 101,
                "ema50": 100,
                "macd": {"is_bullish": True},
                "bb_lower": 99,
                "bb_upper": 110,
            },
            "4H": {
                "price": 100,
                "ema20": 98,
                "ema50": 100,
                "macd": {"is_bullish": False},
                "bb_lower": 99,
                "bb_upper": 110,
            },
            "1D": {
                "price": 100,
                "ema20": 98,
                "ema50": 100,
                "macd": {"is_bullish": False},
                "bb_lower": 99,
                "bb_upper": 110,
            },
        }

        decision = trader._calc_direction_score(
            market,
            {"funding_rate": 0, "oi_change_pct": 0},
            {"trend": "bullish"},
            "清算压力: bullish",
            {"trend_strength": "震荡"},
        )

        self.assertEqual(decision["direction"], "WAIT")
        self.assertIn("观望", decision["conclusion"])
        self.assertGreater(decision["long_score"], decision["short_score"])
        self.assertIn("趋势强度=震荡", decision["blockers"])

    def test_market_report_does_not_emit_trade_advice_when_wait_or_depth_low(self):
        class FakeLiq:
            _running = False

        trader = AutoTrader.__new__(AutoTrader)
        trader.safety = AutoTrader.HARD_RULES["safety"]
        trader.liq_tracker = FakeLiq()
        trader._market = lambda symbol, timeframes: {
            "1H": {
                "price": 100,
                "ema20": 101,
                "ema50": 100,
                "macd": {"is_bullish": True},
                "bb_lower": 99,
                "bb_upper": 110,
                "vwap": 102,
                "supertrend": {"trend": "up"},
                "adx": {"adx": 17},
                "support": 98,
                "resistance": 106,
                "atr": 2,
            },
            "4H": {
                "price": 100,
                "ema20": 98,
                "ema50": 100,
                "macd": {"is_bullish": False},
                "bb_lower": 99,
                "bb_upper": 110,
                "vwap": 103,
                "supertrend": {"trend": "up"},
                "adx": {"adx": 43},
            },
            "1D": {
                "price": 100,
                "ema20": 98,
                "ema50": 100,
                "macd": {"is_bullish": False},
                "bb_lower": 99,
                "bb_upper": 110,
                "vwap": 103,
                "supertrend": {"trend": "up"},
                "adx": {"adx": 2},
            },
        }
        trader._onchain = lambda symbol: {
            "funding_rate": 0,
            "oi_change_pct": 0,
            "oi_val": 1000,
            "crowd_signal": "中性",
            "explanation": "",
        }
        trader._market_structure = lambda symbol: {"trend_strength": "震荡", "range_bound": True}
        trader._smc_structure = lambda symbol: {"trend": "bullish", "summary": "趋势:bullish", "order_blocks": []}
        trader._microstructure = lambda symbol: {
            "spread": 0,
            "buy_sell_ratio": 0.5,
            "depth_1pct": 2464,
            "liquidity_gap": False,
        }

        report = trader.market_report("BTC-USDT-SWAP")

        self.assertIn("观望", report)
        self.assertIn("深度不足", report)
        self.assertIn("风控阻断", report)
        self.assertNotIn("*建议:*", report)

    def test_liquidation_summary_labels_future_relevant_sides(self):
        tracker = LiquidationTracker()
        symbol = "BTC-USDT-SWAP"
        tracker.long_liq[symbol][72400] = 32740
        tracker.long_liq[symbol][74000] = 99999
        tracker.short_liq[symbol][73600] = 31544
        tracker.short_liq[symbol][73900] = 1405

        summary = tracker.summary(symbol, 73815)

        self.assertIn("下方多头清算区: $72,400.0(32740)", summary)
        self.assertIn("上方空头清算区: $73,900.0(1405)", summary)
        self.assertNotIn("最大空头清算区", summary)
        self.assertNotIn("$74,000.0(99999)", summary)

    def test_core_universe_filters_non_core_dynamic_candidates(self):
        class FakeResponse:
            def json(self):
                return {
                    "code": "0",
                    "data": [
                        {"instId": "BTC-USDT-SWAP", "vol24h": "99999999", "askPx": "100000",
                         "bidPx": "99990", "askSz": "1", "bidSz": "1", "open24h": "99000"},
                        {"instId": "XAU-USDT-SWAP", "vol24h": "99999999", "askPx": "3000",
                         "bidPx": "2999", "askSz": "10", "bidSz": "10", "open24h": "2900"},
                        {"instId": "TRUMP-USDT-SWAP", "vol24h": "99999999", "askPx": "10",
                         "bidPx": "9.99", "askSz": "2000", "bidSz": "2000", "open24h": "9.8"},
                    ],
                }

        old_get = auto_trader_module.requests.get
        old_universe = auto_trader_module.TRADING_UNIVERSE
        auto_trader_module.requests.get = lambda *args, **kwargs: FakeResponse()
        auto_trader_module.TRADING_UNIVERSE = "core"
        trader = AutoTrader.__new__(AutoTrader)
        trader.proxies = None
        try:
            candidates = trader._candidate_coins()
        finally:
            auto_trader_module.requests.get = old_get
            auto_trader_module.TRADING_UNIVERSE = old_universe

        self.assertEqual([c["symbol"] for c in candidates], ["BTC-USDT-SWAP"])

    def test_trade_logger_records_ai_decisions_and_auto_trades(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        logger = TradeLogger(path)
        try:
            logger.log_ai_decision("BTC-USDT-SWAP", "scalp", {
                "action": "WAIT",
                "reason": "confidence 70<78",
                "confidence": 70,
            })
            stats = logger.get_ai_decision_stats(hours=24)
            recent = logger.get_recent_ai_decisions(1)

            self.assertEqual(stats["total"], 1)
            self.assertEqual(stats["by_mode"]["scalp"]["wait"], 1)
            self.assertIn("confidence", recent[0][4])

            trade_id = logger.log_auto_trade({
                "symbol": "BTC-USDT-SWAP",
                "direction": "long",
                "entry": 100,
                "leverage": 3,
                "stop_loss": 95,
                "take_profit": 115,
            }, 2, "order-1")
            self.assertGreater(trade_id, 0)
            self.assertEqual(logger.get_today_trades()[0][4], "long")

            logger.log_ai_order({
                "symbol": "BTC-USDT-SWAP",
                "mode": "scalp",
                "direction": "long",
                "entry": 100,
                "leverage": 10,
                "stop_loss": 95,
                "take_profit": 115,
            }, 2, "order-2")
            active = logger.get_active_ai_orders()
            self.assertEqual(active[0][0], "order-2")
            logger.update_ai_order("order-2", "canceled")
            self.assertEqual(logger.get_active_ai_orders(), [])

            logger.close_trade(trade_id, 90, -2.5)
            losses = logger.get_symbol_loss_stats("BTC-USDT-SWAP")
            self.assertEqual(losses["losses"], 1)
        finally:
            logger.close()
            os.remove(path)

    def test_execute_logs_successful_ai_auto_trade(self):
        class FakeOKX:
            def __init__(self):
                self.placed = None

            def get_balance(self):
                return {"equity": 1000}

            def get_positions(self):
                return []

            def set_leverage(self, symbol, leverage):
                return True, ""

            def get_instrument_info(self, symbol):
                return {"ctVal": 1, "minSz": 1, "lotSz": 1}

            def place_order(self, *args, **kwargs):
                self.placed = (args, kwargs)
                return True, "ok", "order-1"

        class FakeLiq:
            def nearest_cluster(self, symbol, entry):
                return {"below": None, "above": None}

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        logger = TradeLogger(path)
        trader = AutoTrader.__new__(AutoTrader)
        trader.okx = FakeOKX()
        trader.logger = logger
        trader.risk = RiskManager()
        trader.safety = AutoTrader.HARD_RULES["safety"]
        trader.blackout_until = None
        trader.consecutive_losses = 0
        trader._last_health = None
        trader.pending_orders = {}
        trader.liq_tracker = FakeLiq()
        trader._risk_gate = lambda d, send: (True, "")
        trader._microstructure = lambda symbol: {
            "atr_ratio": 1,
            "atr_val": 0,
            "depth_1pct": 100000,
            "spread": 0,
            "mid_price": 100,
            "liquidity_gap": False,
        }
        trader._correlation_exposure = lambda positions: {}
        messages = []
        decision = {
            "symbol": "BTC-USDT-SWAP",
            "direction": "long",
            "mode": "scalp",
            "entry": 100,
            "stop_loss": 95,
            "take_profit": 115,
            "leverage": 3,
            "confidence": 90,
            "risk_reward": 3,
            "liq_buffer": 10,
            "risk_pct": 0.25,
            "regime": {},
            "reason": "unit test",
        }
        try:
            ok = trader.execute(decision, messages.append)
            trades = logger.get_today_trades()
            orders = logger.get_active_ai_orders()
        finally:
            logger.close()
            os.remove(path)

        self.assertTrue(ok)
        self.assertEqual(trader.okx.placed[0][0], "BTC-USDT-SWAP")
        self.assertEqual(len(trades), 0)
        self.assertEqual(orders[0][0], "order-1")
        self.assertIn("order-1", trader.pending_orders)
        self.assertEqual(trader.pending_orders["order-1"]["decision"]["symbol"], "BTC-USDT-SWAP")

    def test_pending_filled_order_logs_auto_trade(self):
        class FakeOKX:
            def get_order(self, symbol, order_id):
                return {"state": "filled", "accFillSz": "3", "avgPx": "101"}

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        logger = TradeLogger(path)
        trader = AutoTrader.__new__(AutoTrader)
        trader.okx = FakeOKX()
        trader.logger = logger
        trader.pending_orders = {
            "order-1": {
                "symbol": "BTC-USDT-SWAP",
                "entry": 100,
                "qty": 3,
                "time": time.time(),
                "timeout": 120,
                "notified": False,
                "trade_id": None,
                "decision": {
                    "symbol": "BTC-USDT-SWAP",
                    "direction": "long",
                    "entry": 100,
                    "leverage": 3,
                    "stop_loss": 95,
                    "take_profit": 115,
                },
            }
        }
        messages = []
        try:
            trader._check_pending_orders(messages.append)
            trades = logger.get_today_trades()
        finally:
            logger.close()
            os.remove(path)

        self.assertEqual(trader.pending_orders, {})
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0][3], "BTC-USDT-SWAP")
        self.assertEqual(trades[0][5], 101)
        self.assertIn("已成交", messages[0])


if __name__ == "__main__":
    unittest.main()
