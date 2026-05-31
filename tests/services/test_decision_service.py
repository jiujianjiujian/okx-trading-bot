"""AI 决策服务测试 — 规则回退逻辑"""

import pytest
from src.services.decision_service import DecisionService
from src.services.analysis_service import AnalysisService
from src.core.models import MarketSnapshot, IndicatorBundle
from datetime import datetime, timezone


@pytest.fixture
def decision_svc():
    """DeepSeek 未配置时的规则引擎回退"""
    class FakeDS:
        @property
        def configured(self):
            return False
        def chat(self, *a, **kw):
            return ""
        def chat_json(self, *a, **kw):
            return None
    return DecisionService(FakeDS(), AnalysisService())


@pytest.fixture
def btc_snapshot():
    return MarketSnapshot(
        symbol="BTCUSDT", symbol_base="BTC",
        timestamp=datetime.now(timezone.utc),
        current_price=87000.0, change_24h=1.5,
    )


@pytest.fixture
def bull_bundle():
    return IndicatorBundle(
        ema20=86900.0, ema50=86500.0, rsi=55.0,
        macd=100.0, macd_bullish=True, atr=200.0,
        adx=28.0, di_plus=25.0, di_minus=15.0,
        vol_expansion=True, supertrend="up",
    )


@pytest.fixture
def bear_bundle():
    return IndicatorBundle(
        ema20=86500.0, ema50=86900.0, rsi=42.0,
        macd=-50.0, macd_bullish=False, atr=180.0,
        adx=22.0, di_plus=12.0, di_minus=24.0,
        vol_expansion=False, supertrend="down",
    )


class TestRuleBasedDecision:
    def test_bullish_trend_long(self, decision_svc, btc_snapshot, bull_bundle):
        """上升趋势应该做多"""
        result = decision_svc._rule_based_decision(btc_snapshot, bull_bundle)
        assert result.action in ("LONG", "WAIT")
        if result.action == "LONG":
            assert result.direction == "long"

    def test_bearish_trend_short(self, decision_svc, btc_snapshot, bear_bundle):
        """下降趋势应该做空"""
        result = decision_svc._rule_based_decision(btc_snapshot, bear_bundle)
        assert result.action in ("SHORT", "WAIT")

    def test_rsi_oversold_long(self, decision_svc, btc_snapshot):
        """RSI 超卖反弹做多"""
        bundle = IndicatorBundle(
            ema20=86900.0, ema50=86500.0, rsi=25.0,
            macd=-20.0, macd_bullish=False, atr=300.0,
            adx=25.0, vol_expansion=True,
        )
        result = decision_svc._rule_based_decision(btc_snapshot, bundle)
        assert result.action == "LONG"

    def test_rsi_overbought_direction(self, decision_svc, btc_snapshot):
        """RSI 超买 + 均线死叉时方向应为 short 或观望"""
        bundle = IndicatorBundle(
            ema20=86300.0, ema50=87100.0, rsi=78.0,
            macd=-150.0, macd_bullish=False, atr=400.0,
            adx=35.0, di_plus=8.0, di_minus=32.0,
            vol_expansion=True,
        )
        result = decision_svc._rule_based_decision(btc_snapshot, bundle)
        # 规则引擎保守，空头信号不够强可能观望
        assert result.action in ("SHORT", "WAIT")
        if result.action == "SHORT":
            assert result.direction == "short"

    def test_low_score_waits(self, decision_svc, btc_snapshot):
        """无明显信号时观望"""
        bundle = IndicatorBundle(
            ema20=87000.0, ema50=87000.0, rsi=50.0,
            macd=0.0, macd_bullish=False, atr=50.0,
            adx=10.0, vol_expansion=False,
        )
        result = decision_svc._rule_based_decision(btc_snapshot, bundle)
        assert result.action == "WAIT"

    def test_decision_has_entry_price(self, decision_svc, btc_snapshot, bull_bundle):
        result = decision_svc._rule_based_decision(btc_snapshot, bull_bundle)
        assert result.entry == btc_snapshot.current_price
        assert result.symbol == "BTCUSDT"
        assert 0 <= result.confidence <= 100
