"""风控服务 — 剥头皮前置检查

核心规则:
1. 日累计亏损 > MAX_DAILY_LOSS_USDT → 暂停
2. 日交易次数 > MAX_DAILY_TRADES → 暂停
3. 单笔亏损 > MAX_LOSS_PER_TRADE_USDT → 拒绝
4. 黑天鹅 → 暂停 30 分钟
5. 连续亏损 > 3 → 暂停 1 小时
6. 同币种冷却
"""

import time
from collections import defaultdict
from datetime import datetime, timezone

from ..core.models import ScalpDecision
from ..infrastructure.config import (
    MAX_DAILY_LOSS_USDT, MAX_DAILY_TRADES, MAX_LOSS_PER_TRADE_USDT,
    MIN_AI_CONFIDENCE,
)
from ..infrastructure.logging_ import get_logger

logger = get_logger(__name__)


class RiskService:
    """
    剥头皮风控引擎

    状态追踪:
    - daily_pnl_usdt: 当日累计盈亏
    - daily_trade_count: 当日交易次数
    - consecutive_losses: 连亏次数
    - blackout_until: 暂停截止时间戳
    - symbol_cooldown: 币种冷却截止时间
    """

    def __init__(self):
        self.daily_pnl_usdt = 0.0
        self.daily_trade_count = 0
        self.consecutive_losses = 0
        self.blackout_until: float = 0.0
        self.symbol_cooldown: dict[str, float] = {}
        self.symbol_daily_losses: dict[str, int] = defaultdict(int)
        self._last_reset_date = datetime.now(timezone.utc).date()

    def _maybe_reset_daily(self):
        """跨天重置"""
        today = datetime.now(timezone.utc).date()
        if today != self._last_reset_date:
            logger.info("新交易日, 重置风控状态")
            self.daily_pnl_usdt = 0.0
            self.daily_trade_count = 0
            self.consecutive_losses = 0
            self.symbol_daily_losses.clear()
            self._last_reset_date = today

    # ── 前置检查 ──────────────────────────────────────

    def pre_trade_check(self, decision: ScalpDecision) -> tuple[bool, str]:
        """
        下单前完整风控检查
        返回: (通过, 原因)
        """
        self._maybe_reset_daily()

        # 1. 黑天鹅暂停
        if self.blackout_until > time.time():
            remain = int(self.blackout_until - time.time())
            return False, f"全局暂停中, 剩余 {remain}s"

        # 2. 连亏暂停
        if self.consecutive_losses >= 3:
            return False, f"连亏 {self.consecutive_losses} 次, 暂停 1 小时"

        # 3. 日亏损上限
        if abs(self.daily_pnl_usdt) >= MAX_DAILY_LOSS_USDT and self.daily_pnl_usdt < 0:
            return False, f"日亏损已达上限 ({abs(self.daily_pnl_usdt):.1f} ≥ {MAX_DAILY_LOSS_USDT})"

        # 4. 日交易次数
        if self.daily_trade_count >= MAX_DAILY_TRADES:
            return False, f"已达日交易上限 {MAX_DAILY_TRADES}"

        # 5. AI 置信度
        if decision.confidence < MIN_AI_CONFIDENCE:
            return False, f"AI 置信度 {decision.confidence} < {MIN_AI_CONFIDENCE}"

        # 6. 单笔最大亏损
        if decision.risk_usdt > MAX_LOSS_PER_TRADE_USDT:
            return False, f"单笔风险 {decision.risk_usdt:.2f} > {MAX_LOSS_PER_TRADE_USDT}"

        # 7. 动作验证
        if decision.action == "WAIT":
            return False, f"AI 决策为观望: {decision.invalid_condition or decision.reason}"

        # 8. 币种冷却
        sym = decision.symbol
        if sym in self.symbol_cooldown and self.symbol_cooldown[sym] > time.time():
            remain = int(self.symbol_cooldown[sym] - time.time())
            return False, f"{sym} 冷却中, 剩余 {remain}s"

        return True, "OK"

    # ── 事后更新 ──────────────────────────────────────

    def on_trade_open(self, decision: ScalpDecision):
        """交易已开仓"""
        self.daily_trade_count += 1
        logger.info(
            "开仓: %s %s | 日交易 #%s | 风险 $%.2f",
            decision.symbol, decision.direction,
            self.daily_trade_count, decision.risk_usdt,
        )

    def on_trade_close(self, symbol: str, pnl_usdt: float):
        """交易已平仓"""
        self.daily_pnl_usdt += pnl_usdt

        if pnl_usdt < 0:
            self.consecutive_losses += 1
            self.symbol_daily_losses[symbol] += 1
            # 同币种当日亏损 2 次 → 冷却 2 小时
            if self.symbol_daily_losses[symbol] >= 2:
                self.symbol_cooldown[symbol] = time.time() + 7200
                logger.warning("%s 当日亏损 %s 次, 冷却 2h", symbol, self.symbol_daily_losses[symbol])
            # 连亏 3 次 → 暂停 1 小时
            if self.consecutive_losses >= 3:
                self.blackout_until = time.time() + 3600
                logger.warning("连亏 %s 次, 全局暂停 1h", self.consecutive_losses)
        else:
            self.consecutive_losses = 0
            self.symbol_daily_losses[symbol] = max(0, self.symbol_daily_losses[symbol] - 1)

        logger.info(
            "平仓: %s | PnL $%.2f | 累计日盈亏 $%.2f | 连亏 %s",
            symbol, pnl_usdt, self.daily_pnl_usdt, self.consecutive_losses,
        )

    def on_blackswan(self):
        """黑天鹅事件触发"""
        self.blackout_until = time.time() + 1800  # 暂停 30 分钟
        logger.warning("黑天鹅触发! 暂停交易 30 分钟")

    # ── 状态查询 ──────────────────────────────────────

    def status_report(self) -> str:
        """风控状态文本"""
        self._maybe_reset_daily()
        lines = [
            f"📊 风控状态",
            f"日交易: {self.daily_trade_count}/{MAX_DAILY_TRADES}",
            f"日盈亏: ${self.daily_pnl_usdt:+.2f} (上限 -${MAX_DAILY_LOSS_USDT})",
            f"连亏: {self.consecutive_losses}/3",
            f"暂停: {'是' if self.blackout_until > time.time() else '否'}",
        ]
        return "\n".join(lines)
