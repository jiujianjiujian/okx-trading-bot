"""3Commas Signal Bot Webhook 发送器

向 3Commas DCA Bot 发送 TradingView 格式的 Webhook 信号。
3Commas 在 Bybit 上以限价单执行，按保证金百分比控制仓位。

信号格式 (TradingView → 3Commas):
  - 多头: action=enter_long, limit buy
  - 空头: action=enter_short, limit sell
  - 平仓: action=exit (由 3Commas 自带 TP/SL 管理)
"""

import time

import httpx

from .config import (
    THREECOMMAS_SECRET,
    THREECOMMAS_BOT_UUID,
    THREECOMMAS_WEBHOOK_URL,
    THREECOMMAS_EXCHANGE,
    PROXY_URL,
    BYBIT_DEMO,
)
from .logging_ import get_logger
from ..core.models import ScalpDecision

logger = get_logger(__name__)

# 默认保证金百分比范围
MARGIN_PCT_MIN = 1.0   # 最低 1%
MARGIN_PCT_MAX = 15.0  # 最高 15%
MAX_LAG_SECONDS = 300   # 信号最大延迟


class ThreeCommasClient:
    """3Commas TradingView Webhook 信号发送器

    使用 3Commas DCA Bot 原生 Webhook 格式:
    - JWT secret 认证
    - limit order + margin_percent
    - 支持 enter_long / enter_short / exit
    """

    def __init__(self):
        self._secret = THREECOMMAS_SECRET
        self._bot_uuid = THREECOMMAS_BOT_UUID
        self._webhook_url = THREECOMMAS_WEBHOOK_URL
        self._exchange = "Bybit" if not BYBIT_DEMO else "Bybit"

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
        return bool(self._secret and self._bot_uuid and self._webhook_url)

    # ── 主接口 ──────────────────────────────────────────

    def send_signal(self, decision: ScalpDecision) -> tuple[bool, str]:
        """
        发送剥头皮决策到 3Commas

        Args:
            decision: 包含完整 SL/TP/仓位参数的 ScalpDecision

        Returns:
            (成功, 消息)
        """
        if not self.configured:
            return False, "3Commas 未配置 (THREECOMMAS_SECRET / BOT_UUID / WEBHOOK_URL)"

        # 计算保证金百分比
        margin_pct = self._calc_margin_pct(decision)

        # 构建 TradingView Webhook 信号
        payload = self._build_payload(decision, margin_pct)

        try:
            resp = self._client.post(
                self._webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code in (200, 201, 202):
                logger.info(
                    "3Commas 已发送: %s %s | 入场 %.4f (限价) | "
                    "SL %.2f%% TP %.2f%% | 保证金 %.1f%% | RR %.1f",
                    decision.symbol,
                    "多" if decision.direction == "long" else "空",
                    decision.entry,
                    decision.sl_pct, decision.tp_pct,
                    margin_pct, decision.net_risk_reward,
                )
                return True, f"已发送 ({decision.direction} {decision.symbol})"
            else:
                logger.error("3Commas HTTP %s: %s", resp.status_code, resp.text[:300])
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:
            logger.error("3Commas 连接失败: %s", str(e))
            return False, str(e)

    def send_close(self, symbol: str) -> tuple[bool, str]:
        """发送平仓信号"""
        if not self.configured:
            return False, "3Commas 未配置"

        payload = {
            "secret": self._secret,
            "max_lag": str(MAX_LAG_SECONDS),
            "timestamp": str(int(time.time() * 1000)),
            "tv_exchange": self._exchange,
            "tv_instrument": symbol,
            "action": "exit",
            "bot_uuid": self._bot_uuid,
        }

        try:
            resp = self._client.post(
                self._webhook_url, json=payload,
                headers={"Content-Type": "application/json"},
            )
            ok = resp.status_code in (200, 201, 202)
            return ok, f"HTTP {resp.status_code}"
        except Exception as e:
            return False, str(e)

    # ── 内部 ────────────────────────────────────────────

    def _build_payload(self, d: ScalpDecision, margin_pct: float) -> dict:
        """构建 TradingView 信号 JSON"""
        now_ms = str(int(time.time() * 1000))
        # 限价单入场价 — 使用决策中的 entry 价格
        limit_price = f"{d.entry:.4f}"

        return {
            "secret": self._secret,
            "max_lag": str(MAX_LAG_SECONDS),
            "timestamp": now_ms,
            "trigger_price": f"{d.entry:.4f}",
            "tv_exchange": self._exchange,
            "tv_instrument": d.symbol,
            "action": "enter_long" if d.direction == "long" else "enter_short",
            "bot_uuid": self._bot_uuid,
            "order": {
                "amount": f"{margin_pct:.1f}",
                "currency_type": "margin_percent",
                "order_type": "limit",
                "price": limit_price,
            },
        }

    def _calc_margin_pct(self, d: ScalpDecision) -> float:
        """根据风险和账户计算合理的保证金百分比

        保证金百分比 = 所需保证金 / 可用余额 × 100

        原则:
        - 置信度越高 → 保证金越接近 MARGIN_PCT_MAX
        - 置信度越低 → 保证金越接近 MARGIN_PCT_MIN
        - 符合单笔最大亏损限制
        """
        # 基于置信度线性插值
        conf_ratio = max(0, min(1, d.confidence / 100.0))
        margin_pct = MARGIN_PCT_MIN + (MARGIN_PCT_MAX - MARGIN_PCT_MIN) * conf_ratio

        # 如果已计算仓位大小，用它反推保证金百分比
        if d.position_size > 0:
            # 10x 杠杆: position_size = margin × 10
            # margin_pct = (position_size / 10) / total_equity × 100
            # 用 position_size 反推一个合理的百分比
            estimated_equity = max(500, d.position_size * 2)  # 保守估计
            derived_pct = (d.position_size / 10) / estimated_equity * 100
            # 在范围内取合理值
            margin_pct = max(MARGIN_PCT_MIN, min(MARGIN_PCT_MAX, derived_pct))

        return round(margin_pct, 1)
