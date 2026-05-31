"""剥头皮策略回测引擎

在历史K线上模拟 AI决策 + 剥头皮参数, 计算期望收益。
不需要 AI API, 用技术指标规则模拟决策。
"""

from datetime import datetime, timezone
from typing import Optional

from ..core.models import IndicatorBundle
from ..infrastructure.bybit_client import BybitClient
from ..infrastructure.config import (
    SCALP_SL_PCT_MIN, SCALP_SL_PCT_MAX,
    SCALP_TP_PCT_MIN, SCALP_TP_PCT_MAX,
    ESTIMATED_FEE_PCT, MIN_NET_RR,
)
from ..infrastructure.logging_ import get_logger
from .analysis_service import AnalysisService
from .scalping_service import ScalpingService

logger = get_logger(__name__)


class BacktestResult:
    """单次回测结果"""
    def __init__(self):
        self.trades = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl_pct = 0.0
        self.total_fees_pct = 0.0
        self.max_drawdown_pct = 0.0
        self.trade_log: list[dict] = []

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades > 0 else 0

    @property
    def net_pnl_pct(self) -> float:
        return self.total_pnl_pct - self.total_fees_pct

    @property
    def profit_factor(self) -> float:
        gross_loss = sum(
            abs(t["pnl_pct"]) for t in self.trade_log if t["pnl_pct"] < 0
        )
        gross_profit = sum(
            t["pnl_pct"] for t in self.trade_log if t["pnl_pct"] > 0
        )
        return gross_profit / gross_loss if gross_loss > 0 else float("inf")

    def summary(self) -> str:
        return (
            f"交易: {self.trades} | 胜率: {self.win_rate:.1f}% | "
            f"净收益: {self.net_pnl_pct:.2f}% | "
            f"盈利因子: {self.profit_factor:.1f} | "
            f"最大回撤: {self.max_drawdown_pct:.2f}%"
        )


class BacktestService:
    """剥头皮策略回测"""

    def __init__(self, bybit: BybitClient):
        self._bybit = bybit
        self._analysis = AnalysisService()
        self._scalper = ScalpingService()

    def run(
        self,
        symbol: str,
        interval: str = "5",
        days: int = 7,
        sl_pct: Optional[float] = None,
        tp_pct: Optional[float] = None,
        min_confidence: int = 60,
    ) -> BacktestResult:
        """
        在历史K线上回测剥头皮策略

        用技术指标规则模拟 AI 决策:
        - EMA 趋势 + RSI + MACD + ADX 综合打分
        - 得分 ≥ min_confidence → 模拟交易
        - SL/TP 用 scalping_service 计算

        Args:
            symbol: 币种
            interval: K线周期 (5, 15, 60)
            days: 回测天数
            sl_pct: 固定止损%, None=ATR自适应
            tp_pct: 固定止盈%, None=ATR自适应
            min_confidence: 最低置信度

        Returns:
            BacktestResult
        """
        # 获取历史K线
        limit = min(days * 288, 1000)  # 5min K线每天288根
        klines = self._bybit.get_klines(symbol, interval=interval, limit=limit)
        if len(klines) < 50:
            logger.warning("K线不足, 无法回测 %s", symbol)
            return BacktestResult()

        # 反转K线 (Bybit返回最新的在前)
        klines = list(reversed(klines))
        kline_dicts = [
            {"open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]}
            for k in klines
        ]

        result = BacktestResult()
        in_position = False
        entry_price = 0.0
        entry_idx = 0
        direction = ""
        position_sl = 0.0
        position_tp = 0.0
        peak_pnl = 0.0
        current_drawdown = 0.0

        window = 50  # 指标计算窗口

        for i in range(window, len(kline_dicts) - 1):
            window_data = kline_dicts[i - window:i]
            current = kline_dicts[i]
            next_bar = kline_dicts[i + 1]

            if in_position:
                # 检查止盈止损
                high = float(next_bar["high"])
                low = float(next_bar["low"])
                close = float(next_bar["close"])

                if direction == "long":
                    if high >= position_tp:
                        pnl = tp_pct or SCALP_TP_PCT_MIN
                        fee = ESTIMATED_FEE_PCT
                        result.total_pnl_pct += pnl
                        result.total_fees_pct += fee
                        result.wins += 1
                        result.trade_log.append({
                            "symbol": symbol, "direction": direction,
                            "entry": entry_price, "exit": position_tp,
                            "pnl_pct": pnl, "fee_pct": fee,
                            "bars": i - entry_idx, "result": "TP",
                        })
                        in_position = False
                    elif low <= position_sl:
                        pnl = -(sl_pct or SCALP_SL_PCT_MIN)
                        fee = ESTIMATED_FEE_PCT
                        result.total_pnl_pct += pnl
                        result.total_fees_pct += fee
                        result.losses += 1
                        result.trade_log.append({
                            "symbol": symbol, "direction": direction,
                            "entry": entry_price, "exit": position_sl,
                            "pnl_pct": pnl, "fee_pct": fee,
                            "bars": i - entry_idx, "result": "SL",
                        })
                        in_position = False
                else:  # short
                    if low <= position_tp:
                        pnl = tp_pct or SCALP_TP_PCT_MIN
                        fee = ESTIMATED_FEE_PCT
                        result.total_pnl_pct += pnl
                        result.total_fees_pct += fee
                        result.wins += 1
                        result.trade_log.append({
                            "symbol": symbol, "direction": direction,
                            "entry": entry_price, "exit": position_tp,
                            "pnl_pct": pnl, "fee_pct": fee,
                            "bars": i - entry_idx, "result": "TP",
                        })
                        in_position = False
                    elif high >= position_sl:
                        pnl = -(sl_pct or SCALP_SL_PCT_MIN)
                        fee = ESTIMATED_FEE_PCT
                        result.total_pnl_pct += pnl
                        result.total_fees_pct += fee
                        result.losses += 1
                        result.trade_log.append({
                            "symbol": symbol, "direction": direction,
                            "entry": entry_price, "exit": position_sl,
                            "pnl_pct": pnl, "fee_pct": fee,
                            "bars": i - entry_idx, "result": "SL",
                        })
                        in_position = False

                # 更新回撤
                current_pnl = result.total_pnl_pct - result.total_fees_pct
                if current_pnl > peak_pnl:
                    peak_pnl = current_pnl
                current_drawdown = peak_pnl - current_pnl
                if current_drawdown > result.max_drawdown_pct:
                    result.max_drawdown_pct = current_drawdown

                result.trades = result.wins + result.losses
                continue

            # ── 信号生成 (模拟 AI 决策) ──
            bundle = self._analysis.compute_bundle(window_data)
            score = self._rule_score(bundle, float(current["close"]))

            if score < min_confidence:
                continue

            # 决定方向
            if bundle.ema20 > bundle.ema50 and bundle.rsi < 65:
                direction = "long"
            elif bundle.ema20 < bundle.ema50 and bundle.rsi > 35:
                direction = "short"
            else:
                continue

            # 入场
            entry_price = float(current["close"])
            entry_idx = i

            if sl_pct and tp_pct:
                position_sl_pct = sl_pct
                position_tp_pct = tp_pct
            else:
                # ATR 自适应
                atr_pct = (bundle.atr / entry_price * 100) if bundle.atr > 0 else 0.3
                position_sl_pct = max(SCALP_SL_PCT_MIN, min(SCALP_SL_PCT_MAX, atr_pct * 1.5))
                # 确保净盈亏比
                fee_pct = ESTIMATED_FEE_PCT
                min_tp = (position_sl_pct + fee_pct) * MIN_NET_RR + fee_pct
                position_tp_pct = max(SCALP_TP_PCT_MIN, min(min_tp, SCALP_TP_PCT_MAX))

            position_sl_pct = sl_pct or position_sl_pct
            position_tp_pct = tp_pct or position_tp_pct

            if direction == "long":
                position_sl = entry_price * (1 - position_sl_pct / 100)
                position_tp = entry_price * (1 + position_tp_pct / 100)
            else:
                position_sl = entry_price * (1 + position_sl_pct / 100)
                position_tp = entry_price * (1 - position_tp_pct / 100)

            in_position = True

        logger.info("回测 %s %s: %s", symbol, interval, result.summary())
        return result

    @staticmethod
    def _rule_score(bundle: IndicatorBundle, price: float) -> int:
        """规则引擎打分 (模拟AI)"""
        score = 40  # 基础分
        if bundle.ema20 > bundle.ema50:
            score += 15
        elif bundle.ema20 < bundle.ema50:
            score += 5
        if 35 < bundle.rsi < 65:
            score += 10
        if bundle.macd_bullish:
            score += 10
        if bundle.adx > 20:
            score += 10
        if bundle.adx > 30:
            score += 5
        if bundle.vol_expansion:
            score += 5
        if bundle.supertrend != "unknown":
            score += 5
        return min(score, 95)

    def run_batch(
        self, symbols: list[str], interval: str = "5", days: int = 7,
    ) -> dict[str, BacktestResult]:
        """批量回测多个币种"""
        results = {}
        for sym in symbols:
            try:
                results[sym] = self.run(sym, interval=interval, days=days)
            except Exception as e:
                logger.warning("回测 %s 失败: %s", sym, str(e))
                results[sym] = BacktestResult()
        return results
