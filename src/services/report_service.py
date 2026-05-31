"""消息格式化服务 — 提供 Bot 层统一模板，替代重复的字符串拼接"""

from ..core.models import TradeSignal, ScalpDecision, PnLRecord, DailyStats


class ReportService:
    """纯消息模板 — 无副作用，可直接测试"""

    @staticmethod
    def signal_header(signal: TradeSignal) -> str:
        return (
            f"📡 *交易信号*\n"
            f"币种: `{signal.bybit_symbol}`\n"
            f"方向: {'🔴 做空' if signal.direction == 'short' else '🟢 做多'}\n"
            f"策略: {signal.strategy} | {signal.interval}"
        )

    @staticmethod
    def scalp_decision_preview(dec: ScalpDecision) -> str:
        """发送给 3Commas 前的决策预览"""
        emoji = "🟢" if dec.direction == "long" else "🔴"
        return (
            f"{emoji} *AI 剥头皮决策*\n"
            f"币种: `{dec.symbol}` | {dec.mode} | {dec.scalping_strategy}\n"
            f"入场: ${dec.entry:.4f}\n"
            f"止损: ${dec.stop_loss:.4f} (‑{dec.sl_pct:.2f}%)\n"
            f"止盈: ${dec.take_profit:.4f} (+{dec.tp_pct:.2f}%)\n"
            f"盈亏比: {dec.risk_reward:.1f} / 净 {dec.net_risk_reward:.1f} (扣费 {dec.fee_cost:.2f}%)\n"
            f"仓位: ${dec.position_size:.0f} | 风险: ${dec.risk_usdt:.2f}\n"
            f"预期: ${dec.expected_profit_usdt:+.2f} | 信心: {dec.confidence}%\n"
            f"理由: {dec.reason[:200]}"
        )

    @staticmethod
    def trade_opened(dec: ScalpDecision, signal_id: int) -> str:
        emoji = "🟢" if dec.direction == "long" else "🔴"
        return (
            f"{emoji} *交易已发送 3Commas* #{signal_id}\n"
            f"`{dec.symbol}` {dec.direction.upper()} "
            f"| SL ‑{dec.sl_pct:.2f}% | TP +{dec.tp_pct:.2f}% | RR {dec.net_risk_reward:.1f}\n"
            f"预期: ${dec.expected_profit_usdt:+.2f} | 仓位: ${dec.position_size:.0f}"
        )

    @staticmethod
    def trade_closed(rec: PnLRecord) -> str:
        """平仓通知"""
        emoji = "✅" if rec.pnl_usdt >= 0 else "❌"
        return (
            f"{emoji} *交易平仓*\n"
            f"`{rec.symbol}` {rec.direction.upper()} | {rec.closed_by.upper()}\n"
            f"入场 ${rec.entry:.4f} → 出场 ${rec.exit_price:.4f}\n"
            f"盈亏: ${rec.pnl_usdt:+.2f} ({rec.pnl_pct:+.3f}%)\n"
            f"手续费: ${rec.fee_paid:.2f} | 仓位: ${rec.position_size:.0f}"
        )

    @staticmethod
    def daily_stats(stats: DailyStats) -> str:
        """每日统计推送"""
        target_emoji = "🎯" if stats.net_pnl_usdt >= 50 else "⏳"
        return (
            f"{target_emoji} *每日统计* {stats.date}\n"
            f"交易: {stats.total_trades} 笔 | 胜率: {stats.win_rate:.0f}%\n"
            f"盈亏: ${stats.total_pnl_usdt:+.2f}\n"
            f"手续费: ‑${stats.total_fees_usdt:.2f}\n"
            f"*净利润: ${stats.net_pnl_usdt:+.2f}* | 目标: $50\n"
            f"最佳: ${stats.best_trade:+.2f} | 最差: ${stats.worst_trade:+.2f} | 均RR: {stats.avg_rr:.1f}"
        )

    @staticmethod
    def reject_notice(symbol: str, reason: str) -> str:
        return f"🚫 `{symbol}` 拦截: {reason}"

    @staticmethod
    def error_notice(title: str, detail: str) -> str:
        return f"⚠️ *{title}*\n{detail[:500]}"

    @staticmethod
    def blackswan_warning(change_pct: float) -> str:
        return f"🌪 *黑天鹅警告* BTC 快速下跌 {abs(change_pct):.1f}%，暂停交易 30 分钟"

    @staticmethod
    def daily_target_reached(stats: DailyStats) -> str:
        return (
            f"🎉 *日目标达成!*\n"
            f"净利润: ${stats.net_pnl_usdt:+.2f} / $50\n"
            f"胜率: {stats.win_rate:.0f}% | 交易: {stats.total_trades} 笔\n"
            f"建议停止交易，明天继续"
        )
