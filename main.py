"""
Bybit 剥头皮交易机器人 V6 — 模块化架构

数据流:
  市场数据 (Bybit) → AI 分析 (DeepSeek) → 剥头皮参数计算
       → 3Commas Webhook → Bybit 执行 → PnL 追踪

  TradingView Webhook 同样走此管线

启动:
  python main.py
"""

import os
import sys
import threading
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ── 导入 ──
from src.infrastructure.config import (
    WEBHOOK_PORT, AI_AUTO_START, TELEGRAM_ENABLED,
)
from src.infrastructure.bybit_client import BybitClient
from src.infrastructure.threecommas_client import ThreeCommasClient
from src.infrastructure.deepseek_client import DeepSeekClient
from src.infrastructure.sqlite_store import SqliteStore
from src.infrastructure.logging_ import get_logger

from src.services.analysis_service import AnalysisService
from src.services.market_service import MarketService
from src.services.decision_service import DecisionService
from src.services.scalping_service import ScalpingService
from src.services.risk_service import RiskService
from src.services.report_service import ReportService
from src.services.signal_service import SignalService

from src.interfaces.notifier_adapters import CompositeNotifier, ConsoleNotifier
from src.interfaces.telegram_bot import TelegramBot
from src.interfaces.webhook_routes import webhook_app
from src.interfaces.api_routes import api_app

from src.shared.di import container

logger = get_logger(__name__)


# ── 防重复启动 ────────────────────────────────────────

def acquire_lock(path: str = ".bot.lock"):
    lock_file = open(path, "a+", encoding="utf-8")  # noqa: SIM115 — lock must stay open
    try:
        if os.name == "nt":
            import msvcrt
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        raise RuntimeError("交易机器人已在运行中") from exc
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


_INSTANCE_LOCK = None
if __name__ == "__main__":
    _INSTANCE_LOCK = acquire_lock()


# ── DI 容器初始化 ─────────────────────────────────────

def bootstrap():
    """组装所有依赖并注册到 DI 容器"""
    logger.info("初始化模块...")

    # 基础设施
    bybit = BybitClient()
    threecommas = ThreeCommasClient()
    deepseek = DeepSeekClient()
    store = SqliteStore()

    # 服务层
    analysis = AnalysisService()
    market = MarketService(bybit)
    decision = DecisionService(deepseek, analysis)
    scalper = ScalpingService()
    risk = RiskService()
    report = ReportService()

    # 通知层
    notifier = CompositeNotifier([ConsoleNotifier()])

    # 信号服务(核心, 组装所有依赖)
    signal_svc = SignalService(
        bybit=bybit,
        market=market,
        analysis=analysis,
        decision=decision,
        scalper=scalper,
        risk=risk,
        report=report,
        threecommas=threecommas,
        store=store,
        notifier=notifier,
    )

    # Telegram Bot
    if TELEGRAM_ENABLED:
        telegram = TelegramBot(bybit, store, signal_svc)
        notifier.add(telegram)
        logger.info("Telegram Bot 已注册")
    else:
        telegram = None

    # 注册到容器
    container.register("bybit", lambda: bybit)
    container.register("store", lambda: store)
    container.register("notifier", lambda: notifier)
    container.register("signal_service", lambda: signal_svc)
    container.register("risk", lambda: risk)

    logger.info(
        "初始化完成 | Bybit %s | 3Commas %s | DeepSeek %s | Telegram %s",
        "✓" if bybit else "✗",
        "✓" if threecommas.configured else "✗",
        "✓" if deepseek.configured else "✗",
        "✓" if telegram else "✗",
    )

    return {
        "bybit": bybit,
        "store": store,
        "notifier": notifier,
        "signal_svc": signal_svc,
        "telegram": telegram,
        "threecommas": threecommas,
        "deepseek": deepseek,
    }


# ── 应用启动 ──────────────────────────────────────────

components = bootstrap()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    print("=" * 60)
    print("⚡ Bybit 剥头皮交易机器人 V6 启动中...")
    print("   目标: 日净利 $50 USDT")
    print("   模式: AI 剥头皮 + 3Commas 执行")
    print(f"   3Commas 信号: {'已配置' if components.get('threecommas') and components['threecommas'].configured else '未配置'}")
    print(f"   Webhook: http://0.0.0.0:{WEBHOOK_PORT}/webhook")
    print("=" * 60)

    # 启动 Telegram
    telegram = components.get("telegram")
    if telegram:
        telegram.start()
        print("[Telegram] Bot 已启动")

    # 自动启动 AI
    svc = components.get("signal_svc")
    if AI_AUTO_START and svc:
        def _notify(msg):
            notifier = container.resolve("notifier")
            notifier.send(msg)
        import asyncio
        await asyncio.sleep(2)
        svc.start_loop()
        print("[AI] 自主交易引擎已自动启动")

    # 定期 PnL 报告 (每小时)
    report_active = True

    def hourly_report():
        while report_active:
            try:
                if svc:
                    stats = svc.get_daily_stats()
                    net = stats.get("net_pnl_usdt", 0)
                    trades = stats.get("total_trades", 0)
                    print(f"[PnL] 今日: ${net:+.2f} | {trades} 笔")
                    if net >= 50:
                        container.resolve("notifier").send(
                            f"🎯 日目标达成! 净利 ${net:+.2f} / $50"
                        )
            except Exception:
                pass
            time.sleep(3600)

    threading.Thread(target=hourly_report, daemon=True).start()

    yield

    report_active = False
    print("机器人已停止")
    if svc:
        svc.stop_loop()
    if telegram:
        telegram.stop()
    components.get("store").close()
    if "bybit" in components:
        components["bybit"].close()


# ── 组装 FastAPI ──────────────────────────────────────

app = FastAPI(title="Bybit Scalping Bot V6", lifespan=lifespan)

# 挂载子应用
app.mount("/", webhook_app)


# 手动注册 API 路由(避免 mount 冲突)
for route in api_app.routes:
    if hasattr(route, "path") and not any(
        r.path == route.path for r in app.routes
    ):
        app.router.routes.append(route)


# ── 入口 ──────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level="info",
        reload=False,
    )
