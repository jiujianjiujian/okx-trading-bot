"""领域枚举"""

from enum import Enum


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class TradeMode(str, Enum):
    SCALP = "scalp"
    SWING = "swing"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EXTREME = "extreme"


class MarketRegimeType(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    SQUEEZE = "squeeze"
    LOW_LIQUIDITY = "low_liquidity"
    NEWS_SHOCK = "news_shock"
    LIQUIDATION = "liquidation"
    FAKE_BREAKOUT = "fake_breakout"
    UNCLEAR = "unclear"


class ScalpMode(str, Enum):
    MOMENTUM = "momentum"          # 追涨杀跌
    MEAN_REVERSION = "mean_reversion"  # 均值回归
    BREAKOUT = "breakout"          # 突破追入
    PULLBACK = "pullback"          # 回踩入场


class SignalStatus(str, Enum):
    PENDING = "pending"            # AI 已生成，待发送
    SENT = "sent"                  # 已发 3Commas
    EXECUTED = "executed"          # 3Commas 已执行
    CLOSED_TP = "closed_tp"       # 止盈平仓
    CLOSED_SL = "closed_sl"       # 止损平仓
    CANCELLED = "cancelled"        # 已取消


class ThreeCommasAction(str, Enum):
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"
    CLOSE = "close"


class SessionType(str, Enum):
    ASIA = "asia"
    EU = "eu"
    US = "us"
    OVERLAP = "overlap"
    WEEKEND = "weekend"
