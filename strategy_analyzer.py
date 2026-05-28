"""
策略分析引擎 - 多维度技术指标分析，TV 信号质量打分

分析维度:
  1. 趋势方向 (EMA排列)
  2. RSI 超买超卖
  3. 成交量确认
  4. MACD 动量
  5. 支撑/阻力距离
  6. 波动率 (ATR)

综合评分 0-100，>=60 通过，<60 拒绝
"""

from dataclasses import dataclass

import http_wrapper as requests
from config import PROXY_URL


@dataclass
class AnalysisResult:
    """分析结果"""
    signal_direction: str         # TV 信号方向
    score: int                    # 综合评分 0-100
    passed: bool                  # 是否通过
    reasons: list                 # 关键理由
    warnings: list                # 警告
    details: dict                 # 各项指标明细
    summary: str                  # 一句话总结


class StrategyAnalyzer:
    """策略分析引擎"""

    def __init__(self):
        self.proxies = {"https": PROXY_URL} if PROXY_URL else None

    # ----------------------------------------------------------------
    # 数据获取
    # ----------------------------------------------------------------

    def fetch_klines(self, symbol: str, bar: str = "1H", limit: int = 200) -> list:
        """
        从 OKX 拉取 K 线数据

        返回: [{open, high, low, close, volume, timestamp}, ...]
        旧→新排序
        """
        url = (
            f"https://www.okx.com/api/v5/market/candles"
            f"?instId={symbol}&bar={bar}&limit={limit}"
        )
        try:
            r = requests.get(url, proxies=self.proxies, timeout=10)
            data = r.json()
            if data.get("code") != "0":
                return []

            candles = []
            for item in reversed(data["data"]):  # OKX 返回新→旧，翻转为旧→新
                candles.append({
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[7]),  # 交易量(USDT)
                    "timestamp": int(item[0]),
                })
            return candles
        except Exception as e:
            print(f"[K线拉取失败] {e}")
            return []

    # ----------------------------------------------------------------
    # 指标计算
    # ----------------------------------------------------------------

    @staticmethod
    def ema(data: list, period: int) -> list:
        """指数移动平均"""
        if len(data) < period:
            return [data[-1]] * len(data) if data else []

        result = [0.0] * len(data)
        # SMA 作为起始值
        sma = sum(data[:period]) / period
        result[period - 1] = sma

        multiplier = 2.0 / (period + 1.0)
        for i in range(period, len(data)):
            result[i] = (data[i] - result[i - 1]) * multiplier + result[i - 1]

        # 填充前面的值
        for i in range(period - 1):
            result[i] = sma

        return result

    @staticmethod
    def rsi(closes: list, period: int = 14) -> float:
        """计算 RSI"""
        if len(closes) < period + 1:
            return 50.0

        gains = 0.0
        losses = 0.0

        for i in range(len(closes) - period, len(closes)):
            delta = closes[i] - closes[i - 1]
            if delta > 0:
                gains += delta
            else:
                losses -= delta

        avg_gain = gains / period
        avg_loss = losses / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    @staticmethod
    def macd(closes: list) -> dict:
        """计算 MACD"""
        ema12 = StrategyAnalyzer.ema(closes, 12)
        ema26 = StrategyAnalyzer.ema(closes, 26)

        macd_line = [ema12[i] - ema26[i] for i in range(len(closes))]
        signal_line = StrategyAnalyzer.ema(macd_line, 9)

        # 最新值
        cur_macd = macd_line[-1]
        cur_signal = signal_line[-1]
        prev_macd = macd_line[-2] if len(macd_line) > 1 else cur_macd
        prev_signal = signal_line[-2] if len(signal_line) > 1 else cur_signal

        return {
            "macd": cur_macd,
            "signal": cur_signal,
            "histogram": cur_macd - cur_signal,
            "is_bullish": cur_macd > cur_signal,
            "crossed_up": prev_macd <= prev_signal and cur_macd > cur_signal,
            "crossed_down": prev_macd >= prev_signal and cur_macd < cur_signal,
        }

    @staticmethod
    def atr(candles: list, period: int = 14) -> float:
        """计算 ATR"""
        if len(candles) < period + 1:
            return 0.0

        tr_list = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)

        return sum(tr_list[-period:]) / period

    @staticmethod
    def volume_ratio(candles: list, period: int = 20) -> float:
        """成交量比（当前/均量）"""
        if len(candles) < period:
            return 1.0

        recent_vol = sum(c["volume"] for c in candles[-3:]) / 3
        avg_vol = sum(c["volume"] for c in candles[-period:]) / period
        return recent_vol / avg_vol if avg_vol > 0 else 1.0

    @staticmethod
    def find_sr_levels(candles: list, lookback: int = 50) -> dict:
        """找近期支撑阻力位"""
        if len(candles) < lookback:
            lookback = len(candles)

        recent = candles[-lookback:]
        highs = sorted(set(round(c["high"], 1) for c in recent), reverse=True)
        lows = sorted(set(round(c["low"], 1) for c in recent))

        current = candles[-1]["close"]

        # 找最近的阻力和支撑（被多次测试的）
        resistances = []
        supports = []

        for price in highs[::-1]:  # 从低到高
            if price > current:
                count = sum(1 for c in recent if abs(c["high"] - price) < price * 0.01)
                if count >= 2:
                    resistances.append({"price": price, "touches": count})

        for price in lows:
            if price < current:
                count = sum(1 for c in recent if abs(c["low"] - price) < price * 0.01)
                if count >= 2:
                    supports.append({"price": price, "touches": count})

        return {
            "nearest_resistance": resistances[-1] if resistances else None,
            "nearest_support": supports[0] if supports else None,
        }

    # ----------------------------------------------------------------
    # 综合评分
    # ----------------------------------------------------------------

    def analyze(self, signal) -> AnalysisResult:
        """
        对 TV 信号进行多维度分析

        返回: AnalysisResult (score, passed, reasons, etc.)
        """
        direction = signal.direction  # "long" or "short"
        reasons = []
        warnings = []
        score = 0
        detail = {}

        # ---- 拉取数据 ----
        candles_1h = self.fetch_klines(signal.okx_symbol, "1H", 200)
        candles_4h = self.fetch_klines(signal.okx_symbol, "4H", 100)

        if len(candles_1h) < 60:
            return AnalysisResult(
                signal_direction=direction, score=0, passed=False,
                reasons=[], warnings=["K 线数据不足，无法分析"],
                details={}, summary="数据不足，拒绝交易"
            )

        closes_1h = [c["close"] for c in candles_1h]
        current_price = closes_1h[-1]

        # ---- 1. 趋势分析 (25分) ----
        ema20 = self.ema(closes_1h, 20)
        ema50 = self.ema(closes_1h, 50)
        trend_up = ema20[-1] > ema50[-1]
        trend_strong = ema20[-1] > ema50[-1] and ema20[-2] > ema50[-2]

        if direction == "long":
            if trend_up:
                score += 25 if trend_strong else 18
                reasons.append("EMA 多头排列，趋势向上")
            else:
                score += 5
                warnings.append("EMA 空头排列中做多，逆势风险较高")
        else:
            if not trend_up:
                score += 25 if (ema20[-1] < ema50[-1] and ema20[-2] < ema50[-2]) else 18
                reasons.append("EMA 空头排列，趋势向下")
            else:
                score += 5
                warnings.append("EMA 多头排列中做空，逆势风险较高")

        detail["ema20"] = round(ema20[-1], 1)
        detail["ema50"] = round(ema50[-1], 1)
        detail["trend"] = "up" if trend_up else "down"

        # ---- 2. RSI 分析 (20分) ----
        rsi_val = self.rsi(closes_1h, 14)
        detail["rsi"] = round(rsi_val, 1)

        if direction == "long":
            if 30 <= rsi_val <= 65:
                score += 20
                reasons.append(f"RSI={rsi_val:.0f} 处于健康区间")
            elif rsi_val < 30:
                score += 18
                reasons.append(f"RSI={rsi_val:.0f} 超卖反弹机会")
            else:
                score += 8
                warnings.append(f"RSI={rsi_val:.0f} 偏高，追多风险大")
        else:
            if 35 <= rsi_val <= 70:
                score += 20
                reasons.append(f"RSI={rsi_val:.0f} 处于健康区间")
            elif rsi_val > 70:
                score += 18
                reasons.append(f"RSI={rsi_val:.0f} 超买回调机会")
            else:
                score += 8
                warnings.append(f"RSI={rsi_val:.0f} 偏低，追空风险大")

        # ---- 3. MACD 动量 (20分) ----
        macd_data = self.macd(closes_1h)
        detail["macd"] = macd_data

        if direction == "long":
            if macd_data["is_bullish"]:
                score += 20 if macd_data["crossed_up"] else 15
                tag = "金叉" if macd_data["crossed_up"] else "多头"
                reasons.append(f"MACD {tag}，动量向上")
            elif macd_data["histogram"] > -1:
                score += 10
                warnings.append("MACD 空头但有收敛迹象")
            else:
                score += 3
                warnings.append("MACD 空头排列，与做多方向背离")
        else:
            if not macd_data["is_bullish"]:
                score += 20 if macd_data["crossed_down"] else 15
                tag = "死叉" if macd_data["crossed_down"] else "空头"
                reasons.append(f"MACD {tag}，动量向下")
            elif macd_data["histogram"] < 1:
                score += 10
                warnings.append("MACD 多头但有衰减迹象")
            else:
                score += 3
                warnings.append("MACD 多头排列，与做空方向背离")

        # ---- 4. 成交量确认 (15分) ----
        vol_ratio = self.volume_ratio(candles_1h)
        detail["volume_ratio"] = round(vol_ratio, 2)

        if vol_ratio >= 1.3:
            score += 15
            reasons.append(f"放量 {vol_ratio:.1f}x，资金参与度高")
        elif vol_ratio >= 0.8:
            score += 10
        else:
            score += 4
            warnings.append(f"缩量 {vol_ratio:.1f}x，市场参与度低")

        # ---- 5. 支撑阻力 (20分) ----
        sr = self.find_sr_levels(candles_4h if candles_4h else candles_1h)
        detail["sr"] = sr

        if direction == "long":
            support = sr.get("nearest_support")
            resistance = sr.get("nearest_resistance")
            if support:
                dist_to_support = (current_price - support["price"]) / current_price * 100
                detail["dist_to_support_pct"] = round(dist_to_support, 2)
                if dist_to_support < 2.0:
                    score += 20
                    reasons.append(f"距支撑 ${support['price']:.0f} 仅 {dist_to_support:.1f}%，止损空间小")
                elif dist_to_support < 5.0:
                    score += 12
                else:
                    score += 6
                    warnings.append(f"距支撑较远 {dist_to_support:.1f}%，止损空间大")
            else:
                score += 8

            if resistance:
                dist_to_res = (resistance["price"] - current_price) / current_price * 100
                detail["dist_to_resistance_pct"] = round(dist_to_res, 2)
                if dist_to_res < 1.0:
                    score -= 5
                    warnings.append(f"距阻力 ${resistance['price']:.0f} 仅 {dist_to_res:.1f}%，空间不足")
        else:
            resistance = sr.get("nearest_resistance")
            support = sr.get("nearest_support")
            if resistance:
                dist_to_res = (resistance["price"] - current_price) / current_price * 100
                detail["dist_to_resistance_pct"] = round(dist_to_res, 2)
                if abs(dist_to_res) < 2.0:
                    score += 20
                    reasons.append(f"距阻力 ${resistance['price']:.0f} 仅 {abs(dist_to_res):.1f}%，止损空间小")
                elif abs(dist_to_res) < 5.0:
                    score += 12
                else:
                    score += 6
                    warnings.append(f"距阻力较远 {abs(dist_to_res):.1f}%，止损空间大")
            else:
                score += 8

            if support:
                dist_to_support = (current_price - support["price"]) / current_price * 100
                detail["dist_to_support_pct"] = round(dist_to_support, 2)
                if dist_to_support < 1.0:
                    score -= 5
                    warnings.append(f"距支撑 ${support['price']:.0f} 仅 {dist_to_support:.1f}%，下行空间不足")

        # ---- 综合评分 ----
        score = max(0, min(100, score))
        passed = score >= 60

        direction_cn = "做多" if direction == "long" else "做空"

        if passed:
            summary = f"信号通过 [{score}分] - {direction_cn} {signal.okx_symbol}"
        else:
            summary = f"信号未通过 [{score}分] - {direction_cn} {signal.okx_symbol}，条件不足"

        return AnalysisResult(
            signal_direction=direction,
            score=score,
            passed=passed,
            reasons=reasons,
            warnings=warnings,
            details=detail,
            summary=summary,
        )

    # ----------------------------------------------------------------
    # Telegram 报告格式化
    # ----------------------------------------------------------------

    @staticmethod
    def format_report(signal, result: AnalysisResult) -> str:
        """生成分析报告（Telegram Markdown）"""
        emoji = "🟢" if result.passed else "🔴"
        direction_cn = "做多" if signal.direction == "long" else "做空"

        msg = f"{emoji} *策略分析报告*\n\n"
        msg += f"📌 {signal.okx_symbol} *{direction_cn}*\n"
        msg += f"💵 价格: ${signal.price:,.1f}\n"
        msg += f"📊 评分: *{result.score}/100*\n"
        msg += f"✅ 判定: {'通过，准备下单' if result.passed else '未通过，已拦截'}\n\n"

        if result.reasons:
            msg += "*✅ 支持理由:*\n"
            for r in result.reasons:
                msg += f"  • {r}\n"

        if result.warnings:
            msg += "\n*⚠️ 风险提示:*\n"
            for w in result.warnings:
                msg += f"  • {w}\n"

        d = result.details
        msg += "\n*📈 指标数据:*\n"
        msg += f"  RSI: {d.get('rsi', '-')} | 趋势: {'📈' if d.get('trend')=='up' else '📉'}\n"
        msg += f"  EMA20: {d.get('ema20', '-')} | EMA50: {d.get('ema50', '-')}\n"
        msg += f"  MACD: {d.get('macd', {}).get('histogram', 0):.2f}\n"
        msg += f"  量比: {d.get('volume_ratio', '-')}x\n"

        sr = d.get('sr', {})
        if sr.get('nearest_support'):
            msg += f"  支撑: ${sr['nearest_support']['price']:.0f}\n"
        if sr.get('nearest_resistance'):
            msg += f"  阻力: ${sr['nearest_resistance']['price']:.0f}\n"

        return msg
