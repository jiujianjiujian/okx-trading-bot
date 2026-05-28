"""
风控管理器 - 仓位计算、风险检查
"""

from config import (
    RISK_PER_TRADE,
    MAX_POSITIONS,
    MAX_DAILY_LOSS,
)


class RiskManager:
    """负责所有风控相关的计算和检查"""

    def __init__(self):
        self.daily_pnl = 0.0       # 当日已实现盈亏
        self.trade_count = 0       # 当日交易次数

    # ----------------------------------------------------------------
    # 仓位计算
    # ----------------------------------------------------------------

    def calculate_position_size(
        self, balance: float, entry_price: float, stop_loss: float
    ) -> tuple[int, float]:
        """
        基于风险比例计算开仓数量

        参数:
            balance:     账户 USDT 余额
            entry_price: 入场价
            stop_loss:   止损价

        返回:
            (合约张数, 名义价值)

        计算逻辑:
            风险金额 = 余额 × 风险比例
            每张风险 = |入场价 - 止损价| × 每张面值
            张数 = 风险金额 / 每张风险
        """
        risk_amount = balance * (RISK_PER_TRADE / 100)
        per_contract_risk = abs(entry_price - stop_loss)

        # BTC 合约: 1张=0.001 BTC, ETH 合约: 1张=0.01 ETH
        # 简化处理：per_contract_risk 是每张的风险（按价格差×面值）
        # 这里 per_contract_risk 已经是价格差，需要换算
        # 对于 BTC-USDT-SWAP, 面值=0.001 BTC, 每张风险 = 价格差 × 0.001
        contract_value = 0.001  # 默认 BTC 合约面值

        per_contract_risk_usdt = per_contract_risk * contract_value

        if per_contract_risk_usdt <= 0:
            return 0, 0.0

        contracts = int(risk_amount / per_contract_risk_usdt)
        notional = contracts * contract_value * entry_price

        return max(contracts, 1), notional

    # ----------------------------------------------------------------
    # 风险检查
    # ----------------------------------------------------------------

    def check_max_positions(self, current_positions: int) -> tuple[bool, str]:
        """检查是否超过最大持仓数"""
        if current_positions >= MAX_POSITIONS:
            return False, f"已达最大持仓数 {MAX_POSITIONS}，拒绝新开仓"
        return True, ""

    def check_daily_loss(self, account_balance: float) -> tuple[bool, str]:
        """检查当日亏损是否超过上限"""
        if account_balance <= 0:
            return False, "账户余额异常"

        loss_pct = abs(self.daily_pnl) / account_balance * 100

        if loss_pct >= MAX_DAILY_LOSS:
            return False, f"当日亏损 {loss_pct:.1f}% 已达上限 {MAX_DAILY_LOSS}%，停止交易"
        return True, ""

    def validate_signal(self, signal, balance: float, positions: int) -> tuple[bool, str]:
        """综合信号验证——前置风控"""
        # 1. 检查价格
        if signal.price <= 0:
            return False, "信号价格无效"

        # 2. 检查止损（必须有止损）
        if signal.stop_loss <= 0:
            return False, "信号未包含止损价，拒绝交易（必须设置止损）"

        # 3. 止损方向检查
        if signal.direction == "long" and signal.stop_loss >= signal.price:
            return False, f"做多信号止损价 {signal.stop_loss} >= 入场价 {signal.price}"
        if signal.direction == "short" and signal.stop_loss <= signal.price:
            return False, f"做空信号止损价 {signal.stop_loss} <= 入场价 {signal.price}"

        # 4. 最大持仓检查
        ok, msg = self.check_max_positions(positions)
        if not ok:
            return False, msg

        # 5. 日内亏损检查
        ok, msg = self.check_daily_loss(balance)
        if not ok:
            return False, msg

        return True, "风控通过"

    def reset_daily(self):
        """每日重置"""
        self.daily_pnl = 0.0
        self.trade_count = 0
