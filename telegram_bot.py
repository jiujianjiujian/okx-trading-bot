"""
Telegram 机器人 - 信号通知、交易确认、查询指令
"""

import asyncio
import contextlib
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.request import HTTPXRequest

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_PROXY, AUTO_TRADE, DEEPSEEK_API_KEY
from okx_client import OKXClient
from trade_logger import TradeLogger


class TelegramBot:
    """Telegram 通知和交互"""

    def __init__(self):
        self.token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.proxy = TELEGRAM_PROXY
        self.app = None
        self.okx = None
        self.logger = None
        self.reviewer = None
        self.auto_trader = None
        self._thread = None
        self._loop = None

    def set_dependencies(self, okx: OKXClient, logger: TradeLogger, reviewer=None, auto_trader=None):
        self.okx = okx
        self.logger = logger
        self.reviewer = reviewer
        self.auto_trader = auto_trader

    # ----------------------------------------------------------------
    # 启动
    # ----------------------------------------------------------------

    def start(self):
        """在后台线程启动 Telegram Bot"""
        if not self.token:
            print("[Telegram] 未配置 BOT_TOKEN，跳过启动")
            return

        def _run():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop

            builder = Application.builder().token(self.token)

            if self.proxy:
                request = HTTPXRequest(
                    proxy=self.proxy,
                    connect_timeout=20,
                    read_timeout=20,
                    httpx_kwargs={"verify": False},
                )
                builder = builder.request(request)
                print(f"[Telegram] 使用代理: {self.proxy}")
            else:
                # 国内直连也得加超时
                request = HTTPXRequest(connect_timeout=20, read_timeout=20, httpx_kwargs={"verify": False})
                builder = builder.request(request)

            self.app = builder.build()
            self._register_handlers()
            print("[Telegram] 机器人已启动")
            self.app.run_polling(stop_signals=())

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def _register_handlers(self):
        """注册指令处理"""
        self.app.add_handler(CommandHandler("start", self._cmd_start))
        self.app.add_handler(CommandHandler("help", self._cmd_help))
        self.app.add_handler(CommandHandler("balance", self._cmd_balance))
        self.app.add_handler(CommandHandler("positions", self._cmd_positions))
        self.app.add_handler(CommandHandler("pnl", self._cmd_pnl))
        self.app.add_handler(CommandHandler("stats", self._cmd_stats))
        self.app.add_handler(CommandHandler("close", self._cmd_close))
        self.app.add_handler(CommandHandler("autotrade", self._cmd_autotrade))
        self.app.add_handler(CommandHandler("review", self._cmd_review))
        self.app.add_handler(CommandHandler("start_ai", self._cmd_start_ai))
        self.app.add_handler(CommandHandler("stop_ai", self._cmd_stop_ai))
        self.app.add_handler(CommandHandler("market", self._cmd_market))
        self.app.add_handler(CommandHandler("scan", self._cmd_scan))
        self.app.add_handler(CommandHandler("menu", self._cmd_menu))
        self.app.add_handler(CallbackQueryHandler(self._handle_button))

    # ----------------------------------------------------------------
    # 发送消息（主动推送）
    # ----------------------------------------------------------------

    async def _send_async(self, text: str, parse_mode: str = "Markdown"):
        """异步发送消息到配置的 Chat ID"""
        if not self.app or not self.chat_id:
            return
        try:
            await self.app.bot.send_message(
                chat_id=self.chat_id, text=text, parse_mode=parse_mode
            )
        except Exception as e:
            print(f"[Telegram] 发送失败: {e}")

    def send(self, text: str, parse_mode: str = "Markdown"):
        """同步方式发送消息（从外部线程调用）"""
        if not self.app or not self.chat_id:
            return
        try:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self._send_async(text, parse_mode), self._loop
                )
            elif self._loop:
                self._loop.call_soon_threadsafe(
                    lambda: asyncio.create_task(self._send_async(text, parse_mode))
                )
        except Exception as e:
            print(f"[Telegram] 发送失败: {e}")

    # ----------------------------------------------------------------
    # 信号通知模板
    # ----------------------------------------------------------------

    def notify_signal(self, signal):
        """收到信号通知"""
        emoji = "🟢" if signal.direction == "long" else "🔴"
        direction_cn = "做多" if signal.direction == "long" else "做空"
        auto_status = "自动执行" if AUTO_TRADE else "手动审核"

        msg = (
            f"{emoji} *交易信号来了*\n\n"
            f"📌 币种: `{signal.okx_symbol}`\n"
            f"📈 方向: *{direction_cn}*\n"
            f"💵 价格: ${signal.price:,.1f}\n"
        )
        if signal.stop_loss:
            msg += f"🛑 止损: ${signal.stop_loss:,.1f}\n"
        if signal.take_profit:
            msg += f"🎯 止盈: ${signal.take_profit:,.1f}\n"
        if signal.strategy:
            msg += f"🧠 策略: {signal.strategy}\n"
        if signal.interval:
            msg += f"⏱ 周期: {signal.interval}\n"
        if signal.comment:
            msg += f"💬 备注: {signal.comment}\n"
        msg += f"\n⚙️ 模式: {auto_status}"

        self.send(msg)

    def notify_trade(self, signal, quantity: int, leverage: int, order_id: str):
        """下单成功通知"""
        direction_cn = "做多" if signal.direction == "long" else "做空"
        msg = (
            f"✅ *已下单*\n\n"
            f"📌 {signal.okx_symbol} *{direction_cn}*\n"
            f"📦 {quantity} 张 | {leverage}x 杠杆\n"
            f"💵 入场: ${signal.price:,.1f}\n"
        )
        if signal.stop_loss:
            msg += f"🛑 止损: ${signal.stop_loss:,.1f}\n"
        if signal.take_profit:
            msg += f"🎯 止盈: ${signal.take_profit:,.1f}\n"
        msg += f"\n🆔 订单: `{order_id}`"

        self.send(msg)

    def notify_reject(self, signal, reason: str):
        """风控拦截通知"""
        msg = (
            f"⛔ *交易被拦截*\n\n"
            f"📌 {signal.okx_symbol} {signal.direction.upper()}\n"
            f"💵 ${signal.price:,.1f}\n"
            f"❌ 原因: {reason}"
        )
        self.send(msg)

    def notify_close(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float):
        """平仓通知"""
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} *仓位已平*\n\n"
            f"📌 {symbol} {side.upper()}\n"
            f"📥 入场: ${entry:,.1f}\n"
            f"📤 出场: ${exit_price:,.1f}\n"
            f"💰 盈亏: {pnl:+.2f} USDT"
        )
        self.send(msg)

    def notify_error(self, title: str, detail: str):
        """错误通知"""
        self.send(f"⚠️ *{title}*\n{detail}")

    # ----------------------------------------------------------------
    # 指令处理
    # ----------------------------------------------------------------

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 *OKX 交易机器人已就绪*\n\n"
            "我会在这里推送交易信号和执行结果。\n\n"
            "指令列表:\n"
            "/balance - 查看账户余额\n"
            "/positions - 查看当前持仓\n"
            "/pnl - 查看今日盈亏\n"
            "/stats - 查看交易统计\n"
            "/close SYMBOL - 平仓\n"
            "/autotrade - 查看自动交易状态\n"
            "/help - 帮助",
            parse_mode="Markdown",
        )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "📖 *帮助*\n\n"
            "*TradingView 警报设置:*\n"
            "Webhook URL: `http://你的IP:8000/webhook`\n"
            "消息格式 (JSON):\n"
            '{"signal":"long","symbol":"BTCUSDT","price":76800,'
            '"stop_loss":76000,"take_profit":79000,'
            '"strategy":"LuxAlgo","interval":"1h"}',
            parse_mode="Markdown",
        )

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.okx:
            return
        summary = self.okx.get_account_summary()
        await update.message.reply_text(summary, parse_mode="Markdown")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.okx:
            return
        positions = self.okx.get_positions()
        if not positions:
            await update.message.reply_text("📭 当前无持仓")
            return

        text = "*当前持仓:*\n"
        for pos in positions:
            emoji = "🟢" if pos["upl"] >= 0 else "🔴"
            text += (
                f"\n{emoji} {pos['instId']} {pos['side'].upper()} "
                f"{pos['quantity']}张\n"
                f"  均价: {pos['avgPx']:.1f} | 标记: {pos['markPx']:.1f}\n"
                f"  浮动: {pos['upl']:.2f} USDT | 杠杆: {pos['lever']}x"
            )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_pnl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.logger:
            return
        pnl = self.logger.get_today_pnl()
        emoji = "🟢" if pnl >= 0 else "🔴"
        await update.message.reply_text(
            f"{emoji} *今日盈亏:* {pnl:+.2f} USDT", parse_mode="Markdown"
        )

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.logger:
            return
        stats = self.logger.get_trade_stats()
        text = (
            "📊 *交易统计*\n\n"
            f"总交易: {stats['total_trades']}\n"
            f"胜: {stats['win_count']} | 负: {stats['loss_count']}\n"
            f"胜率: {stats['win_rate']}\n"
            f"累计盈亏: {stats['total_pnl']}"
        )
        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """平仓指令: /close BTC-USDT-SWAP"""
        if not self.okx:
            return
        if not context.args:
            await update.message.reply_text("请指定币种，如: `/close BTC-USDT-SWAP`", parse_mode="Markdown")
            return

        symbol = context.args[0].upper()
        if not symbol.endswith("-SWAP"):
            symbol = symbol + "-SWAP"

        ok, msg = self.okx.close_position(symbol)
        if ok:
            await update.message.reply_text(f"✅ 已提交平仓: {symbol}")
        else:
            await update.message.reply_text(f"❌ 平仓失败: {msg}")

    async def _cmd_autotrade(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        status = "✅ 开启" if AUTO_TRADE else "⏸ 关闭（仅通知）"
        await update.message.reply_text(f"⚙️ 自动交易: {status}")

    async def _cmd_review(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """每日复盘: /review"""
        if not self.reviewer or not self.logger:
            await update.message.reply_text("❌ 复盘模块未初始化")
            return

        trades = self.logger.get_today_trades()
        closed = [t for t in trades if t[9] == "closed"]

        if not closed:
            await update.message.reply_text("📭 今天还没有已平仓的交易")
            return

        await update.message.reply_text("🤖 正在调用 DeepSeek AI 做每日复盘+参数优化...")

        trade_dicts = [self._trade_row_to_dict(t) for t in closed]
        report = self.reviewer.daily_review(trade_dicts)
        await update.message.reply_text(
            f"🤖 *DeepSeek 每日复盘 & 参数优化建议*\n\n{report}",
            parse_mode="Markdown",
        )

    async def _cmd_start_ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """启动 AI 自主交易: /start_ai"""
        if not self.auto_trader:
            await update.message.reply_text("❌ AI 引擎未初始化")
            return

        if not DEEPSEEK_API_KEY:
            await update.message.reply_text("❌ 请先配置 DEEPSEEK_API_KEY")
            return

        if self.auto_trader._running:
            await update.message.reply_text("🤖 AI 引擎已在运行中")
            return

        self.auto_trader.start(self.send)
        await update.message.reply_text("🤖 *AI 自主交易引擎已启动*\n每1H分析市场，自信度>=65时自动下单", parse_mode="Markdown")

    async def _cmd_stop_ai(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """停止 AI 自主交易: /stop_ai"""
        if not self.auto_trader:
            return
        self.auto_trader.stop()
        await update.message.reply_text("🛑 AI 引擎已停止")

    async def _cmd_market(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """AI 快速看盘: /market [symbol]"""
        if not self.auto_trader:
            await update.message.reply_text("❌ AI 引擎未初始化")
            return

        symbol = context.args[0] if context.args else "BTC-USDT-SWAP"
        await update.message.reply_text(f"🤖 正在分析 {symbol}...")
        report = self.auto_trader.market_report(symbol)
        await update.message.reply_text(
            f"🤖 *AI 盘面分析 [{symbol}]*\n\n{report}", parse_mode="Markdown"
        )

    async def _cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """高级市场扫描: /scan [symbol]"""
        if not self.auto_trader:
            await update.message.reply_text("❌ 引擎未初始化")
            return
        symbol = context.args[0] if context.args else "BTC-USDT-SWAP"
        await update.message.reply_text(f"🔬 扫描 {symbol}...")
        report = self.auto_trader._advanced_market_report(symbol)
        await update.message.reply_text(report, parse_mode="Markdown")

    async def _cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """弹出功能菜单"""
        keyboard = [
            [InlineKeyboardButton("🤖 启动AI交易", callback_data="start_ai"),
             InlineKeyboardButton("🛑 停止AI交易", callback_data="stop_ai")],
            [InlineKeyboardButton("📊 AI看盘", callback_data="market"),
             InlineKeyboardButton("📋 每日复盘", callback_data="review")],
            [InlineKeyboardButton("💰 查余额", callback_data="balance"),
             InlineKeyboardButton("📈 查持仓", callback_data="positions")],
            [InlineKeyboardButton("📊 交易统计", callback_data="stats"),
             InlineKeyboardButton("❓ 帮助", callback_data="help")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(
            "📋 *功能菜单*\n选择一个操作：",
            reply_markup=reply_markup, parse_mode="Markdown"
        )

    async def _handle_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理菜单按钮点击"""
        query = update.callback_query
        await query.answer()

        cmd = query.data
        if cmd == "start_ai":
            await self._cmd_start_ai(update, context)
        elif cmd == "stop_ai":
            await self._cmd_stop_ai(update, context)
        elif cmd == "market":
            await query.message.reply_text("🤖 正在分析 BTC...")
            if self.auto_trader:
                report = self.auto_trader.market_report("BTC-USDT-SWAP")
                await query.message.reply_text(
                    f"🤖 *AI 盘面分析*\n\n{report}", parse_mode="Markdown"
                )
        elif cmd == "review":
            await self._cmd_review(update, context)
        elif cmd == "balance":
            await self._cmd_balance(update, context)
        elif cmd == "positions":
            await self._cmd_positions(update, context)
        elif cmd == "stats":
            await self._cmd_stats(update, context)
        elif cmd == "help":
            await self._cmd_help(update, context)

    @staticmethod
    def _trade_row_to_dict(row) -> dict:
        """将数据库行转为 dict"""
        cols = ["id", "signal_id", "time", "symbol", "direction", "entry_price",
                "quantity", "leverage", "stop_loss", "take_profit", "order_id",
                "status", "exit_price", "pnl", "close_time"]
        d = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
        for k in ("entry_price", "exit_price", "pnl", "stop_loss", "take_profit", "quantity", "leverage"):
            if k in d and d[k] is not None:
                with contextlib.suppress(ValueError, TypeError):
                    d[k] = float(d[k])
        return d
