"""AI 决策服务 — 双 AI 校验管线

快速 AI: 技术指标打分 → LONG/SHORT/WAIT
深度 AI: 完整 prompt → JSON 格式决策 (方向/置信度/策略/理由)
最终: 方向一致 → 通过, 不一致 → WAIT
"""


from ..core.models import MarketSnapshot, ScalpDecision, IndicatorBundle
from ..infrastructure.deepseek_client import DeepSeekClient
from ..infrastructure.local_llm_client import LocalLLMClient
from ..infrastructure.config import MIN_AI_CONFIDENCE
from ..infrastructure.logging_ import get_logger
from .analysis_service import AnalysisService

logger = get_logger(__name__)


# ── AI Prompt 模板 ────────────────────────────────────

SYSTEM_PROMPT = """你是一名专业加密货币剥头皮交易员。你的任务是分析市场数据,给出精确的短线交易决策。

规则:
1. 你只能选择 LONG(做多)、SHORT(做空)、WAIT(观望)
2. 置信度 0-100, 低于65就不要交易
3. 剥头皮交易: 持仓时间数分钟到数小时, 止损紧(≤0.5%), 止盈小(≥0.3%), 但盈亏比要高
4. 优先考虑趋势方向, 逆势交易风险高
5. 低波动 + 低成交量 = 不交易
6. 费用是双向 0.11%, 必须确保扣费后仍盈利

返回纯 JSON:
{
  "action": "LONG" | "SHORT" | "WAIT",
  "confidence": 0-100,
  "scalping_strategy": "momentum" | "mean_reversion" | "breakout" | "pullback",
  "key_reasons": ["原因1", "原因2"],
  "risk_warnings": ["风险1"]
}"""


def _build_user_prompt(snap: MarketSnapshot, bundle: IndicatorBundle, mode: str = "scalp") -> str:
    """构建 AI 分析 prompt"""
    kline_5m = snap.klines.get("5m", [])
    recent_closes = [f.close for f in kline_5m[-5:]] if kline_5m else []

    return f"""分析以下市场数据, 给出剥头皮交易决策:

币种: {snap.symbol}
模式: {mode}
当前价格: {snap.current_price}
24h涨跌: {snap.change_24h:+.2f}%

【技术指标】
EMA20: {bundle.ema20:.4f} | EMA50: {bundle.ema50:.4f}
RSI: {bundle.rsi:.1f}
MACD: {bundle.macd:.4f} | {'看涨' if bundle.macd_bullish else '看跌'}
ATR: {bundle.atr:.4f} (波动率)
布林带: 上{bundle.bb_upper:.2f} 中{bundle.bb_mid:.2f} 下{bundle.bb_lower:.2f} (宽{bundle.bb_width:.1f}%)
ADX: {bundle.adx:.1f} (DI+ {bundle.di_plus:.1f} DI- {bundle.di_minus:.1f})
SuperTrend: {bundle.supertrend}
成交量放大: {'是' if bundle.vol_expansion else '否'}
支撑: {bundle.support} | 阻力: {bundle.resistance}

【盘口】
价差: {snap.spread_pct:.4f}%
买卖失衡: {snap.bid_ask_imbalance:+.3f} (正=买盘强)

【OI持仓分析 — 关键!】
OI变动: {snap.oi_change_pct:+.2f}% | 资金费率: {snap.funding_rate:.4%}
信号规则:
  OI up + 价跌 + 费率正 → 多头拥挤付资金费 → 空头信号增强
  OI down + 价横盘 → 只是去杠杆 → 不要追空
  OI up + 价涨 → 新资金入场 → 趋势延续
  OI down + 价跌 → 多头爆仓 → 下跌趋势确认

【最近价格】
{recent_closes}

注意: 手续费双向 {0.11}%, 结合OI信号判断方向强度"""


class DecisionService:
    """AI 剥头皮决策引擎"""

    def __init__(self, deepseek: DeepSeekClient, analysis: AnalysisService, local_llm: LocalLLMClient | None = None):
        self._ds = deepseek
        self._local = local_llm
        self._analysis = analysis
        # 本地 LLM 优先
        self._primary = self._local if self._local and self._local.configured else self._ds

    @property
    def available(self) -> bool:
        return self._ds.configured

    def decide(
        self,
        snap: MarketSnapshot,
        bundle: IndicatorBundle,
        mode: str = "scalp",
    ) -> ScalpDecision:
        """
        完整 AI 决策管线

        Returns:
            ScalpDecision with action/confidence/reason filled
        """
        base = ScalpDecision(
            symbol=snap.symbol,
            direction=None,
            action="WAIT",
            mode=mode,
            confidence=0,
            reason="AI 未配置或调用失败",
            entry=snap.current_price,
        )

        if not self.available:
            return self._rule_based_decision(snap, bundle)

        # ── 快速 AI ──
        quick_prompt = f"币种: {snap.symbol} 价格{snap.current_price}, RSI {bundle.rsi:.1f}, 趋势{'看涨' if bundle.macd_bullish else '看跌'}, ADX {bundle.adx:.1f}。只回答 LONG, SHORT, 或 WAIT。"
        quick_resp = self._primary.chat([
            {"role": "system", "content": "你是交易员。只回答 LONG, SHORT, 或 WAIT 一个词。"},
            {"role": "user", "content": quick_prompt},
        ], max_tokens=10, temperature=0.1)

        quick_direction = quick_resp.strip().upper() if quick_resp else "WAIT"
        logger.info("快速AI(%s): %s → %s",
                    "local" if self._primary is self._local else "deepseek",
                    snap.symbol, quick_direction)

        # ── 深度 AI ──
        deep_prompt = _build_user_prompt(snap, bundle, mode)
        deep_result = self._primary.chat_json([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": deep_prompt},
        ], max_tokens=800)

        if deep_result is None:
            return self._rule_based_decision(snap, bundle)

        deep_action = str(deep_result.get("action", "WAIT")).upper()
        deep_confidence = int(deep_result.get("confidence", 0))
        deep_strategy = str(deep_result.get("scalping_strategy", "momentum"))
        reasons = deep_result.get("key_reasons", [])
        warnings = deep_result.get("risk_warnings", [])

        logger.info("深度AI: %s → %s (置信度 %s)", snap.symbol, deep_action, deep_confidence)

        # ── 双 AI 交叉验证 ──
        if quick_direction == deep_action and deep_action in ("LONG", "SHORT"):
            direction = "long" if deep_action == "LONG" else "short"
            base.action = deep_action
            base.direction = direction
            base.confidence = deep_confidence
            base.scalping_strategy = deep_strategy
            base.reason = "; ".join(reasons[:3]) if reasons else "双AI一致"
            if warnings:
                base.reason += f" | ⚠️ {warnings[0]}"
        elif deep_action in ("LONG", "SHORT"):
            # 快速 AI 不一致但深度 AI 有明确方向 → 降低置信度
            direction = "long" if deep_action == "LONG" else "short"
            base.action = deep_action
            base.direction = direction
            base.confidence = max(0, deep_confidence - 15)
            base.scalping_strategy = deep_strategy
            base.reason = "; ".join(reasons[:3]) if reasons else "深度AI决策(快速AI不一致)"
            base.reason += f" | 快速AI: {quick_direction}"
        else:
            base.action = "WAIT"
            base.reason = f"深度AI={deep_action}, 快速AI={quick_direction}, 不一致"

        # ── 低置信度过滤 ──
        if base.confidence < MIN_AI_CONFIDENCE:
            base.action = "WAIT"
            base.reason = f"AI 置信度 {base.confidence} < {MIN_AI_CONFIDENCE}"

        return base

    def quick_scan(self, symbol: str, price: float, bundle: IndicatorBundle) -> str:
        """快速扫描 — 最低 token 消耗"""
        prompt = (
            f"{symbol} ${price:.4f} | RSI {bundle.rsi:.0f} | "
            f"MACD {'bull' if bundle.macd_bullish else 'bear'} | "
            f"ADX {bundle.adx:.0f} | 趋势{bundle.supertrend} | "
            f"波动{'扩张' if bundle.vol_expansion else '收敛'} → LONG/SHORT/WAIT?"
        )
        resp = self._ds.chat([
            {"role": "system", "content": "只回答 LONG, SHORT, 或 WAIT。"},
            {"role": "user", "content": prompt},
        ], max_tokens=8, temperature=0)
        return resp.strip().upper() if resp else "WAIT"

    def _rule_based_decision(
        self, snap: MarketSnapshot, bundle: IndicatorBundle,
    ) -> ScalpDecision:
        """纯规则决策 (AI 不可用时的回退)"""
        score = 0
        direction = "long"

        if bundle.ema20 > bundle.ema50:
            score += 20
        else:
            score -= 20
            direction = "short"

        if bundle.rsi < 30:
            score += 15
            direction = "long"
        elif bundle.rsi > 70:
            score += 15
            direction = "short"
        elif 40 <= bundle.rsi <= 60:
            pass  # 中性
        else:
            score += 5

        if bundle.macd_bullish:
            score += 10 if direction == "long" else -5
        else:
            score += 10 if direction == "short" else -5

        if bundle.adx > 25:
            score += 10
        if bundle.vol_expansion:
            score += 10

        confidence = min(abs(score), 80)
        if score >= 30:
            action = "LONG" if direction == "long" else "SHORT"
        else:
            action = "WAIT"

        return ScalpDecision(
            symbol=snap.symbol,
            direction=direction if action != "WAIT" else None,
            action=action,
            mode="scalp",
            confidence=confidence,
            reason=f"规则引擎 (得分 {score})",
            entry=snap.current_price,
            scalping_strategy="momentum" if bundle.adx > 25 else "mean_reversion",
        )
