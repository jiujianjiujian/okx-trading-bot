"""
贝叶斯追踪器 v2 — 条件概率 + 组合信号追踪
P(win | A, B) = P(win) × P(A|win) × P(B|win) / (P(A) × P(B))
"""
import json
import os
from datetime import datetime


class BayesianTracker:
    def __init__(self, db_path: str = "bayesian_stats.json"):
        self.db_path = db_path
        self.data = self._load()

    def _load(self) -> dict:
        if os.path.exists(self.db_path):
            try:
                with open(self.db_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"conditions": {}, "combos": {}, "last_updated": None}

    def _save(self):
        self.data["last_updated"] = datetime.now().isoformat()
        with open(self.db_path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)
        os.replace(self.db_path + ".tmp", self.db_path)

    # ---- 单条件追踪 ----
    def update(self, conditions: dict, won: bool):
        """每笔交易完成后调用，同时更新单条件和组合条件"""
        # 单条件
        for key, value in conditions.items():
            self._update_entry(f"conditions.{key}::{value}", won)
        # 组合条件: 所有条件串成一个 key
        combo_key = "|".join(f"{k}={v}" for k, v in sorted(conditions.items()))
        self._update_entry(f"combos.{combo_key}", won)
        self._save()

    def _update_entry(self, cond_key: str, won: bool):
        entry = self.data.setdefault("conditions", {}).get(cond_key)
        # 处理嵌套存储 (combos 在 data["combos"] 下)
        if "." in cond_key:
            store, key = cond_key.split(".", 1)
        else:
            store, key = "conditions", cond_key
        if store not in self.data:
            self.data[store] = {}
        entry = self.data[store].get(key)
        if not entry:
            entry = {"wins": 0, "total": 0, "p_win": 0.5}
            self.data[store][key] = entry
        entry["total"] += 1
        if won:
            entry["wins"] += 1
        entry["p_win"] = round((entry["wins"] + 1) / (entry["total"] + 2), 4)

    # ---- 条件贝叶斯: P(A|B) × P(B) = P(B|A) × P(A) ----
    def conditional_probability(self, target: str, conditions: dict, min_samples: int = 5) -> float:
        """
        计算 P(win | target=value, 所有conditions)
        返回概率 0-1, 样本不足返回 None
        """
        combo_key = f"{target}|{'|'.join(f'{k}={v}' for k, v in sorted(conditions.items()))}"
        entry = self.data.get("combos", {}).get(combo_key)
        if entry and entry["total"] >= min_samples:
            return entry["p_win"]
        # 回退: 只用 target 的单条件概率
        target_key = f"{target}"
        entry = self.data.get("conditions", {}).get(target_key)
        if entry and entry["total"] >= min_samples:
            return entry["p_win"]
        return None

    def get_confidence_adjustment(self, conditions: dict, min_samples: int = 5) -> int:
        """根据条件概率调整置信度, 组合条件优先"""
        adjustments = []
        # 先查组合条件
        for combo_key, entry in sorted(self.data.get("combos", {}).items(),
                                        key=lambda x: -x[1]["total"]):
            if entry["total"] < min_samples:
                continue
            # 检查当前条件是否匹配此组合
            combo_parts = dict(p.split("=", 1) for p in combo_key.split("|"))
            match = all(conditions.get(k) == v for k, v in combo_parts.items())
            if match and len(combo_parts) >= 2:
                p = entry["p_win"]
                if p > 0.60:
                    adjustments.append(15)
                elif p > 0.55:
                    adjustments.append(8)
                elif p < 0.30:
                    adjustments.append(-25)
                elif p < 0.40:
                    adjustments.append(-12)
        # 单条件回退
        if not adjustments:
            for key, value in conditions.items():
                entry = self.data.get("conditions", {}).get(f"{key}::{value}")
                if entry and entry["total"] >= min_samples:
                    p = entry["p_win"]
                    if p > 0.60:
                        adjustments.append(10)
                    elif p > 0.55:
                        adjustments.append(5)
                    elif p < 0.30:
                        adjustments.append(-20)
                    elif p < 0.40:
                        adjustments.append(-10)
        if not adjustments:
            return 0
        return round(sum(adjustments) / len(adjustments))

    def get_summary(self) -> str:
        """人类可读的统计摘要 (优先显示组合条件)"""
        lines = ["*贝叶斯统计:*"]
        items = []
        for store in ("combos", "conditions"):
            for key, entry in sorted(self.data.get(store, {}).items(),
                                      key=lambda x: -x[1]["total"]):
                if entry["total"] < 3:
                    continue
                label = key.replace("::", "=")
                items.append(f"  {label}: {entry['wins']}/{entry['total']} "
                             f"({entry['p_win']:.0%})")
        lines.extend(items[:10])
        return "\n".join(lines) if len(lines) > 1 else "*贝叶斯统计:* 数据不足"
