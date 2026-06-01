"""Cornix 信号发送器 — 通过 Telegram 频道发送格式化交易信号

Cornix Bot 监听 Telegram 频道中的信号消息, 自动在 Bybit 执行。
格式遵循 Cornix 标准解析规则。

信号格式:
  📊 NEW SIGNAL
  Pair: BTCUSDT
  Direction: LONG
  Entry: 87000
  Stop Loss: 86500
  Take Profit:
    87500
    88000
  Leverage: 10x
  Exchange: Bybit
"""

import httpx

from .config import CORNIX_CHANNEL_ID, TELEGRAM_BOT_TOKEN, BYBIT_DEMO, PROXY_URL
from .logging_ import get_logger
from ..core.models import ScalpDecision

logger = get_logger(__name__)


class CornixClient:
    """Cornix Telegram 信号发送客户端

    通过 Bot API 发送格式化信号到指定 Telegram 频道。
    Cornix Bot 加入该频道后自动读取并执行。
    """

    def __init__(self):
        self._channel_id = CORNIX_CHANNEL_ID
        self._bot_token = TELEGRAM_BOT_TOKEN
        self._exchange = "Bybit"

        limits = httpx.Limits(max_keepalive_connections=3, max_connections=5)
        transport_kwargs = {}
        if PROXY_URL:
            transport_kwargs["proxy"] = PROXY_URL
        self._client = httpx.Client(
            timeout=httpx.Timeout(15.0),
            limits=limits,
            transport=httpx.HTTPTransport(retries=3, **transport_kwargs),
        )

    def close(self):
        self._client.close()

    @property
    def configured(self) -> bool:
        return bool(self._channel_id and self._bot_token)

    # ── 信号发送 ──────────────────────────────────────

    def send_signal(self, decision: ScalpDecision) -> tuple[bool, str]:
        """发送交易信号到 Cornix 专用频道"""
        if not self.configured:
            return False, "Cornix 未配置 (CORNIX_CHANNEL_ID)"

        msg = self._format_signal(decision)
        return self._send_telegram(msg)

    def send_close(self, symbol: str) -> tuple[bool, str]:
        """发送平仓信号"""
        if not self.configured:
            return False, "Cornix 未配置"

        msg = f"🔴 CLOSE {symbol}\nExchange: {self._exchange}"
        return self._send_telegram(msg)

    # ── 格式化 ────────────────────────────────────────

    def _format_signal(self, d: ScalpDecision) -> str:
        """Cornix 信号卡 — Cornix 解析关键词需保留

        Cornix 识别: Coin / Direction / Entry / Targets / Stop Loss / Exchange
        """
        is_long = d.direction == "long"
        emoji = "🟢" if is_long else "🔴"
        direction = "LONG" if is_long else "SHORT"
        label = "做多" if is_long else "做空"
        arrow = "↗️" if is_long else "↘️"

        tp1 = d.take_profit
        mult_15 = 1 + d.tp_pct * 1.5 / 100 if is_long else 1 - d.tp_pct * 1.5 / 100
        mult_20 = 1 + d.tp_pct * 2.0 / 100 if is_long else 1 - d.tp_pct * 2.0 / 100
        tp2 = d.entry * mult_15
        tp3 = d.entry * mult_20

        return (
            f"{emoji} {label} {d.symbol} {arrow}\n\n"
            f"Entry: {d.entry:.4f}\n"
            f"🎯 TP1: {tp1:.4f}\n"
            f"🎯 TP2: {tp2:.4f}\n"
            f"🎯 TP3: {tp3:.4f}\n"
            f"🛑 SL:  {d.stop_loss:.4f}\n\n"
            f"Coin: {d.symbol}\n"
            f"Direction: {direction}\n"
            f"Targets: {tp1:.4f}, {tp2:.4f}, {tp3:.4f}\n"
            f"Stop Loss: {d.stop_loss:.4f}\n"
            f"Exchange: {self._exchange}\n"
            f"Leverage: 10x\n\n"
            f"⚡ {d.confidence}% 置信 | RR {d.net_risk_reward:.1f} | {d.scalping_strategy}"
        )

    def send_entry_filled(self, symbol: str, direction: str, entry_price: float) -> tuple[bool, str]:
        """入场成交通知"""
        is_long = direction == "long"
        emoji = "🟢" if is_long else "🔴"
        label = "做多" if is_long else "做空"
        msg = f"{emoji} 已入场 {symbol} {label}\n\nEntry: {entry_price:.4f}\n\n✅ 订单已成交 | 追踪止盈/止损已激活"
        return self._send_telegram(msg)

    def send_tp_hit(self, symbol: str, tp_num: int, price: float) -> tuple[bool, str]:
        """止盈达成通知"""
        emojis = {1: "1️⃣", 2: "2️⃣", 3: "3️⃣"}
        msg = f"🎯 TP{tp_num} 达成! {emojis.get(tp_num, '✅')}\n\n{symbol}\n价格: {price:.4f}\n✅ 部分仓位已平仓"
        return self._send_telegram(msg)

    def send_sl_hit(self, symbol: str, price: float, pnl_pct: float) -> tuple[bool, str]:
        """止损触发通知"""
        msg = f"🛑 止损触发 {symbol}\n\n价格: {price:.4f}\n亏损: {pnl_pct:+.3f}%"
        return self._send_telegram(msg)

    def send_tp_update(self, symbol: str, tp1_hit: bool, tp2_hit: bool, tp3_hit: bool, current_price: float) -> tuple[bool, str]:
        """持仓状态更新"""
        status = []
        status.append(("🎯 TP1: " + ("✅ 已达成" if tp1_hit else "⏳ 等待中")))
        status.append(("🎯 TP2: " + ("✅ 已达成" if tp2_hit else "⏳ 等待中")))
        status.append(("🎯 TP3: " + ("✅ 已达成" if tp3_hit else "⏳ 等待中")))
        msg = f"📊 {symbol} 持仓状态\n\n当前价: {current_price:.4f}\n\n" + "\n".join(status)
        return self._send_telegram(msg)

    def _send_telegram(self, text: str) -> tuple[bool, str]:
        """通过 Telegram Bot API 发送消息到 Cornix 频道"""
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        payload = {
            "chat_id": self._channel_id,
            "text": text,
            "parse_mode": "",
            "disable_web_page_preview": True,
        }
        try:
            resp = self._client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    logger.info("Cornix 信号已发送")
                    return True, "已发送"
            logger.error("Cornix Telegram 错误: %s %s", resp.status_code, resp.text[:200])
            return False, f"Telegram HTTP {resp.status_code}"
        except Exception as e:
            logger.error("Cornix 发送失败: %s", str(e))
            return False, str(e)
