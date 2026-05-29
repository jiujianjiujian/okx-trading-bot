import asyncio
import time
import unittest

from auto_trader import AutoTrader
import main
from backtest_engine import BacktestEngine
from okx_client import OKXClient
from risk_manager import RiskManager
from signal_parser import TradeSignal


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def notify_reject(self, signal, msg):
        self.messages.append(("reject", signal.okx_symbol, msg))

    def notify_trade(self, signal, quantity, leverage, order_id):
        self.messages.append(("trade", signal.okx_symbol, quantity, leverage, order_id))


class ExecutionSafetyTests(unittest.TestCase):
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

        self.assertLessEqual(adjusted["leverage"], 5)
        self.assertLessEqual(adjusted["risk_pct"], 0.25)
        self.assertGreaterEqual(adjusted["min_confidence"], 78)
        self.assertGreaterEqual(adjusted["min_rr"], 2.6)
        self.assertEqual(adjusted["max_positions"], 1)

    def test_daily_optimize_reports_but_does_not_mutate_hard_params(self):
        class FakeLogger:
            def get_today_trades(self):
                row_win = [None] * 14
                row_win[9] = "closed"
                row_win[13] = 3.0
                row_loss = [None] * 14
                row_loss[9] = "closed"
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


if __name__ == "__main__":
    unittest.main()
