"""信号处理服务 — Webhook → 分析 → 3Commas → 日志"""

import json
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional

from ..core.models import (
    TradeSignal, MarketSnapshot, ScalpDecision, PnLRecord, DailyStats,
)
from ..core.interfaces import Notifier, SignalStore
from ..infrastructure.bybit_client import BybitClient
from ..infrastructure.threecommas_client import ThreeCommasClient
from ..infrastructure.config import (
    DAILY_TARGET_USDT, AI_AUTO_START, SCALP_UNIVERSE,
)
from ..infrastructure.logging_ import get_logger
from .market_service import MarketService
from .analysis_service import AnalysisService
from .decision_service import DecisionService
from .scalping_service import ScalpingService
from .risk_service import RiskService
from .report_service import ReportService
from .optimization_service import OptimizationService

logger = get_logger(__name__)


# ── 相关性集群 (全仓模式防止同向过载) ─────────────────

_CORRELATION_CLUSTERS: dict[str, str] = {
    # 主流 L1/公链 — BTC 高度相关
    "BTC": "L1", "ETH": "L1", "BNB": "L1",
    "SOL": "L1", "XRP": "L1", "ADA": "L1",
    "AVAX": "L1", "SUI": "L1", "APT": "L1",
    "INJ": "L1", "LINK": "L1",
    # Meme — 情绪独立驱动
    "DOGE": "MEME", "WIF": "MEME", "PEPE": "MEME",
}


def _correlation_cluster(symbol: str) -> str:
    """返回币种的相关性集群标签"""
    base = symbol.replace("USDT", "").upper()
    return _CORRELATION_CLUSTERS.get(base, "OTHER")


class SignalService:
    """Webhook 信号处理 + AI 自主交易调度"""

    def __init__(
        self,
        bybit: BybitClient,
        market: MarketService,
        analysis: AnalysisService,
        decision: DecisionService,
        scalper: ScalpingService,
        risk: RiskService,
        report: ReportService,
        threecommas: ThreeCommasClient,
        store: SignalStore,
        notifier: Notifier,
        optimizer: Optional[OptimizationService] = None,
    ):
        self._bybit = bybit
        self._market = market
        self._analysis = analysis
        self._decision = decision
        self._scalper = scalper
        self._risk = risk
        self._report = report
        self._tc = threecommas
        self._store = store
        self._notifier = notifier
        self._optimizer = optimizer

        # AI 自主交易状态
        self._running = False
        self._loop_thread: Optional[threading.Thread] = None

        # 定时任务状态
        self._last_daily_review_date: Optional[datetime] = None
        self._last_weekly_optimize_date: Optional[datetime] = None

    # ── Webhook 信号处理 ──────────────────────────────

    def process_tv_signal(self, signal: TradeSignal) -> dict:
        """处理 TradingView Webhook 信号"""
        signal_id = self._store.log_signal(signal)
        signal.signal_id = signal_id
        self._notifier.notify_signal(signal)

        # 获取市场数据
        snap = self._market.get_snapshot(signal.bybit_symbol)
        klines = self._bybit.get_klines(signal.bybit_symbol, interval="5", limit=100)
        kline_dicts = [{"open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]}
                       for k in reversed(klines)]
        bundle = self._analysis.compute_bundle(kline_dicts)

        # AI 验证信号
        decision = self._decision.decide(snap, bundle, mode="scalp")
        decision.entry = signal.price
        decision.symbol = signal.bybit_symbol

        self._store.log_decision(decision, signal_id)

        if decision.is_wait:
            self._store.update_signal_status(signal_id, "rejected")
            return {"status": "rejected", "reason": decision.reason}

        # 计算剥头皮参数
        balance = self._bybit.get_account_summary()
        positions = self._bybit.get_positions()
        decision = self._scalper.calculate_parameters(
            decision, bundle, balance.get("equity", 1000), len(positions),
        )

        if decision.is_wait:
            self._store.update_signal_status(signal_id, "rejected")
            return {"status": "rejected", "reason": decision.invalid_condition}

        # 风控
        ok, reason = self._risk.pre_trade_check(decision)
        if not ok:
            self._store.update_signal_status(signal_id, "rejected")
            self._notifier.notify_error(f"风控拦截 {signal.bybit_symbol}", reason)
            return {"status": "rejected", "reason": reason}

        # 发送 3Commas
        ok, msg = self._tc.send_signal(decision)
        if not ok:
            self._notifier.notify_error("3Commas 发送失败", msg)
            return {"status": "error", "message": msg}

        self._store.update_signal_status(signal_id, "sent")
        self._risk.on_trade_open(decision)
        self._notifier.notify_trade_open(decision, signal_id)

        return {"status": "sent", "signal_id": signal_id, "message": msg}

    # ── AI 自主交易循环 ──────────────────────────────

    def start_loop(self, send: Callable | None = None):
        """启动 AI 自主交易主循环"""
        if self._running:
            return
        self._running = True
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()
        logger.info("AI 自主交易循环已启动")

    def stop_loop(self):
        self._running = False
        logger.info("AI 自主交易循环已停止")

    def _run_loop(self):
        """AI 自主交易主循环 — 每 10 秒一轮"""
        last_blackswan_check = 0.0
        last_pnl_sync = 0.0

        while self._running:
            try:
                now = time.time()

                # 黑天鹅检测 (每 30 秒)
                if now - last_blackswan_check > 30:
                    if self._market.check_blackswan():
                        self._risk.on_blackswan()
                        self._notifier.send(self._report.blackswan_warning(3.0))
                    last_blackswan_check = now

                # 每日复盘 (UTC 00:05, 即北京时间 08:05)
                if self._optimizer:
                    utc_now = datetime.now(timezone.utc)
                    today_key = utc_now.date()
                    if (self._last_daily_review_date != today_key
                            and utc_now.hour == 0 and utc_now.minute < 15):
                        self._last_daily_review_date = today_key
                        try:
                            review = self._optimizer.daily_review()
                            if review:
                                stats_data = review.get("stats", {})
                                net_pnl = stats_data.get("net_pnl_usdt", 0)
                                target_hit = net_pnl >= DAILY_TARGET_USDT
                                daily_stats = DailyStats(
                                    date=str(today_key),
                                    total_trades=stats_data.get("total_trades", 0),
                                    wins=stats_data.get("wins", 0),
                                    losses=stats_data.get("losses", 0),
                                    win_rate=stats_data.get("win_rate", 0),
                                    net_pnl_usdt=net_pnl,
                                    total_fees_usdt=stats_data.get("total_fees_usdt", 0),
                                    total_pnl_usdt=stats_data.get("total_pnl_usdt", 0),
                                )
                                self._notifier.send(self._report.daily_stats(daily_stats))
                                if target_hit:
                                    self._notifier.send(self._report.daily_target_reached(daily_stats))
                                if review.get("suggestions"):
                                    self._notifier.send(
                                        f"💡 *优化建议*\n" +
                                        "\n".join(f"• {s}" for s in review["suggestions"])
                                    )
                        except Exception as e:
                            logger.warning("每日复盘失败: %s", str(e))

                    # 每周优化 (每周一 UTC 01:00)
                    if (utc_now.weekday() == 0 and utc_now.hour == 1 and utc_now.minute < 15
                            and self._last_weekly_optimize_date != today_key):
                        self._last_weekly_optimize_date = today_key
                        try:
                            opt = self._optimizer.weekly_optimize()
                            if opt and opt.get("recommendation"):
                                self._notifier.send(
                                    f"📊 *每周策略优化*\n"
                                    f"{opt.get('recommendation', '')}\n\n"
                                    f"建议 SL: {opt.get('sl_pct_min', '?')}-{opt.get('sl_pct_max', '?')}%\n"
                                    f"建议 TP: {opt.get('tp_pct_min', '?')}-{opt.get('tp_pct_max', '?')}%\n"
                                    f"建议置信度: {opt.get('min_confidence', '?')}"
                                )
                                week_stats = opt.get("week_stats", {})
                                if week_stats:
                                    self._notifier.send(
                                        f"本周: {week_stats.get('trades', 0)}笔 | "
                                        f"胜率 {week_stats.get('win_rate', 0)}% | "
                                        f"净利 ${week_stats.get('net_pnl', 0):+.2f}"
                                    )
                        except Exception as e:
                            logger.warning("周优化失败: %s", str(e))

                # PnL 同步 (每 2 分钟)
                if now - last_pnl_sync > 120:
                    self._sync_pnl()
                    last_pnl_sync = now

                # 风控检查
                dummy = ScalpDecision(action="LONG", symbol="")
                ok, reason = self._risk.pre_trade_check(dummy)
                if not ok and "AI 决策为观望" not in reason:
                    time.sleep(10)
                    continue

                # 选币 + 分析 (14币池, 取前6个按成交量排的)
                candidates = self._market.get_candidates(SCALP_UNIVERSE)
                sent_this_cycle = 0
                sent_directions: dict[str, str] = {}  # cluster → direction, 防同向重复

                for c in candidates[:6]:
                    if sent_this_cycle >= 2:  # 每轮最多发2个信号
                        break

                    symbol = c["symbol"]
                    snap = self._market.get_snapshot(symbol)
                    klines = self._bybit.get_klines(symbol, interval="5", limit=100)
                    kline_dicts = [
                        {"open": k[1], "high": k[2], "low": k[3], "close": k[4], "volume": k[5]}
                        for k in reversed(klines)
                    ]
                    bundle = self._analysis.compute_bundle(kline_dicts)

                    # AI 决策
                    decision = self._decision.decide(snap, bundle, mode="scalp")
                    self._store.log_decision(decision, 0)

                    if decision.is_wait:
                        continue

                    # ── 同向相关性过滤 ──
                    cluster = _correlation_cluster(symbol)
                    if cluster in sent_directions and sent_directions[cluster] == decision.direction:
                        logger.info("%s 跳过: 同向 %s 已有 %s",
                                    symbol, cluster, sent_directions[cluster])
                        continue

                    # 剥头皮参数
                    balance = self._bybit.get_account_summary()
                    positions = self._bybit.get_positions()
                    decision = self._scalper.calculate_parameters(
                        decision, bundle, balance.get("equity", 1000), len(positions),
                    )

                    if decision.is_wait:
                        continue

                    # 风控
                    ok, reason = self._risk.pre_trade_check(decision)
                    if not ok:
                        continue

                    # 发送 3Commas
                    self._notifier.notify_scalp_decision(decision)
                    ok, _ = self._tc.send_signal(decision)
                    if ok:
                        self._risk.on_trade_open(decision)
                        self._notifier.notify_trade_open(decision, 0)
                        sent_directions[cluster] = decision.direction or ""
                        sent_this_cycle += 1
                        time.sleep(5)

            except Exception as e:
                logger.error("主循环异常: %s", str(e))

            time.sleep(10)

    # ── PnL 同步 ──────────────────────────────────────

    def _sync_pnl(self):
        """同步 Bybit 已平仓 PnL 到本地数据库"""
        try:
            records = self._bybit.get_pnl_records(days=1)
            # 用 set 去重 (order_id 唯一)
            synced_ids = self._store.get_synced_order_ids() if hasattr(self._store, 'get_synced_order_ids') else set()

            for r in records:
                order_id = r.get("orderId", "")
                pnl = float(r.get("closedPnl", "0"))
                symbol = r.get("symbol", "")
                if pnl == 0 or order_id in synced_ids:
                    continue
                rec = PnLRecord(
                    symbol=symbol,
                    pnl_usdt=pnl,
                    time=datetime.now(timezone.utc),
                    closed_by="bybit_sync",
                    signal_id=0,
                )
                self._store.log_pnl(rec)
                synced_ids.add(order_id)
                self._risk.on_trade_close(symbol, pnl)

            # 检查目标
            stats = self._store.get_today_stats()
            net_pnl = stats.get("net_pnl_usdt", 0)
            if net_pnl >= DAILY_TARGET_USDT:
                logger.info("日目标达成! $%.2f / $%.0f", net_pnl, DAILY_TARGET_USDT)
        except Exception as e:
            logger.error("PnL 同步异常: %s", str(e))

    def get_daily_stats(self) -> dict:
        return self._store.get_today_stats()

    def get_risk_status(self) -> str:
        return self._risk.status_report()
