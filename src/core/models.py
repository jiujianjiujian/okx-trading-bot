"""核心数据模型 — 基于 Bybit + 3Commas 剥头皮架构"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from decimal import Decimal

from .enums import Direction, ScalpMode, SignalStatus


# ── 交易信号 ──────────────────────────────────────────

@dataclass
class TradeSignal:
    """标准化交易信号（TradingView Webhook 或内部 AI 生成）"""
    symbol: str                    # 原始符号 "BTCUSDT"
    bybit_symbol: str              # Bybit 格式 "BTCUSDT"
    direction: str                 # "long" / "short"
    price: float
    stop_loss: float
    take_profit: float
    strategy: str                  # 策略名称
    interval: str                  # K线周期
    comment: str
    raw_data: dict = field(default_factory=dict)
    signal_id: Optional[int] = None


# ── 市场数据 ──────────────────────────────────────────

@dataclass
class KlineFrame:
    """单 K 线帧"""
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: int


@dataclass
class MarketSnapshot:
    """币种全维度市场快照"""
    symbol: str
    symbol_base: str               # "BTC" from "BTCUSDT"
    timestamp: datetime
    current_price: float
    change_24h: float              # 24h涨跌幅 %

    # 多周期 K 线
    klines: dict[str, list[KlineFrame]] = field(default_factory=dict)
    # 盘口
    spread_pct: float = 0.0
    bid_ask_imbalance: float = 0.0
    depth_1pct: float = 0.0
    # 资金费率
    funding_rate: float = 0.0
    open_interest: float = 0.0
    # 成交量
    volume_24h: float = 0.0
    volume_ratio: float = 1.0


# ── 技术指标 ──────────────────────────────────────────

@dataclass
class IndicatorBundle:
    """一组技术指标结果"""
    ema20: float = 0.0
    ema50: float = 0.0
    vwap: float = 0.0
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_histogram: float = 0.0
    macd_bullish: bool = False
    atr: float = 0.0
    bb_upper: float = 0.0
    bb_mid: float = 0.0
    bb_lower: float = 0.0
    bb_width: float = 0.0
    adx: float = 0.0
    di_plus: float = 0.0
    di_minus: float = 0.0
    supertrend: str = "unknown"    # "up" / "down"
    vol_expansion: bool = False
    support: Optional[float] = None
    resistance: Optional[float] = None


# ── 剥头皮决策 ────────────────────────────────────────

@dataclass
class ScalpDecision:
    """AI 剥头皮交易决策，含完整的入场/止损/止盈参数"""
    # 方向
    action: str                    # "LONG" / "SHORT" / "WAIT"
    direction: Optional[str] = None  # "long" / "short"

    # 通用
    symbol: str = ""
    mode: str = "scalp"            # 交易模式
    confidence: int = 0            # AI 置信度 0-100
    reason: str = ""
    scalping_strategy: str = ""    # "momentum" / "mean_reversion" / "breakout"

    # 入场
    entry: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0

    # 盈亏比
    risk_reward: float = 0.0       # 毛盈亏比（扣费前）
    net_risk_reward: float = 0.0   # 净盈亏比（扣费后）
    sl_pct: float = 0.0            # 止损距离 %
    tp_pct: float = 0.0            # 止盈距离 %
    fee_cost: float = 0.0          # 双向手续费 %

    # 仓位
    position_size: float = 0.0     # USDT 名义价值
    risk_usdt: float = 0.0         # 最大亏损 USDT
    expected_profit_usdt: float = 0.0  # 预期盈利 USDT

    # 风控
    risk_level: str = "medium"
    invalid_condition: str = ""

    # 3Commas 信号
    threecommas_signal: Optional[dict] = None

    @property
    def is_trade(self) -> bool:
        return self.action in ("LONG", "SHORT")

    @property
    def is_wait(self) -> bool:
        return self.action == "WAIT"


# ── 3Commas 信号 ─────────────────────────────────────

@dataclass
class ThreeCommasSignal:
    """3Commas Signal Bot Webhook 消息"""
    bot_id: int
    email_token: str
    pair: str                      # "USDT_BTC"
    action: str                    # "open_long" / "open_short" / "close"
    comment: str = ""
    delay_seconds: int = 0


# ── 盈亏记录 ──────────────────────────────────────────

@dataclass
class PnLRecord:
    """单笔交易盈亏记录"""
    id: int = 0
    time: datetime = field(default_factory=datetime.now)
    symbol: str = ""
    direction: str = ""
    entry: float = 0.0
    exit_price: float = 0.0
    position_size: float = 0.0     # USDT
    fee_paid: float = 0.0
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0
    closed_by: str = ""            # "tp" / "sl" / "manual"
    signal_id: Optional[int] = None


@dataclass
class DailyStats:
    """每日交易统计"""
    date: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl_usdt: float = 0.0
    total_fees_usdt: float = 0.0
    net_pnl_usdt: float = 0.0
    avg_rr: float = 0.0
    best_trade: float = 0.0
    worst_trade: float = 0.0
    target_50_usdt: bool = False   # 是否达到 50 USDT 目标


# ── 账户快照 ──────────────────────────────────────────

@dataclass
class AccountSnapshot:
    """Bybit 账户快照"""
    equity: float = 0.0
    available: float = 0.0
    unrealized_pnl: float = 0.0
    positions: int = 0
    margin_used: float = 0.0
