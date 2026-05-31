"""3Commas Signal Bot Webhook 发送器

向 3Commas 发送交易信号，由 3Commas 在 Bybit 上执行。
支持自定义信号，控制追踪止盈/追踪止损参数。

参考: https://github.com/3commas-io/3commas-official-api-docs
"""

import json
import hashlib
import hmac
import time

import httpx

from .config import (
    THREECOMMAS_BOT_ID,
    THREECOMMAS_EMAIL_TOKEN,
    PROXY_URL,
)
from .logging_ import get_logger
from ..core.models import ScalpDecision

logger = get_logger(__name__)

# 3Commas Signal Futures Webhook URL
SIGNAL_URL = "https://3commas.io/signals/futures/webhook"


class ThreeCommasClient:
    """3Commas 信号发送客户端"""

    def __init__(self):
        self._bot_id = THREECOMMAS_BOT_ID
        self._email_token = THREECOMMAS_EMAIL_TOKEN
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
        return bool(self._bot_id and self._email_token)

    def send_signal(self, decision: ScalpDecision) -> tuple[bool, str]:
        """
        发送交易信号到 3Commas Signal Bot

        3Commas Futures Signal Bot 格式:
        {
          "message_type": "bot",
          "bot_id": 12345,
          "email_token": "xxx",
          "delay_seconds": 0,
          "pair": "USDT_BTC"
        }

        返回: (成功, 消息)
        """
        if not self.configured:
            return False, "3Commas 未配置 (THREECOMMAS_BOT_ID / THREECOMMAS_EMAIL_TOKEN)"

        # 3Commas pair 格式: USDT_BTC (不是 BTCUSDT)
        pair = f"USDT_{decision.symbol.replace('USDT', '')}"

        payload = {
            "message_type": "bot",
            "bot_id": self._bot_id,
            "email_token": self._email_token,
            "delay_seconds": 0,
            "pair": pair,
        }

        # 附注: 通过 comment 传递完整交易参数
        comment = (
            f"direction={decision.direction} "
            f"entry={decision.entry:.2f} "
            f"sl={decision.stop_loss:.4f} "
            f"tp={decision.take_profit:.4f} "
            f"sl%={decision.sl_pct:.2f}% "
            f"tp%={decision.tp_pct:.2f}% "
            f"rr={decision.net_risk_reward:.1f} "
            f"conf={decision.confidence} "
            f"lev=10x 逐仓 "
            f"strat={decision.scalping_strategy} "
            f"fee={decision.fee_cost:.3f}%"
        )
        payload["comment"] = comment[:500]

        try:
            resp = self._client.post(
                SIGNAL_URL,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201, 202):
                logger.info(
                    "3Commas 信号已发送: %s %s | 入场 %.2f | TP %.4f SL %.4f | RR %.1f",
                    decision.symbol, decision.direction,
                    decision.entry, decision.take_profit, decision.stop_loss,
                    decision.net_risk_reward,
                )
                return True, f"信号已发送 (HTTP {resp.status_code})"
            else:
                logger.error("3Commas 返回错误: %s %s", resp.status_code, resp.text[:300])
                return False, f"3Commas HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            logger.error("3Commas 请求异常: %s", str(e))
            return False, str(e)

    def send_exit_signal(self, symbol: str) -> tuple[bool, str]:
        """发送平仓信号"""
        if not self.configured:
            return False, "3Commas 未配置"

        pair = f"USDT_{symbol.replace('USDT', '')}"
        payload = {
            "message_type": "bot",
            "bot_id": self._bot_id,
            "email_token": self._email_token,
            "delay_seconds": 0,
            "pair": pair,
            "action": "close",
        }

        try:
            resp = self._client.post(SIGNAL_URL, json=payload, headers={"Content-Type": "application/json"})
            ok = resp.status_code in (200, 201, 202)
            return ok, f"HTTP {resp.status_code}" if ok else f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            return False, str(e)

    def send_smart_trade(self, decision: ScalpDecision, account_id: int,
                         api_key: str, api_secret: str) -> tuple[bool, str]:
        """
        通过 3Commas Smart Trade API 创建带追踪止盈/止损的智能交易

        需要 3Commas API Key, 而非 Signal Bot Token。
        POST /public/api/ver1/smart_trades_v2
        """
        pair = f"USDT_{decision.symbol.replace('USDT', '')}"
        order_type = "buy" if decision.direction == "long" else "sell"
        position_size = max(decision.position_size, 50)  # min $50

        body = {
            "account_id": account_id,
            "pair": pair,
            "position": {
                "type": order_type,
                "units": {"value": str(position_size), "type": "quote"},
                "order_type": "market",
                "take_profit": {
                    "enabled": True,
                    "profit_price": decision.tp_pct,
                    "trailing": {"enabled": False, "deviation": 0},
                },
                "stop_loss": {
                    "enabled": True,
                    "loss_price": decision.sl_pct,
                    "trailing": {"enabled": True, "deviation": 0.1},
                },
            },
        }

        url = "https://api.3commas.io/public/api/ver1/smart_trades_v2"
        try:
            resp = self._client.post(url, json=body, headers={
                "APIKEY": api_key,
                "Signature": hmac.new(
                    api_secret.encode(), url.encode(), hashlib.SHA256,
                ).hexdigest(),
                "Content-Type": "application/json",
            })
            ok = resp.status_code in (200, 201)
            return ok, resp.text[:300]
        except Exception as e:
            return False, str(e)
