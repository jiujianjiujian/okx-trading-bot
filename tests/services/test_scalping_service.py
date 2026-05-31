"""剥头皮参数计算测试"""

import pytest
from src.core.models import ScalpDecision, IndicatorBundle
from src.services.scalping_service import ScalpingService


@pytest.fixture
def scalper():
    return ScalpingService()


@pytest.fixture
def long_decision():
    return ScalpDecision(
        symbol="BTCUSDT",
        direction="long",
        action="LONG",
        entry=87000.0,
        confidence=75,
        reason="趋势看涨, RSI 45 超卖反弹",
        scalping_strategy="momentum",
    )


@pytest.fixture
def normal_bundle():
    return IndicatorBundle(
        ema20=86950.0,
        ema50=86500.0,
        rsi=45.0,
        macd=50.0,
        macd_bullish=True,
        atr=200.0,  # ATR ~0.23%
        adx=28.0,
    )


class TestScalpingParameters:
    def test_calculate_basic(self, scalper, long_decision, normal_bundle):
        """基本参数计算: ATR 自适应 SL/TP"""
        result = scalper.calculate_parameters(long_decision, normal_bundle, 1000.0)

        assert result.action == "LONG"
        assert result.sl_pct > 0
        assert result.tp_pct > result.sl_pct  # TP > SL
        assert result.net_risk_reward >= 2.0  # 满足最低盈亏比
        assert result.stop_loss < result.entry  # SL 在入场之下
        assert result.take_profit > result.entry  # TP 在入场之上
        assert result.position_size > 0

    def test_fee_estimate(self, scalper):
        fee = scalper.fee_estimate(1000.0, is_taker=True)
        assert fee == pytest.approx(0.55)  # 1000 * 0.055% = 0.55

    def test_min_tp_for_breakeven(self, scalper):
        min_tp = scalper.min_tp_for_breakeven(0.3)
        # 盈亏平衡: tp% = sl% + 2×fee% = 0.3 + 0.11 = 0.41
        assert min_tp == pytest.approx(0.41, 0.01)

    def test_low_confidence_decision(self, scalper):
        """低置信度决定不应通过 (由 risk_service 处理)"""
        dec = ScalpDecision(
            symbol="BTCUSDT", direction="long", action="LONG",
            entry=87000, confidence=50,
        )
        result = scalper.calculate_parameters(dec, None, 1000.0)
        # scalping_service 不拒绝低置信度, 由 risk_service 处理
        assert result.position_size > 0

    def test_position_size_sanity(self, scalper, long_decision, normal_bundle):
        """仓位大小合理性检查"""
        result = scalper.calculate_parameters(long_decision, normal_bundle, 1000.0)
        # 仓位不应超过账户 10%
        assert result.position_size <= 100.0  # 1000 * 10%
        # 风险不应超过单笔最大亏损
        assert result.risk_usdt <= 5.01  # 容忍浮点

    def test_short_calculation(self, scalper, normal_bundle):
        """做空参数计算"""
        dec = ScalpDecision(
            symbol="BTCUSDT", direction="short", action="SHORT",
            entry=87000.0, confidence=75,
            reason="趋势看跌",
        )
        result = scalper.calculate_parameters(dec, normal_bundle, 1000.0)
        assert result.stop_loss > result.entry  # 做空 SL 在入场之上
        assert result.take_profit < result.entry  # 做空 TP 在入场之下

    def test_max_positions_blocked(self, scalper, long_decision, normal_bundle):
        """持仓上限拒绝"""
        result = scalper.calculate_parameters(
            long_decision, normal_bundle, 1000.0, current_positions=3,
        )
        assert result.action == "WAIT"
        assert "上限" in result.invalid_condition
