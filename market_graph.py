"""
Market graph scorer.

This is a deterministic pre-trade filter. It maps market signals into
directional nodes, measures BULL/BEAR cluster agreement, and blocks trades
when the edge is weak, signals conflict, or liquidity is poor.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GraphNode:
    name: str
    group: str
    bias: int
    strength: float
    reason: str


class MarketGraphScorer:
    """Lightweight signal graph for OKX perp trading decisions."""

    TF_WEIGHTS = {
        "1m": 0.40,
        "5m": 0.55,
        "15m": 0.70,
        "1H": 0.90,
        "4H": 1.15,
        "1D": 1.10,
    }

    def __init__(
        self,
        min_edge: float = 60.0,
        max_conflict: float = 35.0,
        min_liquidity: float = 55.0,
    ):
        self.min_edge = float(min_edge)
        self.max_conflict = float(max_conflict)
        self.min_liquidity = float(min_liquidity)

    @staticmethod
    def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _bias_label(bias: float) -> str:
        if bias >= 0.18:
            return "BULL"
        if bias <= -0.18:
            return "BEAR"
        return "NEUTRAL"

    @staticmethod
    def _adx_factor(data: dict) -> float:
        adx = data.get("adx", {})
        value = float(adx.get("adx", 20) or 20)
        if value >= 35:
            return 1.20
        if value >= 25:
            return 1.05
        if value < 15:
            return 0.70
        return 0.90

    @staticmethod
    def _norm_bool_bias(long_ok: bool, short_ok: bool) -> int:
        if long_ok and not short_ok:
            return 1
        if short_ok and not long_ok:
            return -1
        return 0

    def _tf_nodes(self, market: dict) -> list[GraphNode]:
        nodes: list[GraphNode] = []
        for tf, data in market.items():
            weight = self.TF_WEIGHTS.get(tf, 0.70)
            factor = self._adx_factor(data)

            ema_bias = self._norm_bool_bias(
                data.get("ema20", 0) > data.get("ema50", 0),
                data.get("ema20", 0) < data.get("ema50", 0),
            )
            if ema_bias:
                nodes.append(GraphNode(f"{tf}_ema", "trend", ema_bias, weight * factor, f"{tf} EMA"))

            macd = data.get("macd", {})
            macd_bias = 1 if macd.get("is_bullish") else -1
            nodes.append(GraphNode(f"{tf}_macd", "momentum", macd_bias, weight * 0.60, f"{tf} MACD"))

            st = data.get("supertrend", {})
            st_bias = self._norm_bool_bias(st.get("trend") == "up", st.get("trend") == "down")
            if st_bias:
                nodes.append(GraphNode(f"{tf}_supertrend", "trend", st_bias, weight * 0.70, f"{tf} SuperTrend"))

            bb_range = float(data.get("bb_upper", 0) or 0) - float(data.get("bb_lower", 0) or 0)
            if bb_range > 0:
                price_pos = (float(data.get("price", 0) or 0) - float(data.get("bb_lower", 0) or 0)) / bb_range
                if price_pos < 0.15:
                    nodes.append(GraphNode(f"{tf}_bb_low", "mean_reversion", 1, weight * 0.45, f"{tf} lower BB"))
                elif price_pos > 0.85:
                    nodes.append(GraphNode(f"{tf}_bb_high", "mean_reversion", -1, weight * 0.45, f"{tf} upper BB"))

        return nodes

    def _context_nodes(
        self,
        chain: dict,
        smc: dict,
        liq_text: str,
        microstructure: dict,
        structure: dict,
    ) -> list[GraphNode]:
        nodes: list[GraphNode] = []

        smc_trend = smc.get("trend", "")
        if smc_trend == "bullish":
            nodes.append(GraphNode("smc_trend", "structure", 1, 1.05, "SMC bullish"))
        elif smc_trend == "bearish":
            nodes.append(GraphNode("smc_trend", "structure", -1, 1.05, "SMC bearish"))

        sweep = smc.get("liquidity_sweep") or {}
        if sweep.get("type") == "short_trap":
            nodes.append(GraphNode("smc_sweep", "liquidity", 1, 0.85, "short trap"))
        elif sweep.get("type") == "long_trap":
            nodes.append(GraphNode("smc_sweep", "liquidity", -1, 0.85, "long trap"))

        trend_strength = structure.get("trend_strength", "")
        if "强多" in trend_strength:
            nodes.append(GraphNode("structure_trend", "structure", 1, 1.05, "strong bull structure"))
        elif "偏多" in trend_strength:
            nodes.append(GraphNode("structure_trend", "structure", 1, 0.75, "bull structure"))
        elif "强空" in trend_strength:
            nodes.append(GraphNode("structure_trend", "structure", -1, 1.05, "strong bear structure"))
        elif "偏空" in trend_strength:
            nodes.append(GraphNode("structure_trend", "structure", -1, 0.75, "bear structure"))

        signal = chain.get("signal", "neutral")
        if signal == "crowded_long":
            nodes.append(GraphNode("crowding", "positioning", -1, 0.90, "crowded long"))
        elif signal == "crowded_short":
            nodes.append(GraphNode("crowding", "positioning", 1, 0.90, "crowded short"))
        elif abs(float(chain.get("funding_rate", 0) or 0)) < 0.001 and abs(float(chain.get("oi_change_pct", 0) or 0)) < 5:
            nodes.append(GraphNode("positioning_neutral", "positioning", 0, 0.40, "neutral positioning"))

        liq_lower = (liq_text or "").lower()
        if "bullish" in liq_lower:
            nodes.append(GraphNode("liquidation_pressure", "liquidity", 1, 0.75, "liquidation bullish"))
        elif "bearish" in liq_lower:
            nodes.append(GraphNode("liquidation_pressure", "liquidity", -1, 0.75, "liquidation bearish"))

        imbalance = float(microstructure.get("imbalance", 0.5) or 0.5)
        if imbalance >= 0.62:
            nodes.append(GraphNode("book_imbalance", "order_flow", 1, 0.75, "bid depth advantage"))
        elif imbalance <= 0.38:
            nodes.append(GraphNode("book_imbalance", "order_flow", -1, 0.75, "ask depth advantage"))

        buy_sell = float(microstructure.get("buy_sell_ratio", 0.5) or 0.5)
        if buy_sell >= 0.62:
            nodes.append(GraphNode("taker_flow", "order_flow", 1, 0.65, "taker buy flow"))
        elif buy_sell <= 0.38:
            nodes.append(GraphNode("taker_flow", "order_flow", -1, 0.65, "taker sell flow"))

        cvd = float(microstructure.get("cvd", 0) or 0)
        if cvd > 500:
            nodes.append(GraphNode("cvd", "order_flow", 1, 0.65, "positive CVD"))
        elif cvd < -500:
            nodes.append(GraphNode("cvd", "order_flow", -1, 0.65, "negative CVD"))

        return nodes

    def _liquidity_score(self, microstructure: dict) -> tuple[float, list[str]]:
        score = 100.0
        blockers: list[str] = []
        spread = float(microstructure.get("spread", 0) or 0)
        depth = float(microstructure.get("depth_1pct", 0) or 0)

        if spread > 0.20:
            score -= 45
            blockers.append(f"价差过大({spread:.3f}%)")
        elif spread > 0.12:
            score -= 25
            blockers.append(f"价差偏大({spread:.3f}%)")
        elif spread > 0.08:
            score -= 10

        if depth <= 0:
            score -= 20
            blockers.append("盘口深度缺失")
        elif depth < 30000:
            score -= 45
            blockers.append(f"1%深度不足({depth:.0f})")
        elif depth < 50000:
            score -= 20

        if microstructure.get("liquidity_gap"):
            score -= 35
            blockers.append("流动性断层")

        atr_ratio = float(microstructure.get("atr_ratio", 1.0) or 1.0)
        if atr_ratio > 2.2:
            score -= 20
            blockers.append(f"波动异常(ATRx{atr_ratio:.1f})")

        return self._clamp(score), blockers

    def score(
        self,
        symbol: str,
        direction: str,
        market: dict,
        chain: dict,
        smc: dict,
        liq_text: str,
        microstructure: dict,
        structure: dict,
    ) -> dict:
        direction = (direction or "").lower()
        dir_bias = 1 if direction == "long" else -1 if direction == "short" else 0

        nodes = self._tf_nodes(market) + self._context_nodes(chain, smc, liq_text, microstructure, structure)
        weighted = sum(node.bias * node.strength for node in nodes)
        directional_weight = sum(node.strength for node in nodes if node.bias)
        total_weight = sum(node.strength for node in nodes) or 1.0
        raw_bias = weighted / total_weight

        favorable = sum(node.strength for node in nodes if dir_bias and node.bias == dir_bias)
        opposing = sum(node.strength for node in nodes if dir_bias and node.bias == -dir_bias)
        neutral = sum(node.strength for node in nodes if node.bias == 0)

        if not dir_bias:
            edge_score = self._clamp(abs(raw_bias) * 100)
        elif directional_weight > 0:
            directional_advantage = (favorable - opposing) / directional_weight
            edge_score = self._clamp(50 + directional_advantage * 50)
        else:
            edge_score = 50.0

        cluster_confidence = self._clamp((favorable / directional_weight * 100) if directional_weight and dir_bias else abs(raw_bias) * 100)
        conflict_score = self._clamp((opposing / directional_weight * 100) if directional_weight and dir_bias else 0)
        liquidity_score, blockers = self._liquidity_score(microstructure)

        if chain.get("signal") == "danger":
            conflict_score = self._clamp(conflict_score + 30)
            blockers.append("OI/费率危险")
        if structure.get("range_bound"):
            conflict_score = self._clamp(conflict_score + 15)
            blockers.append("窄幅横盘")

        expected_edge = self._clamp(
            edge_score * 0.50
            + cluster_confidence * 0.25
            + liquidity_score * 0.25
            - max(0.0, conflict_score - 25) * 0.45
        )

        if edge_score < self.min_edge:
            blockers.append(f"图谱优势不足({edge_score:.0f}<{self.min_edge:.0f})")
        if conflict_score > self.max_conflict:
            blockers.append(f"信号冲突过高({conflict_score:.0f}>{self.max_conflict:.0f})")
        if liquidity_score < self.min_liquidity:
            blockers.append(f"流动性评分不足({liquidity_score:.0f}<{self.min_liquidity:.0f})")

        aligned = True
        if dir_bias:
            aligned = raw_bias * dir_bias >= 0.05
            if not aligned:
                blockers.append("图谱方向与交易方向不一致")

        top_support = sorted(
            (node for node in nodes if dir_bias and node.bias == dir_bias),
            key=lambda node: node.strength,
            reverse=True,
        )[:5]
        top_conflict = sorted(
            (node for node in nodes if dir_bias and node.bias == -dir_bias),
            key=lambda node: node.strength,
            reverse=True,
        )[:5]

        return {
            "symbol": symbol,
            "direction": direction,
            "cluster": self._bias_label(raw_bias),
            "raw_bias": round(raw_bias, 3),
            "edge_score": round(edge_score, 1),
            "expected_edge": round(expected_edge, 1),
            "cluster_confidence": round(cluster_confidence, 1),
            "conflict_score": round(conflict_score, 1),
            "liquidity_score": round(liquidity_score, 1),
            "node_count": len(nodes),
            "edge_count": self.estimate_edge_count(nodes),
            "favorable_weight": round(favorable, 2),
            "opposing_weight": round(opposing, 2),
            "neutral_weight": round(neutral, 2),
            "support": [node.reason for node in top_support],
            "conflicts": [node.reason for node in top_conflict],
            "blockers": list(dict.fromkeys(blockers)),
            "trade_allowed": aligned
            and edge_score >= self.min_edge
            and conflict_score <= self.max_conflict
            and liquidity_score >= self.min_liquidity,
        }

    @staticmethod
    def estimate_edge_count(nodes: list[GraphNode]) -> int:
        """Estimate same/opposite relationship edges without building a dense graph."""
        count = 0
        for idx, left in enumerate(nodes):
            for right in nodes[idx + 1:]:
                if left.group == right.group or left.bias == right.bias or left.bias == -right.bias:
                    count += 1
        return count
