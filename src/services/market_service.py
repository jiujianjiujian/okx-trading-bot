"""市场数据聚合服务 — 整合 Bybit 行情 + K线 + 盘口 + 费率"""

from datetime import datetime, timezone

from ..core.models import MarketSnapshot, KlineFrame
from ..core.enums import SessionType
from ..infrastructure.bybit_client import BybitClient
from ..infrastructure.config import SCALP_UNIVERSE
from ..infrastructure.logging_ import get_logger

logger = get_logger(__name__)

# 可选的扩展交易对（根据流动性动态加入）
EXTENDED_COINS = ["LINKUSDT", "AVAXUSDT", "ARBUSDT", "OPUSDT", "NEARUSDT"]


class MarketService:
    """市场数据聚合"""

    def __init__(self, bybit: BybitClient):
        self._bybit = bybit

    # ── 行情快照 ──────────────────────────────────────

    def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """获取单币种市场快照"""
        ticker = self._bybit.get_ticker(symbol)
        klines_5m = self._bybit.get_klines(symbol, interval="5", limit=100)
        ob = self._bybit.get_orderbook(symbol, depth=5)
        fr = self._bybit.get_funding_rate(symbol)

        current = float(ticker.get("lastPrice", "0"))
        change_24h = float(ticker.get("price24hPcnt", "0")) * 100
        vol_24h = float(ticker.get("turnover24h", "0"))

        # 盘口分析
        bids = ob.get("b", [])
        asks = ob.get("a", [])
        spread = 0.0
        imbalance = 0.0
        if bids and asks:
            best_bid = float(bids[0][0])
            best_ask = float(asks[0][0])
            spread = (best_ask - best_bid) / best_ask * 100 if best_ask > 0 else 0
            bid_vol = sum(float(b[1]) for b in bids[:5])
            ask_vol = sum(float(a[1]) for a in asks[:5])
            total = bid_vol + ask_vol
            imbalance = (bid_vol - ask_vol) / total if total > 0 else 0

        # K 线转帧
        kline_frames = {
            "5m": [_kline_to_frame(k) for k in reversed(klines_5m)],
        }

        base = symbol.replace("USDT", "")

        return MarketSnapshot(
            symbol=symbol,
            symbol_base=base,
            timestamp=datetime.now(timezone.utc),
            current_price=current,
            change_24h=change_24h,
            klines=kline_frames,
            spread_pct=round(spread, 4),
            bid_ask_imbalance=round(imbalance, 4),
            funding_rate=fr,
            volume_24h=vol_24h,
        )

    def get_multi_tf_snapshot(self, symbol: str, timeframes: list[str] | None = None) -> MarketSnapshot:
        """获取多周期 K 线快照"""
        if timeframes is None:
            timeframes = ["1", "5", "15", "60"]
        snap = self.get_snapshot(symbol)
        for tf in timeframes:
            if tf == "5":
                continue  # 已在 get_snapshot 中获取
            klines = self._bybit.get_klines(symbol, interval=tf, limit=100)
            snap.klines[tf] = [_kline_to_frame(k) for k in reversed(klines)]
        return snap

    # ── 选币 ──────────────────────────────────────────

    def get_candidates(self, universe: list[str] | None = None) -> list[dict]:
        """筛选可交易币种 (按 24h 成交量 + 价差排序)"""
        symbols = universe or SCALP_UNIVERSE
        candidates = []
        for sym in symbols:
            try:
                t = self._bybit.get_ticker(sym)
                vol = float(t.get("turnover24h", "0"))
                change = float(t.get("price24hPcnt", "0")) * 100
                price = float(t.get("lastPrice", "0"))
                candidates.append({
                    "symbol": sym,
                    "base": sym.replace("USDT", ""),
                    "volume": vol,
                    "change_24h": abs(change),
                    "price": price,
                    "direction_bias": "long" if change > 0 else "short",
                })
            except Exception:
                pass

        # 按成交量降序
        candidates.sort(key=lambda x: x["volume"], reverse=True)
        return candidates[:8]

    # ── 黑天鹅检测 ────────────────────────────────────

    def check_blackswan(self) -> bool:
        """检测 BTC 10 分钟暴跌 > 3%"""
        try:
            klines = self._bybit.get_klines("BTCUSDT", interval="1", limit=10)
            if not klines:
                return False
            # Bybit 返回最新在前: [0]=now, [-1]=10min前
            newest = float(klines[0][4])
            oldest = float(klines[-1][4])
            if oldest <= 0 or newest <= 0:
                return False
            change_pct = (newest - oldest) / oldest * 100
            if change_pct < -3:
                logger.warning("黑天鹅! BTC 10分钟跌 %.2f%%", abs(change_pct))
                return True
        except Exception as e:
            # 限流/网络错误 → 不触发黑天鹅
            err = str(e)
            if "Rate Limit" in err or "10006" in err:
                return False  # 限流不是黑天鹅
            logger.warning("黑天鹅检测异常: %s", err)
            return False  # 网络抖动, 不要误杀
        return False

    # ── 交易时段 ──────────────────────────────────────

    @staticmethod
    def current_session() -> SessionType:
        """UTC 时间判定交易时段"""
        now = datetime.now(timezone.utc)
        beijing_h = (now.hour + 8) % 24
        if now.weekday() >= 5:
            return SessionType.WEEKEND
        if 20 <= beijing_h < 23:
            return SessionType.OVERLAP   # 欧美重叠 先判断
        elif 15 <= beijing_h < 20:
            return SessionType.EU
        elif 8 <= beijing_h < 15:
            return SessionType.ASIA
        else:
            return SessionType.US  # 23:00-08:00 北京

    @staticmethod
    def session_multiplier(session: SessionType) -> float:
        """不同时段仓位系数"""
        return {
            SessionType.OVERLAP: 1.0,
            SessionType.EU: 0.9,
            SessionType.US: 0.9,
            SessionType.ASIA: 0.7,
            SessionType.WEEKEND: 0.5,
        }.get(session, 0.7)


def _kline_to_frame(k: list) -> KlineFrame:
    """Bybit K线数据转 KlineFrame"""
    return KlineFrame(
        open=float(k[1]),
        high=float(k[2]),
        low=float(k[3]),
        close=float(k[4]),
        volume=float(k[5]),
        timestamp=int(k[0]),
    )
