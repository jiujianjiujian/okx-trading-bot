"""剥头皮参数计算服务

根据 AI 决策和市场条件计算精确的止损/止盈距离、仓位大小、手续费。
目标: 稳定盈利、亏损小、日净利 50 USDT。
"""

from ..core.models import ScalpDecision, IndicatorBundle
from ..infrastructure.config import (
    ESTIMATED_FEE_PCT, SCALP_SL_PCT_MIN, SCALP_SL_PCT_MAX,
    SCALP_TP_PCT_MIN, SCALP_TP_PCT_MAX, MIN_NET_RR,
    MAX_LOSS_PER_TRADE_USDT, MAX_POSITIONS,
    FEE_TAKER_PCT, FEE_MAKER_PCT,
)
from ..infrastructure.logging_ import get_logger

logger = get_logger(__name__)


class ScalpingService:
    """
    剥头皮参数引擎

    核心原则:
    - 止损 ≤ 0.50%，止盈 ≥ 0.30%
    - 净盈亏比 (扣手续费后) ≥ 2.0
    - 单笔最大亏损 ≤ 5 USDT
    - 用 ATR 自适应 SL 距离
    """

    def __init__(self):
        pass

    def calculate_parameters(
        self,
        decision: ScalpDecision,
        bundle: IndicatorBundle | None = None,
        account_balance: float = 1000.0,
        current_positions: int = 0,
    ) -> ScalpDecision:
        """
        计算完整的剥头皮参数: SL/TP 距离, 仓位大小, 手续费, 预期盈亏

        基于 ATR 自适应, 高波动 → 宽止损, 低波动 → 窄止损
        """
        if current_positions >= MAX_POSITIONS:
            decision.action = "WAIT"
            decision.invalid_condition = f"持仓已达上限 {MAX_POSITIONS}"
            return decision

        # ── 用 ATR 自适应 SL 距离 ──
        atr_pct = 0.3  # 默认 0.3%
        if bundle and bundle.atr > 0:
            entry = decision.entry
            if entry > 0:
                atr_pct = (bundle.atr / entry) * 100

        # SL: ATR × 1.5，约束在 [SCALP_SL_PCT_MIN, SCALP_SL_PCT_MAX]
        sl_pct = max(SCALP_SL_PCT_MIN, min(SCALP_SL_PCT_MAX, atr_pct * 1.5))
        decision.sl_pct = round(sl_pct, 3)

        # TP: 确保净盈亏比 ≥ MIN_NET_RR
        # 扣费后盈亏比公式: (tp% - fee%) / (sl% + fee%) ≥ MIN_NET_RR
        fee_pct = ESTIMATED_FEE_PCT
        min_tp = (sl_pct + fee_pct) * MIN_NET_RR + fee_pct
        tp_pct = max(SCALP_TP_PCT_MIN, min(min_tp, SCALP_TP_PCT_MAX))
        decision.tp_pct = round(tp_pct, 3)

        # ── 价格计算 ──
        entry = decision.entry
        if decision.direction == "long":
            decision.stop_loss = round(entry * (1 - sl_pct / 100), 4)
            decision.take_profit = round(entry * (1 + tp_pct / 100), 4)
        else:
            decision.stop_loss = round(entry * (1 + sl_pct / 100), 4)
            decision.take_profit = round(entry * (1 - tp_pct / 100), 4)

        # ── 费用 ──
        decision.fee_cost = round(fee_pct, 3)

        # 盈亏比
        if sl_pct > 0:
            decision.risk_reward = round(tp_pct / sl_pct, 2)
            # 净盈亏比 (扣手续费)
            net_loss = sl_pct + fee_pct
            net_profit = tp_pct - fee_pct
            decision.net_risk_reward = round(net_profit / net_loss, 2) if net_loss > 0 else 0

        # ── 仓位计算 ──
        # 单笔最大亏损 = MAX_LOSS_PER_TRADE_USDT
        # position_size = MAX_LOSS / sl_pct * 100
        # 但有每日目标约束：不需要过大的仓位
        if sl_pct > 0:
            risk_per_unit = sl_pct / 100
            max_size_by_loss = MAX_LOSS_PER_TRADE_USDT / risk_per_unit if risk_per_unit > 0 else 100
        else:
            max_size_by_loss = 100

        # 保守仓位: 目标日盈 50 USDT, 期望每笔盈利 ~3 USDT
        # 按 tp_pct 反推
        profit_per_unit = tp_pct / 100 if tp_pct > 0 else 0.003
        conservative_size = 3.0 / profit_per_unit if profit_per_unit > 0 else 100

        decision.position_size = round(min(max_size_by_loss, conservative_size, account_balance * 0.1), 2)

        # ── 预期盈亏 ──
        decision.risk_usdt = round(decision.position_size * sl_pct / 100, 2)
        gross_profit = decision.position_size * tp_pct / 100
        fee_cost_usdt = decision.position_size * fee_pct / 100
        decision.expected_profit_usdt = round(gross_profit - fee_cost_usdt, 2)

        # ── 风控校验 ──
        if decision.net_risk_reward < MIN_NET_RR:
            decision.action = "WAIT"
            decision.invalid_condition = (
                f"净盈亏比 {decision.net_risk_reward:.1f} < {MIN_NET_RR:.1f} "
                f"(扣费 {fee_pct:.2f}% 后)"
            )

        if decision.expected_profit_usdt <= 0:
            decision.action = "WAIT"
            decision.invalid_condition = f"预期净盈利 {decision.expected_profit_usdt:.2f} USDT ≤ 0 (手续费过高)"

        logger.info(
            "剥头皮参数: %s %s | SL %.2f%% TP %.2f%% | RR %.1f (净%.1f) | "
            "仓位 $%.0f | 预期 $%.2f | 最大亏损 $%.2f",
            decision.symbol, decision.direction,
            decision.sl_pct, decision.tp_pct,
            decision.risk_reward, decision.net_risk_reward,
            decision.position_size, decision.expected_profit_usdt,
            decision.risk_usdt,
        )

        return decision

    @staticmethod
    def fee_estimate(position_usdt: float, is_taker: bool = True) -> float:
        """估算单边手续费"""
        rate = FEE_TAKER_PCT if is_taker else FEE_MAKER_PCT
        return position_usdt * rate / 100

    @staticmethod
    def min_tp_for_breakeven(sl_pct: float, fee_pct: float | None = None) -> float:
        """
        计算盈亏平衡所需的最小 TP 距离

        双向手续费: tp% = sl% + 2 × fee% (入场+出场)
        例: sl=0.30%, fee=0.11% → tp=0.52%
        """
        fee = fee_pct if fee_pct is not None else ESTIMATED_FEE_PCT
        return sl_pct + 2 * fee
