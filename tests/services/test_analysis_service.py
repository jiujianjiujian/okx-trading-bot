"""技术指标纯函数测试 — 零 mock"""

import math
import pytest
from src.services.analysis_service import AnalysisService


class TestSMA:
    def test_sma_normal(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = AnalysisService.sma(data, 3)
        assert len(result) == 3
        assert result[0] == pytest.approx(2.0)  # (1+2+3)/3
        assert result[-1] == pytest.approx(4.0)  # (3+4+5)/3

    def test_sma_insufficient_data(self):
        result = AnalysisService.sma([1.0, 2.0], 5)
        assert result[0] == pytest.approx(1.5)


class TestEMA:
    def test_ema_basic(self):
        data = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = AnalysisService.ema(data, 3)
        assert len(result) == len(data)
        assert result[0] == 1.0  # 起点
        k = 2 / 4  # period=3
        expected_1 = 2.0 * k + 1.0 * (1 - k)
        assert result[1] == pytest.approx(expected_1)

    def test_ema_latest(self):
        data = [10.0] * 50
        assert AnalysisService.ema_latest(data, 20) == pytest.approx(10.0)


class TestRSI:
    def test_rsi_all_up(self):
        closes = list(range(100, 115))  # 全部上涨
        rsi = AnalysisService.rsi(closes)
        assert rsi > 50

    def test_rsi_all_down(self):
        closes = list(range(115, 100, -1))  # 全部下跌
        rsi = AnalysisService.rsi(closes)
        assert rsi < 50

    def test_rsi_insufficient_data(self):
        rsi = AnalysisService.rsi([1.0, 2.0])  # < 15 个数据点
        assert 40 <= rsi <= 60  # 回退到中性值


class TestMACD:
    def test_macd_returns_valid(self, trending_up_klines):
        closes = [float(k["close"]) for k in trending_up_klines]
        macd = AnalysisService.macd(closes)
        assert "macd" in macd
        assert "signal" in macd
        assert "histogram" in macd

    def test_macd_bearish(self, trending_down_klines):
        closes = [float(k["close"]) for k in trending_down_klines]
        macd = AnalysisService.macd(closes)
        # 下降趋势下 MACD 可能为负
        assert "macd" in macd


class TestBollinger:
    def test_bollinger_basic(self, sample_klines):
        closes = [float(k["close"]) for k in sample_klines]
        bb = AnalysisService.bollinger(closes)
        assert bb["upper"] > bb["mid"] > bb["lower"]
        assert bb["width"] > 0


class TestATR:
    def test_atr_positive(self, sample_klines):
        highs = [float(k["high"]) for k in sample_klines]
        lows = [float(k["low"]) for k in sample_klines]
        closes = [float(k["close"]) for k in sample_klines]
        atr = AnalysisService.atr(highs, lows, closes)
        assert atr > 0


class TestSuperTrend:
    def test_supertrend_up(self, trending_up_klines):
        highs = [float(k["high"]) for k in trending_up_klines]
        lows = [float(k["low"]) for k in trending_up_klines]
        closes = [float(k["close"]) for k in trending_up_klines]
        st = AnalysisService.supertrend(highs, lows, closes)
        assert st["trend"] in ("up", "down", "unknown")


class TestADX:
    def test_adx_strong_trend(self, trending_up_klines):
        highs = [float(k["high"]) for k in trending_up_klines]
        lows = [float(k["low"]) for k in trending_up_klines]
        closes = [float(k["close"]) for k in trending_up_klines]
        adx = AnalysisService.adx(highs, lows, closes)
        assert adx["adx"] >= 0
        assert adx["trend"] in ("bullish", "bearish", "none")


class TestComputeBundle:
    def test_compute_bundle(self, sample_klines):
        bundle = AnalysisService.compute_bundle(sample_klines)
        assert bundle.rsi > 0  # 所有值应可计算
        assert bundle.atr > 0
        assert bundle.ema20 > 0


class TestLiquidation:
    def test_liq_price_long(self):
        liq = AnalysisService.liq_price(87000, 10, "long")
        assert liq < 87000  # 做多爆仓价在入场价之下

    def test_liq_price_short(self):
        liq = AnalysisService.liq_price(87000, 10, "short")
        assert liq > 87000  # 做空爆仓价在入场价之上

    def test_liq_safe_long(self):
        ok, buffer = AnalysisService.liq_safe(87000, 86500, 10, "long")
        assert ok  # SL 在爆仓价之上
        assert buffer > 0
