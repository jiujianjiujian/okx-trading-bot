"""核心接口协议 — Notifier, SignalStore, MarketDataProvider"""

from typing import Optional, Protocol, runtime_checkable
from .models import TradeSignal, ScalpDecision, PnLRecord


# ── 通知接口 ──────────────────────────────────────────

@runtime_checkable
class Notifier(Protocol):
    """发送通知到 Telegram / Console / 等"""

    def send(self, text: str, parse_mode: str = "Markdown") -> None:
        """发送任意文本"""
        ...

    def notify_signal(self, signal: TradeSignal) -> None:
        """收到交易信号"""
        ...

    def notify_scalp_decision(self, decision: ScalpDecision) -> None:
        """AI 生成剥头皮决策（发送给3Commas前）"""
        ...

    def notify_trade_open(self, decision: ScalpDecision, signal_id: int) -> None:
        """3Commas 确认执行信号"""
        ...

    def notify_trade_close(self, record: PnLRecord) -> None:
        """交易平仓通知（含盈亏）"""
        ...

    def notify_error(self, title: str, detail: str) -> None:
        """错误/风控拦截通知"""
        ...

    def notify_daily_stats(self, stats) -> None:
        """每日统计推送"""
        ...


# ── 数据存储接口 ──────────────────────────────────────

class SignalStore(Protocol):
    """信号和盈亏记录持久化"""

    def log_signal(self, signal: TradeSignal) -> int:
        """记录信号 → 返回 signal_id"""
        ...

    def log_decision(self, decision: ScalpDecision, signal_id: int) -> int:
        """记录 AI 决策"""
        ...

    def update_signal_status(self, signal_id: int, status: str) -> None:
        """更新信号状态"""
        ...

    def log_pnl(self, record: PnLRecord) -> None:
        """记录平仓盈亏"""
        ...

    def get_today_pnl(self) -> float:
        """今日净盈亏 USDT"""
        ...

    def get_today_trades(self) -> list[dict]:
        """今日交易记录"""
        ...

    def get_today_stats(self) -> dict:
        """今日完整统计"""
        ...

    def get_recent_trades(self, limit: int = 50) -> list[dict]:
        """最近 N 笔交易"""
        ...


# ── 市场数据接口 ──────────────────────────────────────

class MarketDataProvider(Protocol):
    """交易所市场数据提供者"""

    def get_market_price(self, symbol: str) -> float:
        """当前市价"""
        ...

    def get_klines(
        self, symbol: str, interval: str, limit: int = 100,
    ) -> list[dict]:
        """K 线数据"""
        ...

    def get_orderbook(self, symbol: str, depth: int = 25) -> dict:
        """订单簿"""
        ...

    def get_funding_rate(self, symbol: str) -> float:
        """资金费率"""
        ...

    def get_24h_ticker(self, symbol: str) -> dict:
        """24h 行情"""
        ...

    def get_account_summary(self) -> dict:
        """账户信息"""
        ...
