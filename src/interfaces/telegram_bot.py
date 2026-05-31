"""Telegram Bot — 通知 + 命令处理"""

import threading
from typing import Optional

from ..core.models import TradeSignal, ScalpDecision, PnLRecord, DailyStats
from ..core.interfaces import Notifier
from ..infrastructure.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from ..infrastructure.bybit_client import BybitClient
from ..infrastructure.logging_ import get_logger
from ..services.report_service import ReportService

logger = get_logger(__name__)


class TelegramBot(Notifier):
    """Telegram 通知器 + 命令处理器

    实现 Notifier 协议, 同时处理用户命令:
    /start /help /balance /stats /pause /resume /market /risk
    """

    def __init__(
        self,
        bybit: BybitClient,
        store,  # SignalStore
        signal_service,  # SignalService
    ):
        self._bybit = bybit
        self._store = store
        self._signal = signal_service
        self._report = ReportService()
        self._chat_id = TELEGRAM_CHAT_ID
        self._token = TELEGRAM_BOT_TOKEN
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 延迟导入 python-telegram-bot
        self._app = None

    # ── Notifier 接口 ─────────────────────────────────

    def send(self, text: str, parse_mode: str = "Markdown") -> None:
        self._send_message(text, parse_mode)

    def notify_signal(self, signal: TradeSignal) -> None:
        text = self._report.signal_header(signal)
        self._send_message(text)

    def notify_scalp_decision(self, decision: ScalpDecision) -> None:
        text = self._report.scalp_decision_preview(decision)
        self._send_message(text)

    def notify_trade_open(self, decision: ScalpDecision, signal_id: int) -> None:
        text = self._report.trade_opened(decision, signal_id)
        self._send_message(text)

    def notify_trade_close(self, rec: PnLRecord) -> None:
        text = self._report.trade_closed(rec)
        self._send_message(text)

    def notify_error(self, title: str, detail: str) -> None:
        text = self._report.error_notice(title, detail)
        self._send_message(text)

    def notify_daily_stats(self, stats) -> None:
        text = self._report.daily_stats(stats)
        self._send_message(text)

    # ── Telegram Bot 生命周期 ─────────────────────────

    def start(self):
        if not self._token or not self._chat_id:
            logger.warning("Telegram 未配置, 跳过启动")
            return
        self._running = True

        def _run_bot():
            import asyncio
            from telegram.ext import Application, CommandHandler

            app = Application.builder().token(self._token).build()
            app.add_handler(CommandHandler("start", self._cmd_start))
            app.add_handler(CommandHandler("help", self._cmd_help))
            app.add_handler(CommandHandler("balance", self._cmd_balance))
            app.add_handler(CommandHandler("stats", self._cmd_stats))
            app.add_handler(CommandHandler("pause", self._cmd_pause))
            app.add_handler(CommandHandler("resume", self._cmd_resume))
            app.add_handler(CommandHandler("market", self._cmd_market))
            app.add_handler(CommandHandler("risk", self._cmd_risk))
            self._app = app
            # 用 start_polling 而非 run_polling — 避免 Linux 信号处理器冲突
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(app.initialize())
                loop.run_until_complete(app.start())
                loop.run_until_complete(app.updater.start_polling())
                loop.run_forever()
            except Exception as e:
                logger.error("Telegram 崩溃: %s", str(e), exc_info=True)
            finally:
                if loop.is_running():
                    loop.run_until_complete(app.stop())
                    loop.run_until_complete(app.shutdown())
                loop.close()

        self._thread = threading.Thread(target=_run_bot, daemon=False)
        self._thread.start()
        logger.info("Telegram Bot 已启动 (命令+通知)")

    def stop(self):
        self._running = False
        if self._app:
            try:
                import asyncio
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self._app.stop())
                loop.close()
            except Exception:
                pass

    # ── 命令处理器 ────────────────────────────────────

    async def _cmd_start(self, update, context):
        await update.message.reply_text(
            "🤖 *Bybit 剥头皮交易机器人 V6*\n\n"
            "📊 *命令:*\n"
            "/balance — 账户余额\n"
            "/stats — 今日统计\n"
            "/market <币种> — AI 看盘\n"
            "/risk — 风控状态\n"
            "/pause — 暂停 AI 交易\n"
            "/resume — 恢复 AI 交易\n"
            "/help — 帮助",
            parse_mode="Markdown",
        )

    async def _cmd_help(self, update, context):
        await self._cmd_start(update, context)

    async def _cmd_balance(self, update, context):
        try:
            acct = self._bybit.get_account_summary()
            text = (
                f"💰 *账户*\n"
                f"净值: ${acct.get('equity', 0):.2f}\n"
                f"可用: ${acct.get('available', 0):.2f}\n"
                f"浮亏: ${acct.get('unrealized_pnl', 0):+.2f}"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"查询失败: {e}")

    async def _cmd_stats(self, update, context):
        try:
            s = self._store.get_today_stats()
            text = self._report.daily_stats(DailyStats(**s))
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"查询失败: {e}")

    async def _cmd_pause(self, update, context):
        self._signal.stop_loop()
        await update.message.reply_text("⏸ AI 自主交易已暂停")

    async def _cmd_resume(self, update, context):
        self._signal.start_loop()
        await update.message.reply_text("▶ AI 自主交易已恢复")

    async def _cmd_market(self, update, context):
        symbol = "BTCUSDT"
        if context.args:
            symbol = context.args[0].upper()
            if not symbol.endswith("USDT"):
                symbol += "USDT"
        try:
            ticker = self._bybit.get_ticker(symbol)
            price = float(ticker.get("lastPrice", "0"))
            change = float(ticker.get("price24hPcnt", "0")) * 100
            text = (
                f"📈 *{symbol}*\n"
                f"价格: ${price:.4f}\n"
                f"24h: {change:+.2f}%"
            )
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"查询失败: {e}")

    async def _cmd_risk(self, update, context):
        text = self._signal.get_risk_status()
        await update.message.reply_text(text, parse_mode="Markdown")

    # ── 内部 ──────────────────────────────────────────

    def _send_message(self, text: str, parse_mode: str = "Markdown") -> None:
        """直接发送消息到 Telegram"""
        if not self._token or not self._chat_id:
            return
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            payload = {
                "chat_id": self._chat_id,
                "text": text[:4000],
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            }
            requests.post(url, json=payload, timeout=10)
        except Exception:
            pass
