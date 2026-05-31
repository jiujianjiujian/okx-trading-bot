"""通知适配器测试"""

import pytest
from src.core.models import ScalpDecision, PnLRecord
from src.interfaces.notifier_adapters import CompositeNotifier, ConsoleNotifier


class SpyNotifier:
    """测试 spy — 记录所有调用"""
    def __init__(self):
        self.calls = []

    def send(self, text, parse_mode="Markdown"):
        self.calls.append(("send", text[:50]))

    def notify_signal(self, signal):
        self.calls.append(("notify_signal", signal.symbol))

    def notify_scalp_decision(self, decision):
        self.calls.append(("notify_scalp_decision", decision.symbol))

    def notify_trade_open(self, decision, signal_id):
        self.calls.append(("notify_trade_open", decision.symbol, signal_id))

    def notify_trade_close(self, rec):
        self.calls.append(("notify_trade_close", rec.symbol))

    def notify_error(self, title, detail):
        self.calls.append(("notify_error", title))

    def notify_daily_stats(self, stats):
        self.calls.append(("notify_daily_stats",))


class TestCompositeNotifier:
    def test_broadcasts_to_all(self):
        spy1 = SpyNotifier()
        spy2 = SpyNotifier()
        cn = CompositeNotifier([spy1, spy2])

        cn.send("测试消息")
        assert len(spy1.calls) == 1
        assert len(spy2.calls) == 1
        assert spy1.calls[0] == ("send", "测试消息")

    def test_one_failure_doesnt_block_others(self):
        class FailingNotifier:
            def send(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_signal(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_scalp_decision(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_trade_open(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_trade_close(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_error(self, *args, **kwargs):
                raise RuntimeError("fail")
            def notify_daily_stats(self, *args, **kwargs):
                raise RuntimeError("fail")

        spy = SpyNotifier()
        cn = CompositeNotifier([FailingNotifier(), spy])

        # 不应抛异常
        cn.send("消息")
        cn.notify_error("标题", "详情")
        assert len(spy.calls) == 2

    def test_console_notifier_no_crash(self):
        cn = ConsoleNotifier()
        # 所有方法调用不应抛异常
        cn.send("test")
        cn.notify_error("title", "detail")
        dec = ScalpDecision(
            symbol="BTCUSDT", direction="long", action="LONG",
            entry=87000.0, stop_loss=86500.0, take_profit=88000.0,
            confidence=75, reason="test", sl_pct=0.3, tp_pct=0.5,
            risk_reward=1.67, net_risk_reward=2.1,
            position_size=100, risk_usdt=3.0, expected_profit_usdt=8.0,
        )
        cn.notify_scalp_decision(dec)
