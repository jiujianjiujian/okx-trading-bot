"""风控引擎测试"""

import time
import pytest
from src.core.models import ScalpDecision
from src.services.risk_service import RiskService


@pytest.fixture
def risk():
    return RiskService()


@pytest.fixture
def good_decision():
    return ScalpDecision(
        symbol="BTCUSDT", direction="long", action="LONG",
        entry=87000.0, confidence=75, risk_usdt=3.0,
        expected_profit_usdt=8.0, reason="趋势看涨",
    )


class TestPreTradeCheck:
    def test_good_decision_passes(self, risk, good_decision):
        ok, reason = risk.pre_trade_check(good_decision)
        assert ok
        assert reason == "OK"

    def test_low_confidence_rejected(self, risk):
        dec = ScalpDecision(symbol="BTC", direction="long", action="LONG",
                            entry=87000, confidence=55, risk_usdt=3.0)
        ok, reason = risk.pre_trade_check(dec)
        assert not ok
        assert "置信度" in reason

    def test_wait_action_blocked(self, risk):
        dec = ScalpDecision(symbol="BTC", action="WAIT", risk_usdt=0)
        ok, reason = risk.pre_trade_check(dec)
        assert not ok

    def test_high_risk_rejected(self, risk):
        dec = ScalpDecision(symbol="BTC", direction="long", action="LONG",
                            entry=87000, confidence=70, risk_usdt=10.0)
        ok, reason = risk.pre_trade_check(dec)
        assert not ok
        assert "单笔风险" in reason


class TestTradeTracking:
    def test_trade_open_increments_count(self, risk, good_decision):
        assert risk.daily_trade_count == 0
        risk.on_trade_open(good_decision)
        assert risk.daily_trade_count == 1

    def test_win_resets_consecutive(self, risk, good_decision):
        risk.consecutive_losses = 2
        risk.on_trade_close("BTCUSDT", 5.0)  # 盈利
        assert risk.consecutive_losses == 0

    def test_loss_increments_consecutive(self, risk):
        risk.on_trade_close("BTCUSDT", -3.0)  # 亏损
        assert risk.consecutive_losses == 1

    def test_three_consecutive_losses_blackout(self, risk):
        for _ in range(3):
            risk.on_trade_close("BTCUSDT", -2.0)
        assert risk.blackout_until > time.time()
        # 此时应被拒绝
        dec = ScalpDecision(symbol="BTC", direction="long", action="LONG",
                            entry=87000, confidence=75, risk_usdt=3.0)
        ok, reason = risk.pre_trade_check(dec)
        assert not ok

    def test_daily_loss_limit(self, risk, good_decision):
        # 模拟日亏损达标
        risk.daily_pnl_usdt = -32.0
        ok, reason = risk.pre_trade_check(good_decision)
        assert not ok
        assert "日亏损" in reason

    def test_daily_trade_limit(self, risk, good_decision):
        risk.daily_trade_count = 20  # 达到上限
        ok, reason = risk.pre_trade_check(good_decision)
        assert not ok
        assert "交易上限" in reason


class TestBlackswan:
    def test_blackswan_sets_blackout(self, risk):
        risk.on_blackswan()
        assert risk.blackout_until > time.time()
