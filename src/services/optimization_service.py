"""策略自优化服务 — 每日复盘 + 每周参数优化

使用 DeepSeek 分析交易记录, 自动调整剥头皮参数。
不自动修改配置文件, 而是输出建议 → Telegram 通知 → 用户确认。
"""

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from ..core.models import DailyStats, PnLRecord
from ..infrastructure.deepseek_client import DeepSeekClient
from ..infrastructure.bybit_client import BybitClient
from ..infrastructure.logging_ import get_logger

logger = get_logger(__name__)

DAILY_REVIEW_PROMPT = """你是专业量化交易分析师。复盘今日剥头皮交易:

{trade_data}

请分析:
1. 今日胜率/盈亏比是否符合预期 (目标胜率≥55%, 净RR≥2.0)
2. 哪些币种表现好, 哪些差
3. AI 决策是否在特定时段更准确
4. 有没有可以改进的地方

返回 JSON:
{{
  "score": 0-100,
  "summary": "一句话总结",
  "best_coins": ["BTCUSDT"],
  "worst_coins": ["PEPEUSDT"],
  "issues": ["止损太紧"],
  "suggestions": ["扩大SL到0.3%"],
  "keep_params": true
}}"""

WEEKLY_OPTIMIZE_PROMPT = """你是量化交易策略优化师。分析本周交易数据, 给出参数调整建议:

{weekly_data}

当前参数:
- SL范围: {sl_min}%-{sl_max}%
- TP范围: {tp_min}%-{tp_max}%
- 最低AI置信度: {min_conf}
- 最大并发持仓: {max_pos}
- 每日目标: ${daily_target}

分析:
1. 当前参数是否需要调整
2. 哪些币种应该移除/添加
3. SL/TP 范围建议
4. 置信度阈值建议

返回 JSON:
{{
  "sl_pct_min": 0.15,
  "sl_pct_max": 0.50,
  "tp_pct_min": 0.30,
  "tp_pct_max": 1.50,
  "min_confidence": 65,
  "max_positions": 3,
  "add_coins": [],
  "remove_coins": [],
  "summary": "本周策略表现...",
  "recommendation": "建议调整..."
}}"""


class OptimizationService:
    """策略自优化引擎"""

    def __init__(self, deepseek: DeepSeekClient, store, bybit: BybitClient):
        self._ds = deepseek
        self._store = store
        self._bybit = bybit

    # ── 每日复盘 ──────────────────────────────────────

    def daily_review(self) -> Optional[dict]:
        """生成每日交易复盘"""
        if not self._ds.configured:
            return None

        stats = self._store.get_today_stats()
        trades = self._store.get_today_trades()
        decisions = self._store.get_recent_decisions(limit=100)

        if stats["total_trades"] == 0:
            return {
                "score": 0,
                "summary": "今日无交易",
                "suggestions": [],
            }

        # 按币种分组统计
        coin_stats = {}
        for t in trades:
            sym = t.get("symbol", "UNKNOWN")
            if sym not in coin_stats:
                coin_stats[sym] = {"trades": 0, "wins": 0, "pnl": 0.0}
            coin_stats[sym]["trades"] += 1
            pnl = t.get("pnl_usdt", 0)
            if pnl > 0:
                coin_stats[sym]["wins"] += 1
            coin_stats[sym]["pnl"] += pnl

        # 决策分析
        ai_actions = {}
        for d in decisions:
            act = d.get("action", "WAIT")
            ai_actions[act] = ai_actions.get(act, 0) + 1

        trade_summary = (
            f"总交易: {stats['total_trades']} | 胜率: {stats['win_rate']:.0f}% | "
            f"净利: ${stats['net_pnl_usdt']:+.2f}\n"
            f"币种: " + ", ".join(
                f"{c}({s['trades']}笔/{s['wins']}胜)"
                for c, s in sorted(coin_stats.items(), key=lambda x: -x[1]['pnl'])
            ) + "\n"
            f"AI决策: {ai_actions}"
        )

        prompt = DAILY_REVIEW_PROMPT.format(trade_data=trade_summary)
        result = self._ds.chat_json([
            {"role": "system", "content": "你是量化交易分析师。只返回JSON。"},
            {"role": "user", "content": prompt},
        ], max_tokens=600)

        if result:
            result["stats"] = stats
            result["coin_stats"] = coin_stats
            logger.info("每日复盘: %s", result.get("summary", ""))

        return result

    # ── 每周自优化 ────────────────────────────────────

    def weekly_optimize(self) -> Optional[dict]:
        """分析本周数据, 建议参数调整"""
        if not self._ds.configured:
            return None

        # 汇总近7天交易
        from ..infrastructure.config import (
            SCALP_SL_PCT_MIN, SCALP_SL_PCT_MAX,
            SCALP_TP_PCT_MIN, SCALP_TP_PCT_MAX,
            MIN_AI_CONFIDENCE, MAX_POSITIONS, DAILY_TARGET_USDT,
            SCALP_UNIVERSE,
        )

        recent = self._store.get_recent_trades(limit=200)
        now = datetime.now(timezone.utc)
        week_ago = now - timedelta(days=7)
        week_trades = [
            t for t in recent
            if datetime.fromisoformat(t["time"].replace("Z", "+00:00")) > week_ago
        ]

        if len(week_trades) < 10:
            logger.info("周交易不足10笔, 跳过优化")
            return None

        wins = sum(1 for t in week_trades if t.get("pnl_usdt", 0) > 0)
        total = len(week_trades)
        total_pnl = sum(t.get("pnl_usdt", 0) for t in week_trades)
        total_fees = sum(t.get("fee_paid", 0) for t in week_trades)

        # 按 SL/TP 分组分析 (从 decisions 表获取)
        decisions = self._store.get_recent_decisions(limit=500)

        weekly_data = (
            f"本周交易: {total}笔 | 胜率: {wins/total*100:.0f}% | "
            f"净利: ${total_pnl - total_fees:+.2f}\n"
            f"日均: {total/7:.1f}笔 | "
            f"当前币池: {SCALP_UNIVERSE}"
        )

        prompt = WEEKLY_OPTIMIZE_PROMPT.format(
            weekly_data=weekly_data,
            sl_min=SCALP_SL_PCT_MIN, sl_max=SCALP_SL_PCT_MAX,
            tp_min=SCALP_TP_PCT_MIN, tp_max=SCALP_TP_PCT_MAX,
            min_conf=MIN_AI_CONFIDENCE,
            max_pos=MAX_POSITIONS,
            daily_target=DAILY_TARGET_USDT,
        )

        result = self._ds.chat_json([
            {"role": "system", "content": "你是量化策略优化师。只返回JSON。"},
            {"role": "user", "content": prompt},
        ], max_tokens=800)

        if result:
            result["week_stats"] = {
                "trades": total, "win_rate": round(wins/total*100, 1),
                "net_pnl": round(total_pnl - total_fees, 2),
            }
            logger.info("周优化建议: %s", result.get("recommendation", ""))

        return result
