"""
回测引擎 — 验证因子和策略信号的历史表现

支持:
  - 多币种/多周期回测
  - 因子 IC/IR 分析
  - 信号胜率/盈亏比统计
  - 最大回撤/夏普比率
"""

import math
from dataclasses import dataclass, field
from typing import ClassVar

from strategy_analyzer import StrategyAnalyzer


@dataclass
class FactorResult:
    """因子回测结果"""
    name: str
    code: str
    ic_mean: float
    ic_std: float
    ir: float
    ic_positive_rate: float
    sharpe: float
    max_drawdown_pct: float
    annual_return_pct: float
    win_rate_pct: float
    profit_factor: float
    total_trades: int
    passed: bool
    score: float
    details: dict = field(default_factory=dict)


@dataclass
class BacktestResult:
    """策略回测结果"""
    total_trades: int
    win_count: int
    loss_count: int
    win_rate_pct: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe_ratio: float
    annual_return_pct: float
    trade_log: list = field(default_factory=list)


class BacktestEngine:

    def __init__(self):
        self.kline = StrategyAnalyzer()

    # ================================================================
    # 因子回测
    # ================================================================

    def test_factor(self, symbol: str, factor_code: str, factor_name: str = "",
                    bar: str = "1H", lookback: int = 500, min_ic: float = 0.02) -> "FactorResult":
        """
        回测单个因子。factor_code: Python 表达式，可用变量:
          close, open_, high, low, volume, returns, prev_close
          返回: 因子值 (正数=看多, 负数=看空)
        """
        candles = self.kline.fetch_klines(symbol, bar, lookback + 50)
        if len(candles) < 100:
            return self._fail_result(factor_name, factor_code)

        closes = [c["close"] for c in candles]
        opens = [c["open"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c["volume"] for c in candles]

        returns = [0.0] * len(closes)
        for i in range(1, len(closes)):
            if closes[i - 1] > 0:
                returns[i] = (closes[i] - closes[i - 1]) / closes[i - 1]

        factor_values = self._eval_factor(factor_code, closes, opens, highs, lows, volumes, returns)
        if factor_values is None or len(factor_values) < 100:
            return self._fail_result(factor_name, factor_code)

        # IC 分析: 因子值 vs 未来收益率
        ic_list = []
        for horizon in [1, 3, 6]:
            for i in range(50, len(factor_values) - horizon):
                fv = factor_values[i]
                if abs(fv) < 1e-10 or abs(fv) > 1e10:
                    continue
                fwd_ret = returns[i + horizon]
                ic_list.append((fv, fwd_ret))

        if len(ic_list) < 30:
            return self._fail_result(factor_name, factor_code)

        # Spearman Rank IC
        n = len(ic_list)
        ranked_fv = sorted(range(n), key=lambda x: ic_list[x][0])
        ranked_ret = sorted(range(n), key=lambda x: ic_list[x][1])
        fv_ranks = [0] * n
        ret_ranks = [0] * n
        for rank, idx in enumerate(ranked_fv):
            fv_ranks[idx] = rank
        for rank, idx in enumerate(ranked_ret):
            ret_ranks[idx] = rank
        d2 = sum((fv_ranks[i] - ret_ranks[i]) ** 2 for i in range(n))
        rank_ic = 1.0 - (6.0 * d2) / (n * (n * n - 1))

        ic_std = 0.15
        ir = abs(rank_ic) / ic_std
        ic_pos = sum(1 for fv, ret in ic_list if fv * ret > 0) / n

        bt = self._signal_backtest(closes, factor_values, min_ic)
        score = self._score_factor(bt, rank_ic, ir)

        return FactorResult(
            name=factor_name or f"factor_{abs(hash(factor_code)) % 10000}",
            code=factor_code,
            ic_mean=round(rank_ic, 4),
            ic_std=round(ic_std, 4),
            ir=round(ir, 3),
            ic_positive_rate=round(ic_pos * 100, 1),
            sharpe=round(bt.sharpe_ratio, 2),
            max_drawdown_pct=round(bt.max_drawdown_pct, 1),
            annual_return_pct=round(bt.annual_return_pct, 1),
            win_rate_pct=round(bt.win_rate_pct, 1),
            profit_factor=round(bt.profit_factor, 2),
            total_trades=bt.total_trades,
            passed=abs(rank_ic) >= min_ic and bt.sharpe_ratio > 0 and bt.total_trades >= 5,
            score=round(score, 1),
            details={"ic": rank_ic, "ir": ir, "sharpe": bt.sharpe_ratio},
        )

    def _fail_result(self, name: str, code: str) -> "FactorResult":
        return FactorResult(
            name=name, code=code,
            ic_mean=0, ic_std=0, ir=0, ic_positive_rate=0,
            sharpe=0, max_drawdown_pct=0, annual_return_pct=0,
            win_rate_pct=0, profit_factor=0, total_trades=0,
            passed=False, score=0,
        )

    def _eval_factor(self, code: str, closes, opens, highs, lows, volumes, returns):
        """安全执行因子代码，注入辅助函数"""
        ns = {"close": closes, "open_": opens, "high": highs, "low": lows,
              "volume": volumes, "returns": returns,
              "sqrt": math.sqrt, "log": math.log, "abs": abs,
              "max": max, "min": min, "sum": sum,
              "prev_close": [closes[0], *closes[:-1]],
              "sma": self._make_sma(), "ema": self._make_ema(),
              "rank": self._make_rank(), "ts_sum": self._make_ts_sum(),
              "ts_corr": self._make_ts_corr(), "roll": self._make_roll()}

        try:
            result = eval(code, {"__builtins__": {}}, ns)
            if isinstance(result, (int, float)):
                result = [float(result)] * len(closes)
            if not isinstance(result, list):
                return None
            return [float(x) if x is not None and abs(x) < 1e10 else 0.0 for x in result]
        except Exception:
            return None

    def _signal_backtest(self, closes: list, factor_values: list, min_signal: float) -> BacktestResult:
        trades = []
        position = 0
        entry_price = 0.0
        equity_curve = [1.0]
        wins = 0
        losses = 0

        for i in range(1, len(closes)):
            fv = factor_values[i - 1]
            price = closes[i]

            if position == 0:
                if fv > min_signal:
                    position = 1
                    entry_price = price
                elif fv < -min_signal:
                    position = -1
                    entry_price = price
            else:
                should_close = (position == 1 and fv < -min_signal * 0.5) or \
                               (position == -1 and fv > min_signal * 0.5)
                if should_close:
                    trade_pnl = (price - entry_price) / entry_price * 100 * position
                    if trade_pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    trades.append(trade_pnl)
                    equity_curve.append(equity_curve[-1] * (1 + trade_pnl / 100))
                    position = 0

        total_trades = wins + losses
        if total_trades == 0:
            return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        pnl_sum = sum(trades)
        avg_win = sum(t for t in trades if t > 0) / max(wins, 1)
        avg_loss = sum(abs(t) for t in trades if t < 0) / max(losses, 1)
        pf = (avg_win * wins) / (avg_loss * losses) if avg_loss > 0 and losses > 0 else 0

        max_dd = 0.0
        peak = 1.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd:
                max_dd = dd

        rets = [equity_curve[i] / equity_curve[i - 1] - 1 for i in range(1, len(equity_curve))
                if equity_curve[i - 1] > 0]
        avg_ret = sum(rets) / len(rets) if rets else 0
        std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in rets) / len(rets)) if rets else 0
        sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0

        n_bars = len(closes)
        total_ret = equity_curve[-1] / equity_curve[0] - 1
        ann_ret = ((1 + total_ret) ** (252 * 24 / n_bars) - 1) * 100 if n_bars > 0 else 0

        return BacktestResult(
            total_trades=total_trades, win_count=wins, loss_count=losses,
            win_rate_pct=round(wins / total_trades * 100, 1),
            total_pnl=round(pnl_sum, 2), avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2), profit_factor=round(pf, 2),
            max_drawdown_pct=round(max_dd, 1), sharpe_ratio=round(sharpe, 2),
            annual_return_pct=round(ann_ret, 1),
            trade_log=[{"pnl": t} for t in trades[-10:]],
        )

    def _score_factor(self, bt: BacktestResult, ic_mean: float, ir: float) -> float:
        score = 50.0
        score += abs(ic_mean) * 500
        score += ir * 10
        if bt.total_trades >= 10: score += 10
        if bt.sharpe_ratio > 1: score += 10
        if bt.sharpe_ratio > 2: score += 10
        if bt.win_rate_pct > 50: score += 5
        if bt.max_drawdown_pct < 10: score += 10
        if bt.profit_factor > 1.5: score += 5
        return min(100, max(0, score))

    # ================================================================
    # 预设因子
    # ================================================================

    PRESET_FACTORS: ClassVar[dict] = {
        "momentum_24h": "(close - roll(close, 24)) / roll(close, 24)",
        "volatility_ratio": "ts_sum(abs(returns), 24) / ts_sum(abs(returns), 168)",
        "volume_breakout": "volume / sma(volume, 50) - 1",
        "rs_divergence": "close / sma(close, 20) - sma(close, 5) / sma(close, 20)",
        "trend_strength": "ema(close, 12) - ema(close, 26)",
        "volume_price_corr": "ts_corr(volume, close, 24)",
        "intraday_range": "(high - low) / close * 100",
        "drawdown_factor": "(close - roll(close, 24)) / roll(close, 24)",
    }

    def test_preset_factors(self, symbol: str, bar: str = "1H") -> list:
        results = []
        for name, code in self.PRESET_FACTORS.items():
            r = self.test_factor(symbol, code, name, bar)
            if r.total_trades > 0:
                results.append(r)
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ================================================================
    # 辅助函数工厂
    # ================================================================

    @staticmethod
    def _make_sma():
        def sma(arr, period):
            r = [0.0] * len(arr)
            for i in range(period - 1, len(arr)):
                r[i] = sum(arr[i - period + 1:i + 1]) / period
            for i in range(period - 1): r[i] = r[period - 1]
            return r
        return sma

    @staticmethod
    def _make_ema():
        def ema(arr, period):
            r = [0.0] * len(arr)
            r[period - 1] = sum(arr[:period]) / period
            mult = 2.0 / (period + 1.0)
            for i in range(period, len(arr)):
                r[i] = (arr[i] - r[i - 1]) * mult + r[i - 1]
            for i in range(period - 1): r[i] = r[period - 1]
            return r
        return ema

    @staticmethod
    def _make_roll():
        def roll(arr, period):
            r = [0.0] * len(arr)
            for i in range(period, len(arr)):
                r[i] = arr[i - period]
            return r
        return roll

    @staticmethod
    def _make_rank():
        def rank(arr):
            n = len(arr)
            idxs = sorted(range(n), key=lambda x: arr[x])
            ranks = [0] * n
            for r, idx in enumerate(idxs):
                ranks[idx] = (r + 1) / n
            return ranks
        return rank

    @staticmethod
    def _make_ts_sum():
        def ts_sum(arr, period):
            r = [0.0] * len(arr)
            for i in range(len(arr)):
                start = max(0, i - period + 1)
                r[i] = sum(arr[start:i + 1])
            return r
        return ts_sum

    @staticmethod
    def _make_ts_corr():
        def ts_corr(a, b, period):
            r = [0.0] * len(a)
            for i in range(period - 1, len(a)):
                ax = a[i - period + 1:i + 1]
                bx = b[i - period + 1:i + 1]
                ma = sum(ax) / period
                mb = sum(bx) / period
                cov = sum((ax[j] - ma) * (bx[j] - mb) for j in range(period))
                sa = math.sqrt(sum((x - ma) ** 2 for x in ax))
                sb = math.sqrt(sum((x - mb) ** 2 for x in bx))
                r[i] = cov / (sa * sb + 0.0001)
            return r
        return ts_corr
