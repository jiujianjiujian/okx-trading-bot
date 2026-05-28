"""
QQ 机器人 — 基于 NapCatQQ + OneBot v11 反向 WebSocket

支持:
  - 交易命令 (/balance, /positions, /pnl, ...)
  - 自由对话 (Claude API)
  - 交易信号推送通知

架构:
  NapCatQQ (Client) ──reverse WS──> qq_bot.py (Server)
"""

import asyncio
import contextlib
import json
import sys
import threading
import traceback

import websockets

# 确保 stdout 支持 UTF-8（Windows GBK 终端会导致 emoji print 崩溃）
if sys.platform == "win32":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from websockets.asyncio.server import serve as ws_serve

from config import (
    QQ_BOT_ENABLED,
    QQ_WS_HOST,
    QQ_WS_PORT,
    QQ_WS_TOKEN,
    QQ_ADMIN_IDS,
    QQ_GROUP_ID,
    AUTO_TRADE,
    DEEPSEEK_API_KEY,
)


class QQBot:
    """QQ 机器人 — OneBot v11 反向 WebSocket 服务器"""

    def __init__(self):
        self._server = None
        self._thread = None
        self._loop = None
        self._connections: set = set()

        # 依赖注入
        self.okx = None
        self.logger = None
        self.reviewer = None
        self.auto_trader = None
        self.claude = None
        self.conversation_mgr = None

    def set_dependencies(self, okx=None, logger=None, reviewer=None,
                         auto_trader=None, claude=None, conversation_mgr=None):
        self.okx = okx
        self.logger = logger
        self.reviewer = reviewer
        self.auto_trader = auto_trader
        self.claude = claude
        self.conversation_mgr = conversation_mgr

    # ================================================================
    # 启动 / 停止
    # ================================================================

    def start(self):
        if not QQ_BOT_ENABLED:
            print("[QQ Bot] 未启用，跳过启动 (QQ_BOT_ENABLED=false)")
            return

        def _run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._start_server())
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server:
            self._server.close()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)

    async def _start_server(self):
        try:
            self._server = await ws_serve(
                self._on_connect,
                host=QQ_WS_HOST,
                port=QQ_WS_PORT,
            )
            print(f"[QQ Bot] OneBot v11 WS 服务器已启动: ws://{QQ_WS_HOST}:{QQ_WS_PORT}")
        except OSError as e:
            print(f"[QQ Bot] 启动 WS 服务器失败 (端口 {QQ_WS_PORT} 可能被占用): {e}")
            return

    # ================================================================
    # WebSocket 连接处理
    # ================================================================

    async def _on_connect(self, websocket):
        path = websocket.request.path if hasattr(websocket, 'request') else "/"
        if path != "/onebot/v11/ws" and path != "/":
            await websocket.close(4001, "Invalid path")
            return

        if QQ_WS_TOKEN:
            auth = websocket.request.headers.get("Authorization", "")
            if auth != f"Bearer {QQ_WS_TOKEN}":
                await websocket.close(4002, "Unauthorized")
                return

        self._connections.add(websocket)
        peer = websocket.remote_address if hasattr(websocket, 'remote_address') else "?"
        print(f"[QQ Bot] NapCatQQ 已连接 ({peer}), 共 {len(self._connections)} 个连接")

        try:
            async for raw in websocket:
                try:
                    await self._handle_event(websocket, raw)
                except Exception:
                    print(f"[QQ Bot] 事件处理异常:\n{traceback.format_exc()}")
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._connections.discard(websocket)
            print(f"[QQ Bot] NapCatQQ 断开, 剩余 {len(self._connections)} 个连接")

    # ================================================================
    # 事件派发
    # ================================================================

    async def _handle_event(self, websocket, raw_data):
        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")
        print(f"[QQ Bot] 收到: {str(raw_data)[:200]}", flush=True)
        try:
            event = json.loads(raw_data)
        except json.JSONDecodeError:
            return

        post_type = event.get("post_type", "")
        if post_type == "meta_event":
            await self._handle_meta(websocket, event)
            return
        if post_type != "message":
            return

        message_type = event.get("message_type", "")
        user_id = event.get("user_id", 0)
        group_id = event.get("group_id", 0)
        message = event.get("message", "")
        _raw_message = event.get("raw_message", "")

        text = self._extract_text(message)

        if message_type == "private":
            await self._handle_private(websocket, user_id, text)
        elif message_type == "group":
            await self._handle_group(websocket, group_id, user_id, text, message)

    async def _handle_meta(self, websocket, event):
        """处理元事件（心跳、生命周期）"""
        # 心跳不需要回复

    # ================================================================
    # 消息文本提取
    # ================================================================

    def _extract_text(self, message) -> str:
        """从 OneBot 消息中提取纯文本（支持字符串和数组格式）"""
        if isinstance(message, str):
            return message.strip()
        if isinstance(message, list):
            parts = []
            for seg in message:
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
            return "".join(parts).strip()
        return str(message).strip()

    def _is_at_bot(self, message) -> bool:
        """检测消息中是否 @了机器人（数组格式）"""
        if isinstance(message, list):
            return any(seg.get("type") == "at" for seg in message)
        return isinstance(message, str) and "[CQ:at," in message

    def _strip_at(self, text: str) -> str:
        """移除文本中的 @mention"""
        import re
        text = re.sub(r'\[CQ:at,qq=\d+\]', '', text)
        text = re.sub(r'@\S+', '', text)
        return text.strip()

    # ================================================================
    # 消息路由
    # ================================================================

    async def _handle_private(self, websocket, user_id: int, text: str):
        if not text:
            return
        if text.startswith("/"):
            await self._dispatch_command(websocket, user_id, None, text)
        else:
            await self._free_chat(websocket, user_id, None, text)

    async def _handle_group(self, websocket, group_id: int, user_id: int,
                            text: str, raw_message):
        # 如果配置了特定群，只响应该群
        if QQ_GROUP_ID and str(group_id) != str(QQ_GROUP_ID):
            return
        # 管理员发消息不需要 @，直接触发
        is_admin = str(user_id) in QQ_ADMIN_IDS
        if not is_admin and not self._is_at_bot(raw_message):
            return

        text = self._strip_at(text) if self._is_at_bot(raw_message) else text
        if not text:
            return
        if text.startswith("/"):
            await self._dispatch_command(websocket, user_id, group_id, text)
        else:
            await self._free_chat(websocket, user_id, group_id, text)

    # ================================================================
    # 交易命令分发
    # ================================================================

    async def _dispatch_command(self, websocket, user_id: int, group_id: int, text: str):
        parts = text[1:].strip().split(maxsplit=1)
        cmd = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""

        handlers = {
            "start":     self._cmd_start,
            "help":      self._cmd_help,
            "menu":      self._cmd_menu,
            "balance":   self._cmd_balance,
            "positions": self._cmd_positions,
            "pnl":       self._cmd_pnl,
            "stats":     self._cmd_stats,
            "close":     self._cmd_close,
            "autotrade": self._cmd_autotrade,
            "review":    self._cmd_review,
            "start_ai":  self._cmd_start_ai,
            "stop_ai":   self._cmd_stop_ai,
            "market":    self._cmd_market,
            "scan":      self._cmd_scan,
            "clear":     self._cmd_clear,
        }

        handler = handlers.get(cmd)
        if handler:
            await handler(websocket, user_id, group_id, args)
        else:
            await self._send_msg(websocket, user_id, group_id,
                                 f"未知指令 /{cmd}，发送 /menu 查看可用命令")

    # ================================================================
    # 自由对话 (Claude API)
    # ================================================================

    async def _free_chat(self, websocket, user_id: int, group_id: int, text: str):
        if not self.claude or not self.claude.health_check():
            await self._send_msg(websocket, user_id, group_id,
                                 "Claude API 未配置，请在 .env 中设置 ANTHROPIC_API_KEY")
            return

        conv_key = f"qq_{user_id}" if not group_id else f"qq_group{group_id}_{user_id}"

        # 存入用户消息
        self.conversation_mgr.add_message(conv_key, "user", text)

        # 构建上下文
        context = self.conversation_mgr.build_claude_context(conv_key)
        system_prompt = self.claude.get_system_prompt()

        print(f"[QQ Bot] Claude 请求: {text[:80]}... (历史 {len(context)} 条)")
        reply = self.claude.chat(context, system_prompt=system_prompt)

        # 存入助手回复
        self.conversation_mgr.add_message(conv_key, "assistant", reply)

        # 发回 QQ
        await self._send_msg(websocket, user_id, group_id, reply)

    # ================================================================
    # 命令处理
    # ================================================================

    async def _cmd_start(self, websocket, uid, gid, args):
        await self._send_msg(websocket, uid, gid,
                             "QQ 交易机器人已就绪\n\n"
                             "发送 /menu 查看功能菜单\n"
                             "发送 /help 查看帮助\n"
                             "直接发消息可以和我自由对话")

    async def _cmd_help(self, websocket, uid, gid, args):
        await self._send_msg(websocket, uid, gid,
                             "【帮助】\n\n"
                             "交易命令:\n"
                             "/balance - 查余额\n"
                             "/positions - 查持仓\n"
                             "/pnl - 查看今日盈亏\n"
                             "/stats - 交易统计\n"
                             "/close 币种 - 平仓\n"
                             "/autotrade - 自动交易状态\n"
                             "/review - AI每日复盘\n"
                             "/start_ai - 启动AI交易\n"
                             "/stop_ai - 停止AI交易\n"
                             "/market 币种 - AI看盘\n"
                             "/scan 币种 - 深度扫描\n"
                             "/menu - 功能菜单\n"
                             "/clear - 清除对话历史\n\n"
                             "自由对话: 直接发消息即可")

    async def _cmd_menu(self, websocket, uid, gid, args):
        await self._send_msg(websocket, uid, gid,
                             "【功能菜单】\n\n"
                             "💰 账户: /balance /positions /pnl\n"
                             "📊 统计: /stats /review\n"
                             "🤖 AI: /start_ai /stop_ai /market /scan\n"
                             "⚙️ 设置: /autotrade /close /clear\n"
                             "❓ /help")

    async def _cmd_balance(self, websocket, uid, gid, args):
        if not self.okx:
            return
        summary = self.okx.get_account_summary()
        await self._send_msg(websocket, uid, gid, summary)

    async def _cmd_positions(self, websocket, uid, gid, args):
        if not self.okx:
            return
        positions = self.okx.get_positions()
        if not positions:
            await self._send_msg(websocket, uid, gid, "当前无持仓")
            return
        lines = ["【当前持仓】"]
        for pos in positions:
            emoji = "🟢" if pos.get("upl", 0) >= 0 else "🔴"
            lines.append(
                f"\n{emoji} {pos['instId']} {pos['side'].upper()} "
                f"{pos['quantity']}张\n"
                f"  均价: {pos.get('avgPx', 0):.1f} | "
                f"标记: {pos.get('markPx', 0):.1f}\n"
                f"  浮动: {pos.get('upl', 0):.2f} USDT | "
                f"杠杆: {pos.get('lever', 0)}x"
            )
        await self._send_msg(websocket, uid, gid, "\n".join(lines))

    async def _cmd_pnl(self, websocket, uid, gid, args):
        if not self.logger:
            return
        pnl = self.logger.get_today_pnl()
        emoji = "🟢" if pnl >= 0 else "🔴"
        await self._send_msg(websocket, uid, gid, f"{emoji} 今日盈亏: {pnl:+.2f} USDT")

    async def _cmd_stats(self, websocket, uid, gid, args):
        if not self.logger:
            return
        stats = self.logger.get_trade_stats()
        text = (
            f"【交易统计】\n\n"
            f"总交易: {stats.get('total_trades', 0)}\n"
            f"胜: {stats.get('win_count', 0)} | 负: {stats.get('loss_count', 0)}\n"
            f"胜率: {stats.get('win_rate', 'N/A')}\n"
            f"累计盈亏: {stats.get('total_pnl', '0')}"
        )
        await self._send_msg(websocket, uid, gid, text)

    async def _cmd_close(self, websocket, uid, gid, args):
        if not self.okx:
            return
        symbol = args.strip().upper() if args else ""
        if not symbol:
            await self._send_msg(websocket, uid, gid, "请指定币种: /close BTC-USDT-SWAP")
            return
        if not symbol.endswith("-SWAP"):
            symbol += "-SWAP"
        ok, msg = self.okx.close_position(symbol)
        if ok:
            await self._send_msg(websocket, uid, gid, f"已提交平仓: {symbol}")
        else:
            await self._send_msg(websocket, uid, gid, f"平仓失败: {msg}")

    async def _cmd_autotrade(self, websocket, uid, gid, args):
        status = "已开启" if AUTO_TRADE else "已关闭（仅通知）"
        await self._send_msg(websocket, uid, gid, f"自动交易: {status}")

    async def _cmd_review(self, websocket, uid, gid, args):
        if not self.reviewer or not self.logger:
            await self._send_msg(websocket, uid, gid, "复盘模块未初始化")
            return
        trades = self.logger.get_today_trades()
        closed = [t for t in trades if len(t) > 9 and t[9] == "closed"]
        if not closed:
            await self._send_msg(websocket, uid, gid, "今天还没有已平仓的交易")
            return
        await self._send_msg(websocket, uid, gid, "正在调用 DeepSeek AI 做每日复盘...")
        trade_dicts = [self._trade_row_to_dict(t) for t in closed]
        report = self.reviewer.daily_review(trade_dicts)
        await self._send_msg(websocket, uid, gid, f"【DeepSeek 每日复盘】\n\n{report}")

    async def _cmd_start_ai(self, websocket, uid, gid, args):
        if not self.auto_trader:
            await self._send_msg(websocket, uid, gid, "AI 引擎未初始化")
            return
        if not DEEPSEEK_API_KEY:
            await self._send_msg(websocket, uid, gid, "请先配置 DEEPSEEK_API_KEY")
            return
        if self.auto_trader._running:
            await self._send_msg(websocket, uid, gid, "AI 引擎已在运行中")
            return
        self.auto_trader.start(self.send)
        await self._send_msg(websocket, uid, gid, "AI 自主交易引擎已启动\n每1H分析市场，自信度>=65时自动下单")

    async def _cmd_stop_ai(self, websocket, uid, gid, args):
        if not self.auto_trader:
            return
        self.auto_trader.stop()
        await self._send_msg(websocket, uid, gid, "AI 引擎已停止")

    async def _cmd_market(self, websocket, uid, gid, args):
        if not self.auto_trader:
            await self._send_msg(websocket, uid, gid, "AI 引擎未初始化")
            return
        symbol = args.strip() if args else "BTC-USDT-SWAP"
        await self._send_msg(websocket, uid, gid, f"正在分析 {symbol}...")
        report = self.auto_trader.market_report(symbol)
        await self._send_msg(websocket, uid, gid, f"【AI 盘面分析 [{symbol}]】\n\n{report}")

    async def _cmd_scan(self, websocket, uid, gid, args):
        if not self.auto_trader:
            await self._send_msg(websocket, uid, gid, "引擎未初始化")
            return
        symbol = args.strip() if args else "BTC-USDT-SWAP"
        await self._send_msg(websocket, uid, gid, f"深度扫描 {symbol}...")
        report = self.auto_trader._advanced_market_report(symbol)
        await self._send_msg(websocket, uid, gid, report)

    async def _cmd_clear(self, websocket, uid, gid, args):
        conv_key = f"qq_{uid}" if not gid else f"qq_group{gid}_{uid}"
        self.conversation_mgr.clear_conversation(conv_key)
        await self._send_msg(websocket, uid, gid, "对话历史已清除")

    # ================================================================
    # 消息发送
    # ================================================================

    async def _send_msg(self, websocket, user_id: int, group_id: int, text: str):
        """通过 OneBot API 发送消息"""
        if not text or not websocket:
            return

        action = {
            "action": "send_group_msg" if group_id else "send_private_msg",
            "params": {
                "user_id": user_id,
                "message": str(text),
            },
        }
        if group_id:
            action["params"]["group_id"] = group_id
        print(f"[QQ Bot] 发送: action={action['action']} user={user_id} text={text[:50]}", flush=True)

        # 长消息分段发送
        MAX_LEN = 2000
        if len(text) <= MAX_LEN:
            await self._send_action(websocket, action)
        else:
            chunks = self._split_long_msg(text, MAX_LEN)
            for chunk in chunks:
                action["params"]["message"] = chunk
                await self._send_action(websocket, action)

    async def _send_action(self, websocket, action: dict):
        try:
            await websocket.send(json.dumps(action, ensure_ascii=False))
        except Exception as e:
            print(f"[QQ Bot] 发送消息失败: {e}")

    def _split_long_msg(self, text: str, max_len: int) -> list:
        """将长文本按段落分割"""
        chunks = []
        current = ""
        for line in text.split("\n"):
            if len(current) + len(line) + 1 > max_len:
                if current:
                    chunks.append(current)
                current = line
            else:
                current = current + "\n" + line if current else line
        if current:
            chunks.append(current)
        return chunks or [text[:max_len]]

    # ================================================================
    # 主动推送通知（从 Webhook 流程调用）
    # ================================================================

    def send(self, text: str):
        """同步方式发送通知到管理员 QQ"""
        if not self._connections or not QQ_ADMIN_IDS:
            return
        admin_uid = int(QQ_ADMIN_IDS[0]) if QQ_ADMIN_IDS else 0
        if not admin_uid:
            return

        async def _do_send():
            ws = next(iter(self._connections), None)
            if ws:
                await self._send_msg(ws, admin_uid, None, text)

        try:
            loop = self._loop
            if loop and loop.is_running():
                asyncio.run_coroutine_threadsafe(_do_send(), loop)
        except Exception as e:
            print(f"[QQ Bot] 通知发送失败: {e}")

    def notify_signal(self, signal):
        emoji = "🟢" if signal.direction == "long" else "🔴"
        direction_cn = "做多" if signal.direction == "long" else "做空"
        auto_status = "自动执行" if AUTO_TRADE else "手动审核"
        msg = (
            f"{emoji} 交易信号来了\n\n"
            f"币种: {signal.okx_symbol}\n"
            f"方向: {direction_cn}\n"
            f"价格: ${signal.price:,.1f}\n"
        )
        if signal.stop_loss:
            msg += f"止损: ${signal.stop_loss:,.1f}\n"
        if signal.take_profit:
            msg += f"止盈: ${signal.take_profit:,.1f}\n"
        if signal.strategy:
            msg += f"策略: {signal.strategy}\n"
        if signal.interval:
            msg += f"周期: {signal.interval}\n"
        msg += f"\n模式: {auto_status}"
        self.send(msg)

    def notify_trade(self, signal, quantity: int, leverage: int, order_id: str):
        direction_cn = "做多" if signal.direction == "long" else "做空"
        msg = (
            f"已下单\n\n"
            f"{signal.okx_symbol} {direction_cn}\n"
            f"{quantity} 张 | {leverage}x 杠杆\n"
            f"入场: ${signal.price:,.1f}\n"
        )
        if signal.stop_loss:
            msg += f"止损: ${signal.stop_loss:,.1f}\n"
        if signal.take_profit:
            msg += f"止盈: ${signal.take_profit:,.1f}\n"
        msg += f"\n订单: {order_id}"
        self.send(msg)

    def notify_reject(self, signal, reason: str):
        msg = (
            f"交易被拦截\n\n"
            f"{signal.okx_symbol} {signal.direction.upper()}\n"
            f"${signal.price:,.1f}\n"
            f"原因: {reason}"
        )
        self.send(msg)

    def notify_close(self, symbol: str, side: str, entry: float, exit_price: float, pnl: float):
        emoji = "🟢" if pnl >= 0 else "🔴"
        msg = (
            f"{emoji} 仓位已平\n\n"
            f"{symbol} {side.upper()}\n"
            f"入场: ${entry:,.1f}\n"
            f"出场: ${exit_price:,.1f}\n"
            f"盈亏: {pnl:+.2f} USDT"
        )
        self.send(msg)

    def notify_error(self, title: str, detail: str):
        self.send(f"{title}\n{detail}")

    # ================================================================
    # 工具方法
    # ================================================================

    @staticmethod
    def _trade_row_to_dict(row) -> dict:
        cols = ["id", "signal_id", "time", "symbol", "direction", "entry_price",
                "quantity", "leverage", "stop_loss", "take_profit", "order_id",
                "status", "exit_price", "pnl", "close_time"]
        d = {cols[i]: row[i] for i in range(min(len(cols), len(row)))}
        for k in ("entry_price", "exit_price", "pnl", "stop_loss", "take_profit",
                  "quantity", "leverage"):
            if k in d and d[k] is not None:
                with contextlib.suppress(ValueError, TypeError):
                    d[k] = float(d[k])
        return d
