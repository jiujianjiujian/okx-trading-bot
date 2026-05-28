"""
因子挖掘器 — LLM 驱动的自动化因子发现与进化

工作流:
  1. 回测预设因子 (BacktestEngine)
  2. 将表现最好的因子 + 市场数据发给 DeepSeek
  3. DeepSeek 提出新因子 → 回测 → 保留有效因子
  4. 每日运行，积累因子库 → 增强交易信号

轻量集成: 因子评分作为交易决策的加分维度，不改变止损止盈逻辑
"""

import json
import os
import time
from datetime import datetime


import http_wrapper as requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, PROXY_URL
from backtest_engine import BacktestEngine, FactorResult

FACTOR_DB_PATH = "factor_library.json"


class FactorMiner:
    """LLM 驱动的因子挖掘引擎"""

    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL
        self.proxies = {"https": PROXY_URL} if PROXY_URL else None
        self.bt = BacktestEngine()
        self._library: dict[str, dict] = self._load_library()

    # ================================================================
    # LLM API
    # ================================================================

    def _call_llm(self, system: str, prompt: str, max_tokens: int = 2048) -> str:
        if not self.api_key:
            return ""
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        p = {"model": "deepseek-chat", "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}],
             "max_tokens": max_tokens, "temperature": 0.6}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=h, json=p,
                              proxies=self.proxies, timeout=180)
            return r.json()["choices"][0]["message"]["content"]
        except Exception:
            return ""

    # ================================================================
    # 因子库管理
    # ================================================================

    def _load_library(self) -> dict:
        if not os.path.exists(FACTOR_DB_PATH):
            return {"factors": {}, "history": [], "last_run": None}
        try:
            with open(FACTOR_DB_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"factors": {}, "history": [], "last_run": None}

    def _save_library(self):
        self._library["last_run"] = datetime.now().isoformat()
        with open(FACTOR_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(self._library, f, ensure_ascii=False, indent=2)

    def get_active_factors(self, symbol: str | None = None) -> list[dict]:
        """获取当前活跃的有效因子"""
        active = []
        for _name, info in self._library.get("factors", {}).items():
            if info.get("active", False) and (symbol is None or symbol in info.get("symbols", [])):
                    active.append(info)
        active.sort(key=lambda x: x.get("score", 0), reverse=True)
        return active

    def compute_factor_signal(self, symbol: str, factor_name: str) -> float:
        """计算单个因子的当前信号值 (-1 ~ 1)"""
        info = self._library.get("factors", {}).get(factor_name)
        if not info or not info.get("active"):
            return 0.0

        code = info.get("code", "")
        if not code:
            return 0.0

        result = self.bt.test_factor(symbol, code, factor_name, "1H", 200)
        if not result or not result.passed:
            return 0.0

        return max(-1.0, min(1.0, result.ic_mean / 0.1))

    def composite_factor_score(self, symbol: str) -> dict:
        """综合所有活跃因子的评分"""
        factors = self.get_active_factors(symbol)
        if not factors:
            return {"score": 0.0, "direction": "neutral", "weight": 0.0, "details": []}

        total_score = 0.0
        total_weight = 0.0
        details = []

        for f in factors[:10]:  # 最多用前 10 个
            sig = self.compute_factor_signal(symbol, f["name"])
            weight = min(f.get("ir", 0.1), 0.5)  # IR 作为权重，上限 0.5
            total_score += sig * weight
            total_weight += weight
            details.append({
                "factor": f["name"],
                "signal": round(sig, 3),
                "weight": round(weight, 3),
                "desc": f.get("desc", ""),
            })

        avg_score = total_score / total_weight if total_weight > 0 else 0
        direction = "long" if avg_score > 0.15 else "short" if avg_score < -0.15 else "neutral"

        return {
            "score": round(avg_score, 3),
            "direction": direction,
            "weight": round(min(total_weight, 1.0), 2),
            "details": details,
        }

    # ================================================================
    # 因子发现主流程
    # ================================================================

    def run_discovery(self, symbols: list | None = None, send=None) -> dict:
        """
        执行一次完整的因子发现流程

        1. 回测预设因子
        2. LLM 生成新因子
        3. 回测新因子
        4. 更新因子库
        5. 报告结果
        """
        if symbols is None:
            symbols = ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]

        start_time = time.time()
        results = {"discovered": [], "passed": 0, "best_score": 0, "errors": []}

        for symbol in symbols:
            if send:
                print(f"🔬 因子挖掘 [{symbol}] ...")

            # Step 1: 回测预设因子
            preset_results = self.bt.test_preset_factors(symbol, "1H")
            if not preset_results:
                results["errors"].append(f"{symbol}: 预设因子回测失败")
                continue

            # 确保预设因子在库中
            for r in preset_results:
                if r.name not in self._library["factors"]:
                    self._library["factors"][r.name] = {
                        "name": r.name,
                        "code": r.code,
                        "ic_mean": r.ic_mean,
                        "ir": r.ir,
                        "sharpe": r.sharpe,
                        "score": r.score,
                        "active": r.passed,
                        "symbols": [symbol],
                        "discovered_at": datetime.now().isoformat(),
                    }

            # Step 2: 筛选有效的预设因子
            valid_presets = [r for r in preset_results if r.passed][:5]
            if send and valid_presets:
                best = valid_presets[0]
                send(f"  预设因子: {len(valid_presets)}个有效, 最佳={best.name} "
                     f"(IC={best.ic_mean:.3f}, 夏普={best.sharpe:.1f})")

            # Step 3: LLM 生成新因子
            llm_prompt = self._build_discovery_prompt(symbol, valid_presets)
            llm_system = (
                "你是一个加密货币量化因子研究员。"
                "你的任务是创造新的、不会与现有因子高度相关的技术分析因子。"
                "因子必须基于OHLCV数据，不涉及基本面。"
                "输出标准JSON，不要任何额外文本。"
            )

            llm_response = self._call_llm(llm_system, llm_prompt, 1536)
            if not llm_response:
                results["errors"].append(f"{symbol}: LLM 调用失败")
                continue

            new_factors = self._parse_llm_response(llm_response)
            if not new_factors:
                results["errors"].append(f"{symbol}: LLM 响应解析失败")
                continue

            # Step 4: 回测新因子
            for nf in new_factors:
                name = nf.get("name", "unnamed")
                code = nf.get("code", "")
                desc = nf.get("desc", "")

                if not code:
                    continue

                bt_result = self.bt.test_factor(symbol, code, f"{name}: {desc}", "1H")
                if not bt_result or bt_result.total_trades == 0:
                    continue

                results["discovered"].append({
                    "symbol": symbol, "name": f"{symbol}:{name}",
                    "code": code, "desc": desc,
                    "ic_mean": bt_result.ic_mean, "ir": bt_result.ir,
                    "sharpe": bt_result.sharpe, "score": bt_result.score,
                    "passed": bt_result.passed,
                })

                if bt_result.score > results["best_score"]:
                    results["best_score"] = bt_result.score

                if bt_result.passed:
                    results["passed"] += 1

                # 更新因子库
                full_name = f"{symbol}:{name}"
                if full_name not in self._library["factors"] or bt_result.score >= \
                   self._library["factors"][full_name].get("score", 0):
                    self._library["factors"][full_name] = {
                        "name": full_name,
                        "code": code,
                        "desc": desc,
                        "ic_mean": bt_result.ic_mean,
                        "ir": bt_result.ir,
                        "sharpe": bt_result.sharpe,
                        "score": bt_result.score,
                        "active": bt_result.passed,
                        "symbols": [symbol],
                        "discovered_at": datetime.now().isoformat(),
                    }

                if send:
                    tag = "✅" if bt_result.passed else "❌"
                    send(f"  {tag} {name}: IC={bt_result.ic_mean:.3f} "
                         f"IR={bt_result.ir:.2f} 夏普={bt_result.sharpe:.1f} "
                         f"评分={bt_result.score:.0f}")

        # 清理低分因子
        self._prune_library()

        # 保存
        self._library["history"].append({
            "time": datetime.now().isoformat(),
            "symbols": symbols,
            "discovered": len(results["discovered"]),
            "passed": results["passed"],
            "best_score": results["best_score"],
            "total_active": sum(1 for f in self._library["factors"].values() if f.get("active")),
        })
        self._save_library()

        elapsed = time.time() - start_time
        active_count = sum(1 for f in self._library["factors"].values() if f.get("active"))
        print(f"📊 因子挖掘完成 ({elapsed:.0f}s): "
              f"新发现{len(results['discovered'])}个, "
              f"通过{results['passed']}个, "
              f"因子库共{active_count}个活跃因子")

        return results

    def _build_discovery_prompt(self, symbol: str, valid_presets: list[FactorResult]) -> str:
        lines = [
            f"为加密货币 {symbol} 设计新的交易因子。",
            "",
            "=== 当前有效因子 (避免生成高度相关的因子) ===",
        ]
        for f in valid_presets[:5]:
            lines.append(f"  {f.name}: IC={f.ic_mean:.4f} IR={f.ir:.2f}")

        lines.extend([
            "",
            "=== 可用数据 (均为列表, 索引越大越新) ===",
            "  close, open_, high, low, volume, returns",
            "",
            "=== 可用函数 ===",
            "  sma(arr, period) — 简单移动平均",
            "  ema(arr, period) — 指数移动平均",
            "  roll(arr, period) — 滞后 period 个周期",
            "  rank(arr) — 交叉截面排名 (0~1)",
            "  ts_sum(arr, period) — 滚动求和",
            "  ts_corr(a, b, period) — 滚动相关系数 (-1~1)",
            "  prev_close — 前一周期收盘价",
            "",
            "=== 因子要求 ===",
            "1. 每行是一个 Python 表达式，值为浮点数列表",
            "2. 正值 → 看多信号，负值 → 看空信号",
            "3. 加密市场特有的特征: 24H交易、全球联动、波动性大",
            "4. 避免生成与上面已列因子高度相似的因子",
            "5. 每个因子一行，简单清晰",
            "",
            "输出 JSON:",
            '{"factors":[{"name":"因子名","code":"表达式","desc":"描述"},...]}',
            "只输出 JSON。",
        ])
        return "\n".join(lines)

    def _parse_llm_response(self, response: str) -> list[dict]:
        try:
            s = response.find("{")
            e = response.rfind("}") + 1
            data = json.loads(response[s:e])
            return data.get("factors", [])
        except Exception:
            return []

    def _prune_library(self, max_factors: int = 50):
        """保留评分最高的 N 个因子"""
        factors = self._library.get("factors", {})
        if len(factors) <= max_factors:
            return

        sorted_factors = sorted(factors.values(), key=lambda x: x.get("score", 0), reverse=True)
        keep = {f["name"]: f for f in sorted_factors[:max_factors]}
        # 标记后 50% 为 inactive
        for f in sorted_factors[max_factors // 2:]:
            f["active"] = False
        self._library["factors"] = keep

    # ================================================================
    # 快速盘前因子扫描 — 给 auto_trader 用
    # ================================================================

    def quick_scan(self, symbol: str) -> dict:
        """快速因子扫描: 只跑活跃因子，返回信号方向"""
        return self.composite_factor_score(symbol)


# ================================================================
# 独立运行
# ================================================================

if __name__ == "__main__":
    print("=" * 50)
    print("🔬 因子挖掘引擎")
    print("=" * 50)

    miner = FactorMiner()

    def log(msg):
        print(f"[{datetime.now():%H:%M:%S}] {msg}")

    results = miner.run_discovery(
        symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        send=log,
    )

    print(f"\n发现 {len(results['discovered'])} 个新因子, {results['passed']} 个通过")
    if results["errors"]:
        print(f"错误: {results['errors']}")
