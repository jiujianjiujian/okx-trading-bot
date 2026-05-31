"""信号处理服务 — Webhook → 分析 → 3Commas → 日志"""

import json
import threading
import time
from datetime import datetime, timezone
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

logger = get_logger(__name__)


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

        # AI 自主交易状态
        self._running = False
        self._loop_thread: Optional[threading.Thread] = None

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

                # 选币 + 分析
                candidates = self._market.get_candidates(SCALP_UNIVERSE)
                for c in candidates[:3]:  # 只分析前 3 个
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
                    ok, msg = self._tc.send_signal(decision)
                    if ok:
                        self._risk.on_trade_open(decision)
                        self._notifier.notify_trade_open(decision, 0)
                        time.sleep(5)  # 之间间隔

            except Exception as e:
                logger.error("主循环异常: %s", str(e))

            time.sleep(10)

    # ── PnL 同步 ──────────────────────────────────────

    def _sync_pnl(self):
        """同步 Bybit 已平仓 PnL 到本地数据库"""
        try:
            records = self._bybit.get_pnl_records(days=1)
            for r in records:
                pnl = float(r.get("closedPnl", "0"))
                symbol = r.get("symbol", "")
                if pnl == 0:
                    continue
                rec = PnLRecord(
                    symbol=symbol,
                    pnl_usdt=pnl,
                    time=datetime.now(timezone.utc),
                    closed_by="bybit_sync",
                )
                self._store.log_pnl(rec)
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
