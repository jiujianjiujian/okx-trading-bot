"""技术指标服务 — 全部纯函数，零外部依赖，可直接测试"""

from __future__ import annotations
import math
from ..core.models import IndicatorBundle


class AnalysisService:
    """纯函数技术指标库，所有方法 @staticmethod"""

    # ── 移动平均 ──────────────────────────────────────

    @staticmethod
    def sma(data: list[float], period: int) -> list[float]:
        """简单移动平均"""
        if len(data) < period:
            return [sum(data) / len(data)] if data else [0]
        result = []
        window_sum = sum(data[:period])
        result.append(window_sum / period)
        for i in range(period, len(data)):
            window_sum += data[i] - data[i - period]
            result.append(window_sum / period)
        return result

    @staticmethod
    def ema(data: list[float], period: int) -> list[float]:
        """指数移动平均"""
        if not data:
            return []
        k = 2 / (period + 1)
        result = [data[0]]
        for x in data[1:]:
            result.append(x * k + result[-1] * (1 - k))
        return result

    @staticmethod
    def ema_latest(data: list[float], period: int) -> float:
        ema_vals = AnalysisService.ema(data, period)
        return ema_vals[-1] if ema_vals else 0.0

    # ── RSI ───────────────────────────────────────────

    @staticmethod
    def rsi(closes: list[float], period: int = 14) -> float:
        """RSI 最新值 0-100"""
        if len(closes) < period + 1:
            return 50.0
        gains = []
        losses = []
        for i in range(1, len(closes)):
            delta = closes[i] - closes[i - 1]
            gains.append(delta if delta > 0 else 0)
            losses.append(abs(delta) if delta < 0 else 0)
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    # ── MACD ──────────────────────────────────────────

    @staticmethod
    def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
        """MACD 最新值"""
        ema_fast = AnalysisService.ema(closes, fast)
        ema_slow = AnalysisService.ema(closes, slow)
        if len(ema_fast) < 2 or len(ema_slow) < 2:
            return {"macd": 0, "signal": 0, "histogram": 0, "is_bullish": False, "crossed_up": False, "crossed_down": False}

        macd_line = [ema_fast[i] - ema_slow[i] for i in range(len(ema_fast))]
        signal_line = AnalysisService.ema(macd_line, signal)
        histogram = macd_line[-1] - signal_line[-1]
        prev_hist = macd_line[-2] - signal_line[-2] if len(signal_line) > 1 else 0

        return {
            "macd": macd_line[-1],
            "signal": signal_line[-1],
            "histogram": histogram,
            "is_bullish": histogram > 0,
            "crossed_up": prev_hist <= 0 < histogram,
            "crossed_down": prev_hist >= 0 > histogram,
        }

    # ── 布林带 ────────────────────────────────────────

    @staticmethod
    def bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> dict:
        """布林带 (上轨, 中轨, 下轨, 带宽)"""
        if len(closes) < period:
            return {"upper": 0, "mid": 0, "lower": 0, "width": 0}
        mid = AnalysisService.sma(closes, period)[-1]
        recent = closes[-period:]
        variance = sum((x - mid) ** 2 for x in recent) / period
        std = math.sqrt(variance)
        width = (2 * mult * std) / mid * 100 if mid > 0 else 0
        return {"upper": mid + mult * std, "mid": mid, "lower": mid - mult * std, "width": width}

    # ── ATR ───────────────────────────────────────────

    @staticmethod
    def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
        """Average True Range"""
        if len(closes) < 2:
            return 0.0
        tr_list = []
        for i in range(1, len(closes)):
            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr_list.append(tr)
        if not tr_list:
            return 0.0
        return AnalysisService.ema(tr_list, period)[-1] if len(tr_list) >= period else sum(tr_list) / len(tr_list)

    # ── VWAP ──────────────────────────────────────────

    @staticmethod
    def vwap(highs: list[float], lows: list[float], closes: list[float], volumes: list[float]) -> float:
        """成交量加权均价"""
        if not volumes or sum(volumes) == 0:
            return closes[-1] if closes else 0.0
        tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
        return sum(tp[i] * volumes[i] for i in range(len(closes))) / sum(volumes)

    # ── SuperTrend ────────────────────────────────────

    @staticmethod
    def supertrend(
        highs: list[float], lows: list[float], closes: list[float],
        period: int = 10, mult: float = 3.0,
    ) -> dict:
        """SuperTrend 趋势指标"""
        atr_val = AnalysisService.atr(highs, lows, closes, period)
        if atr_val == 0 or len(closes) < period:
            return {"trend": "unknown", "value": 0, "flip": False, "streak": 0}

        hl2 = [(highs[i] + lows[i]) / 2 for i in range(len(closes))]
        upper_band = [hl2[i] + mult * atr_val for i in range(len(hl2))]
        lower_band = [hl2[i] - mult * atr_val for i in range(len(hl2))]

        trend = "unknown"
        supertrend_val = 0.0
        for i in range(1, len(closes)):
            if closes[i] > upper_band[i - 1]:
                trend = "up"
                supertrend_val = lower_band[i]
            elif closes[i] < lower_band[i - 1]:
                trend = "down"
                supertrend_val = upper_band[i]

        return {"trend": trend, "value": round(supertrend_val, 4), "flip": False, "streak": 0}

    # ── ADX ───────────────────────────────────────────

    @staticmethod
    def adx(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> dict:
        """ADX 趋势强度"""
        if len(closes) < period + 1:
            return {"adx": 0, "di_plus": 0, "di_minus": 0, "trend": "none"}
        dm_plus = []
        dm_minus = []
        tr_vals = []
        for i in range(1, len(closes)):
            up = highs[i] - highs[i - 1]
            dn = lows[i - 1] - lows[i]
            dm_plus.append(max(up, 0) if up > dn else 0)
            dm_minus.append(max(dn, 0) if dn > up else 0)
            tr_vals.append(max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            ))
        tr_ema = AnalysisService.ema(tr_vals, period)[-1] if tr_vals else 1
        di_p = (AnalysisService.ema(dm_plus, period)[-1] / tr_ema * 100) if tr_ema > 0 else 0
        di_m = (AnalysisService.ema(dm_minus, period)[-1] / tr_ema * 100) if tr_ema > 0 else 0
        dx_sum = abs(di_p - di_m) / (di_p + di_m) * 100 if (di_p + di_m) > 0 else 0
        trend = "bullish" if di_p > di_m else "bearish"
        return {"adx": round(dx_sum, 1), "di_plus": round(di_p, 1), "di_minus": round(di_m, 1), "trend": trend}

    # ── K线形态 ───────────────────────────────────────

    @staticmethod
    def candle_patterns(candles: list[dict]) -> list[dict]:
        """识别 K 线形态"""
        if len(candles) < 3:
            return []
        patterns = []
        for i in range(-3, 0):
            c = candles[i]
            body = abs(float(c["close"]) - float(c["open"]))
            upper = float(c["high"]) - max(float(c["open"]), float(c["close"]))
            min(float(c["open"]), float(c["close"])) - float(c["low"])
            total = float(c["high"]) - float(c["low"])
            body_ratio = body / total if total > 0 else 0
            is_bullish = float(c["close"]) > float(c["open"])

            if body_ratio < 0.1 and upper > body * 2:
                patterns.append({"type": "hammer" if is_bullish else "shooting_star", "index": i})
            elif body_ratio > 0.5:
                patterns.append({"type": "strong_bullish" if is_bullish else "strong_bearish", "index": i})
        return patterns

    # ── 波动率 ────────────────────────────────────────

    @staticmethod
    def vol_expansion(volumes: list[float], period: int = 20) -> bool:
        """成交量放大检测"""
        if len(volumes) < period:
            return False
        avg_vol = sum(volumes[-period:]) / period
        return volumes[-1] > avg_vol * 1.5

    # ── 支撑阻力 ──────────────────────────────────────

    @staticmethod
    def find_sr_levels(highs: list[float], lows: list[float], closes: list[float]) -> dict:
        """简单支撑阻力位"""
        if len(closes) < 20:
            return {"support": None, "resistance": None}
        recent = closes[-20:]
        return {
            "support": min(recent[-10:]) if len(recent) >= 10 else None,
            "resistance": max(recent[-10:]) if len(recent) >= 10 else None,
        }

    # ── 完整指标包 ────────────────────────────────────

    @staticmethod
    def compute_bundle(
        klines: list[dict],
        timeframe: str = "5",
    ) -> IndicatorBundle:
        """从 K 线列表计算完整技术指标包"""
        closes = [float(k["close"]) for k in klines]
        highs = [float(k["high"]) for k in klines]
        lows = [float(k["low"]) for k in klines]
        volumes = [float(k["volume"]) for k in klines]

        if len(closes) < 20:
            return IndicatorBundle(atr=0.0, rsi=50.0)

        macd_data = AnalysisService.macd(closes)
        bb = AnalysisService.bollinger(closes)
        adx_data = AnalysisService.adx(highs, lows, closes)
        st = AnalysisService.supertrend(highs, lows, closes)
        sr = AnalysisService.find_sr_levels(highs, lows, closes)

        return IndicatorBundle(
            ema20=AnalysisService.ema_latest(closes, 20),
            ema50=AnalysisService.ema_latest(closes, 50) if len(closes) >= 50 else AnalysisService.ema_latest(closes, 20),
            vwap=AnalysisService.vwap(highs, lows, closes, volumes),
            rsi=AnalysisService.rsi(closes),
            macd=macd_data["macd"],
            macd_signal=macd_data["signal"],
            macd_histogram=macd_data["histogram"],
            macd_bullish=macd_data["is_bullish"],
            atr=AnalysisService.atr(highs, lows, closes),
            bb_upper=bb["upper"],
            bb_mid=bb["mid"],
            bb_lower=bb["lower"],
            bb_width=bb["width"],
            adx=adx_data["adx"],
            di_plus=adx_data["di_plus"],
            di_minus=adx_data["di_minus"],
            supertrend=st["trend"],
            vol_expansion=AnalysisService.vol_expansion(volumes),
            support=sr["support"],
            resistance=sr["resistance"],
        )

    # ── 多周期综合评分 ────────────────────────────────

    @staticmethod
    def multi_tf_score(bundles: dict[str, IndicatorBundle], direction: str) -> float:
        """
        多周期信号融合打分 (0-100)

        bundles: {"1": IndicatorBundle(1min), "5": IndicatorBundle(5min), "15": IndicatorBundle(15min)}
        """
        if not bundles:
            return 50.0

        scores = []
        for _tf, b in bundles.items():
            tf_score = 50.0
            # 趋势方向一致
            if direction == "long":
                if b.ema20 > b.ema50:
                    tf_score += 15
                if b.macd_bullish:
                    tf_score += 10
                if b.rsi > 40 and b.rsi < 70:
                    tf_score += 10
                elif b.rsi < 30:
                    tf_score += 5  # 超卖反弹
                if b.supertrend == "up":
                    tf_score += 10
            else:  # short
                if b.ema20 < b.ema50:
                    tf_score += 15
                if not b.macd_bullish:
                    tf_score += 10
                if b.rsi < 60 and b.rsi > 30:
                    tf_score += 10
                elif b.rsi > 70:
                    tf_score += 5  # 超买回落
                if b.supertrend == "down":
                    tf_score += 10

            # 通用
            if b.adx > 20:
                tf_score += 5
            if b.adx > 30:
                tf_score += 5
            if b.vol_expansion:
                tf_score += 5

            scores.append(min(tf_score, 100.0))

        return sum(scores) / len(scores)

    # ── 爆仓价计算 ────────────────────────────────────

    @staticmethod
    def liq_price(entry: float, lev: int, direction: str) -> float:
        """估算爆仓价"""
        if direction == "long":
            return entry * (1 - 1 / lev + 0.005)
        else:
            return entry * (1 + 1 / lev - 0.005)

    @staticmethod
    def liq_safe(entry: float, sl: float, lev: int, direction: str) -> tuple[bool, float]:
        """止损在爆仓前是否安全"""
        liq = AnalysisService.liq_price(entry, lev, direction)
        if direction == "long":
            buffer = (sl - liq) / entry * 100
            return sl > liq, buffer
        else:
            buffer = (liq - sl) / entry * 100
            return sl < liq, buffer
