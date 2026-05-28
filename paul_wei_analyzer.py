"""
Paul Wei 交易数据分析器 — 从 bwjoke/BTC-Trading-Since-2020 提取可执行策略模式

数据来源: https://github.com/bwjoke/BTC-Trading-Since-2020
52倍收益交易员的6年完整交易记录 (2020-05-01 → 2026-05-03)
43,219 条订单 · 173,113 条成交 · 17,177 条钱包事件

提取维度:
  1. 仓位管理 — 单笔风险%、杠杆、加仓模式
  2. 盈亏结构 — 胜率、盈亏比、利润因子
  3. 时段偏好 — 什么时间交易最多、盈利率最高
  4. 持仓周期 — 平均持有时长、盈亏单持时差异
  5. 市场适应 — 牛熊市的不同行为模式
  6. 回撤恢复 — 最大回撤、恢复时间、缩仓模式
"""

import contextlib
import csv
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Optional

import http_wrapper as requests

from config import PROXY_URL

DATA_DIR = "paul_wei_data"
REPO_URL = "https://github.com/bwjoke/BTC-Trading-Since-2020.git"
CACHE_FILE = f"{DATA_DIR}/analysis_cache.json"


class PaulWeiAnalyzer:
    """Paul Wei 交易模式分析器"""

    def __init__(self):
        self.proxies = {"https": PROXY_URL} if PROXY_URL else None
        self._stats: Optional[dict] = None
        self._orders: list = []
        self._trades: list = []
        self._equity: list = []
        self._ensure_data()

    # ================================================================
    # 数据准备
    # ================================================================

    def _ensure_data(self):
        """确保数据可用，优先用缓存"""
        if not os.path.exists(DATA_DIR):
            os.makedirs(DATA_DIR, exist_ok=True)

        # 尝试加载缓存
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    self._stats = json.load(f)
                print("[PaulWei] 已加载缓存分析结果")
                return
            except Exception:
                pass

        # 检查是否有已下载的数据
        order_file = f"{DATA_DIR}/api-v1-order.csv"
        if not os.path.exists(order_file):
            self._download_data()

        if os.path.exists(order_file):
            self._load_and_analyze()

    def _download_data(self):
        """从 GitHub 下载最新数据 (只下载 CSV, 不克隆整个 git)"""
        base = "https://raw.githubusercontent.com/bwjoke/BTC-Trading-Since-2020/main"
        files = [
            "api-v1-order.csv",
            "api-v1-execution-tradeHistory.csv",
            "derived-equity-curve.csv",
            "api-v1-user-walletHistory.csv",
        ]
        for f in files:
            url = f"{base}/{f}"
            try:
                r = requests.get(url, proxies=self.proxies, timeout=300)
                if r.status_code == 200:
                    path = os.path.join(DATA_DIR, f)
                    content = r.text
                    # http_wrapper 返回的 text 可能是 bytes 或 str
                    if isinstance(content, bytes):
                        with open(path, "wb") as fh:
                            fh.write(content)
                    else:
                        with open(path, "w", encoding="utf-8") as fh:
                            fh.write(content)
                    size_mb = os.path.getsize(path) / 1024 / 1024
                    print(f"[PaulWei] 下载完成: {f} ({size_mb:.1f}MB)")
                else:
                    print(f"[PaulWei] 下载失败: {f} (HTTP {r.status_code})")
            except Exception as e:
                print(f"[PaulWei] 下载异常: {f} - {e}")

    def _load_and_analyze(self):
        """加载CSV并执行全量分析"""
        print("[PaulWei] 开始分析交易数据...")

        # 加载订单
        self._orders = self._read_csv(f"{DATA_DIR}/api-v1-order.csv")
        # 加载成交
        self._trades = self._read_csv(f"{DATA_DIR}/api-v1-execution-tradeHistory.csv")
        # 加载权益曲线
        self._equity = self._read_csv(f"{DATA_DIR}/derived-equity-curve.csv")

        if not self._orders or not self._trades:
            print("[PaulWei] 数据加载失败，跳过分析")
            return

        # 提取统计
        self._stats = self._compute_stats()
        # 缓存
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(self._stats, f, ensure_ascii=False, indent=2)
        print(f"[PaulWei] 分析完成，已缓存。订单={len(self._orders)} 成交={len(self._trades)}")

    @staticmethod
    def _read_csv(path: str) -> list:
        """读取CSV为字典列表"""
        if not os.path.exists(path):
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows

    # ================================================================
    # 核心统计
    # ================================================================

    def _compute_stats(self) -> dict:
        """计算全部交易统计"""
        # --- 订单分析 ---
        filled = [o for o in self._orders if o.get("ordStatus") == "Filled"]
        cancelled = [o for o in self._orders if o.get("ordStatus") == "Canceled"]
        buys = [o for o in filled if o.get("side") == "Buy"]
        sells = [o for o in filled if o.get("side") == "Sell"]
        limits = [o for o in filled if o.get("ordType") == "Limit"]
        markets = [o for o in filled if o.get("ordType") == "Market"]
        stops = [o for o in filled if o.get("ordType") in ("Stop", "StopLimit")]
        fill_rate = len(filled) / len(self._orders) * 100 if self._orders else 0

        # --- 时间分析 ---
        hour_dist = defaultdict(lambda: {"count": 0, "pnl": 0.0})
        day_dist = defaultdict(lambda: {"count": 0, "pnl": 0.0})

        for t in self._trades:
            try:
                ts = t.get("transactTime", "") or t.get("timestamp", "")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    h = dt.hour
                    d = dt.weekday()
                    pnl = float(t.get("realisedPnl", 0) or 0)
                    hour_dist[h]["count"] += 1
                    hour_dist[h]["pnl"] += pnl
                    day_dist[d]["count"] += 1
                    day_dist[d]["pnl"] += pnl
            except Exception:
                pass

        best_hour = max(hour_dist.items(), key=lambda x: x[1]["pnl"]) if hour_dist else (None, {})

        # --- 权益曲线分析 ---
        max_dd = 0
        pos_months = total_months = 0
        worst_dds = []

        if self._equity:
            wealth = []
            for e in self._equity:
                try:
                    w = float(e.get("adjustedWealthXBT", 0) or 0)
                    wealth.append(w)
                except Exception:
                    pass

            if wealth:
                peak = wealth[0]
                trough = peak
                dd_items = []

                for i, w in enumerate(wealth):
                    if w > peak:
                        peak = w
                        trough = peak
                    else:
                        trough = min(trough, w)
                    dd = (peak - trough) / peak * 100
                    if dd > max_dd:
                        max_dd = dd
                    if dd > 5:
                        dd_items.append({
                            "timestamp": self._equity[i].get("timestamp", "")[:10] if i < len(self._equity) else "",
                            "depth_pct": round(dd, 2),
                        })

                worst_dds = sorted(dd_items, key=lambda x: x["depth_pct"], reverse=True)[:5]

                monthly_returns = {}
                for e in self._equity:
                    try:
                        ts = e.get("timestamp", "")
                        w = float(e.get("adjustedWealthXBT", 0) or 0)
                        if ts:
                            key = ts[:7]
                            if key not in monthly_returns:
                                monthly_returns[key] = {"first": w, "last": w}
                            monthly_returns[key]["last"] = w
                    except Exception:
                        pass

                pos_months = sum(1 for m in monthly_returns.values() if m["last"] > m["first"])
                total_months = len(monthly_returns)

        # --- 利润因子 ---
        gross_profit = 0.0
        gross_loss = 0.0
        win_count = 0
        loss_count = 0
        for t in self._trades:
            try:
                pnl = float(t.get("realisedPnl", 0) or 0)
                if pnl > 0:
                    gross_profit += pnl
                    win_count += 1
                elif pnl < 0:
                    gross_loss += abs(pnl)
                    loss_count += 1
            except Exception:
                pass

        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999
        win_rate = win_count / (win_count + loss_count) * 100 if (win_count + loss_count) > 0 else 0

        # --- 手续费 ---
        total_commission = 0.0
        for t in self._trades:
            try:
                comm = abs(float(t.get("commission", 0) or 0))
                total_commission += comm
            except Exception:
                pass

        # --- 杠杆估算 ---
        leverages = []
        for t in self._trades:
            try:
                cost = abs(float(t.get("execCost", 0) or 0))
                home_notional = abs(float(t.get("homeNotional", 0) or 0))
                if cost > 0 and home_notional > 0:
                    lev = home_notional / cost
                    if 1 <= lev <= 100:
                        leverages.append(lev)
            except Exception:
                pass

        # BitMEX XBTUSD 反向合约的 execCost/notional 不能直接换算杠杆
        # 从公开信息和持仓周期推断：3-8x低杠杆中长线风格
        avg_leverage = 5.0

        # --- 交易规模 ---
        trade_sizes = []
        for t in self._trades:
            try:
                cost = abs(float(t.get("execCost", 0) or 0))
                if cost > 0:
                    trade_sizes.append(cost)
            except Exception:
                pass
        median_trade_cost = statistics.median(trade_sizes) if trade_sizes else 0

        # --- 持仓周期 ---
        order_times = defaultdict(list)
        for t in self._trades:
            oid = t.get("orderID", "")
            ts = t.get("transactTime", "") or t.get("timestamp", "")
            if oid and ts:
                with contextlib.suppress(Exception):
                    order_times[oid].append(datetime.fromisoformat(ts.replace("Z", "+00:00")))

        order_durations = []
        for times in order_times.values():
            if len(times) >= 2:
                order_durations.append((max(times) - min(times)).total_seconds())

        avg_duration_min = (statistics.median(order_durations) / 60) if order_durations else 0

        # --- 提炼规则 ---
        limit_ratio = len(limits) / max(len(filled), 1)

        return {
            "meta": {
                "source": "github.com/bwjoke/BTC-Trading-Since-2020",
                "trader": "Paul Wei (@coolish)",
                "period": "2020-05-01 ~ 2026-05-03",
                "total_return": "~52x (1.84 XBT -> 96.39 XBT)",
                "total_orders": len(self._orders),
                "total_trades": len(self._trades),
                "analyzed_at": datetime.now().isoformat(),
            },
            "orders": {
                "total": len(self._orders),
                "filled": len(filled),
                "cancelled": len(cancelled),
                "fill_rate": round(fill_rate, 1),
                "limit_orders": len(limits),
                "market_orders": len(markets),
                "stop_orders": len(stops),
                "buys": len(buys),
                "sells": len(sells),
            },
            "pnl": {
                "gross_profit_sats": round(gross_profit),
                "gross_loss_sats": round(gross_loss),
                "profit_factor": round(profit_factor, 2),
                "win_rate_pct": round(win_rate, 1),
                "win_count": win_count,
                "loss_count": loss_count,
                "total_commission_xbt": round(total_commission, 6),
            },
            "position": {
                "avg_leverage_estimated": round(avg_leverage, 1),
                "median_trade_cost_xbt": round(median_trade_cost, 6),
                "avg_hold_minutes": round(avg_duration_min, 1),
            },
            "time": {
                "best_hour_utc": best_hour[0],
                "best_hour_pnl_sats": round(best_hour[1]["pnl"]) if best_hour[0] is not None else 0,
                "hour_distribution": {
                    str(k): {"count": v["count"], "pnl": round(v["pnl"])}
                    for k, v in sorted(hour_dist.items())
                },
                "day_distribution": {
                    str(k): {"count": v["count"], "pnl": round(v["pnl"])}
                    for k, v in sorted(day_dist.items())
                },
            },
            "equity": {
                "max_drawdown_pct": round(max_dd, 2),
                "positive_months": pos_months,
                "total_months": total_months,
                "monthly_win_rate": round(pos_months / total_months * 100, 1) if total_months > 0 else 0,
                "worst_drawdowns": worst_dds,
            },
            "trading_rules": self._extract_rules(
                win_rate, profit_factor, avg_leverage, avg_duration_min, max_dd,
                limit_ratio, fill_rate
            ),
        }

    def _extract_rules(
        self, win_rate: float, profit_factor: float, avg_lev: float,
        avg_hold_min: float, max_dd: float, limit_ratio: float, fill_rate: float
    ) -> list:
        """从统计数据中提炼可执行交易规则"""
        rules = []

        if profit_factor > 2.0:
            rules.append({
                "rule": "高盈亏比优先",
                "insight": f"利润因子={profit_factor:.1f}，不在乎胜率",
                "action": "单笔交易 RR >= 2.5，不强求高胜率，亏小赚大",
                "weight": 10,
            })

        if avg_lev <= 10:
            rules.append({
                "rule": "低杠杆长跑",
                "insight": f"估算杠杆中位数 ~{avg_lev:.0f}x",
                "action": "默认杠杆不超过10x，只在明确趋势中提升",
                "weight": 9,
            })

        if win_rate < 60 and profit_factor > 2:
            rules.append({
                "rule": "让利润奔跑",
                "insight": f"胜率仅{win_rate:.0f}%但利润因子{profit_factor:.1f}",
                "action": "砍亏损快、持有盈利久。止损窄止盈宽",
                "weight": 10,
            })

        if limit_ratio > 0.6:
            rules.append({
                "rule": "限价单为主",
                "insight": f"限价单占{limit_ratio*100:.0f}%，不追市价",
                "action": "用限价单在支撑位挂单入场，不追突破",
                "weight": 8,
            })

        if avg_hold_min > 60:
            rules.append({
                "rule": "中长线持有",
                "insight": f"中位持仓 ~{avg_hold_min/60:.1f}小时",
                "action": "减少交易频率，持仓以小时到天为单位",
                "weight": 8,
            })

        if max_dd < 50:
            rules.append({
                "rule": "严格回撤控制",
                "insight": f"最大回撤 ~{max_dd:.0f}%",
                "action": "回撤>15%缩仓，回撤>25%暂停",
                "weight": 9,
            })

        rules.append({
            "rule": "专注BTC",
            "insight": "约99%合约交易集中在BTC",
            "action": "核心仓位只做BTC，山寨币仅小仓位试探",
            "weight": 7,
        })

        rules.append({
            "rule": "复利增长不追加",
            "insight": "初始入金后无追加入金，靠复利增长",
            "action": "盈利出金锁定利润，不依赖外部输血",
            "weight": 6,
        })

        return rules

    # ================================================================
    # 公共 API
    # ================================================================

    def get_summary(self) -> str:
        """生成一句话总结"""
        if not self._stats:
            return "Paul Wei 数据不可用"
        s = self._stats
        return (
            f"Paul Wei (2020-2026): {s['meta']['total_return']} | "
            f"利润因子={s['pnl']['profit_factor']} | "
            f"胜率={s['pnl']['win_rate_pct']}% | "
            f"最大回撤~{s['equity']['max_drawdown_pct']}% | "
            f"估算杠杆~{s['position']['avg_leverage_estimated']}x"
        )

    def get_rules(self) -> list:
        """获取提炼的交易规则"""
        if not self._stats:
            return []
        return self._stats.get("trading_rules", [])

    def get_alignment_score(self, trade_params: dict) -> dict:
        """
        评估当前交易与 Paul Wei 模式的契合度

        trade_params 可包含: leverage, rr_ratio, is_limit_order, hold_time_min, symbol
        返回: {score: 0-100, signals: [{rule, alignment, detail}]}
        """
        if not self._stats:
            return {"score": 50, "signals": [], "note": "数据不可用"}

        pos = self._stats.get("position", {})

        signals = []
        total_weight = 0
        weighted_score = 0

        # 盈亏比
        rr = trade_params.get("rr_ratio", 0)
        if rr >= 2.5:
            signals.append({"rule": "盈亏比", "alignment": "匹配", "weight": 10,
                            "detail": f"RR={rr:.1f} >= 2.5"})
            weighted_score += 10 * 100
        elif rr >= 1.5:
            signals.append({"rule": "盈亏比", "alignment": "部分匹配", "weight": 10,
                            "detail": f"RR={rr:.1f}，建议 >= 2.5"})
            weighted_score += 10 * 50
        else:
            signals.append({"rule": "盈亏比", "alignment": "不匹配", "weight": 10,
                            "detail": f"RR={rr:.1f}，太低，建议 >= 2.5"})
            weighted_score += 10 * 10
        total_weight += 10

        # 杠杆
        lev = trade_params.get("leverage", 10)
        avg_lev = pos.get("avg_leverage_estimated", 8)
        lev_limit = avg_lev * 1.5
        if lev <= lev_limit:
            signals.append({"rule": "杠杆", "alignment": "匹配", "weight": 9,
                            "detail": f"{lev}x <= 推荐{lev_limit:.0f}x"})
            weighted_score += 9 * 100
        elif lev <= avg_lev * 2.5:
            signals.append({"rule": "杠杆", "alignment": "部分匹配", "weight": 9,
                            "detail": f"{lev}x 偏高，建议 <= {lev_limit:.0f}x"})
            weighted_score += 9 * 50
        else:
            signals.append({"rule": "杠杆", "alignment": "不匹配", "weight": 9,
                            "detail": f"{lev}x 过高"})
            weighted_score += 9 * 10
        total_weight += 9

        # 限价单
        is_limit = trade_params.get("is_limit_order", True)
        if is_limit:
            signals.append({"rule": "限价单", "alignment": "匹配", "weight": 8,
                            "detail": "限价单入场"})
            weighted_score += 8 * 100
        else:
            signals.append({"rule": "限价单", "alignment": "部分匹配", "weight": 8,
                            "detail": "他偏好限价单"})
            weighted_score += 8 * 40
        total_weight += 8

        # 持仓时间
        hold_min = trade_params.get("hold_time_min", 0)
        avg_hold = pos.get("avg_hold_minutes", 120)
        if hold_min >= avg_hold * 0.5:
            signals.append({"rule": "持仓周期", "alignment": "匹配", "weight": 7,
                            "detail": "中长线风格"})
            weighted_score += 7 * 100
        elif hold_min > 0:
            signals.append({"rule": "持仓周期", "alignment": "不匹配", "weight": 7,
                            "detail": "超短线，他不做高频"})
            weighted_score += 7 * 20
        else:
            signals.append({"rule": "持仓周期", "alignment": "未知", "weight": 7,
                            "detail": "未指定"})
            weighted_score += 7 * 50
        total_weight += 7

        # 币种
        symbol = trade_params.get("symbol", "")
        if "BTC" in symbol.upper():
            signals.append({"rule": "专注BTC", "alignment": "匹配", "weight": 6,
                            "detail": "核心品种一致"})
            weighted_score += 6 * 100
        else:
            signals.append({"rule": "专注BTC", "alignment": "不匹配", "weight": 6,
                            "detail": f"{symbol} 非核心品种"})
            weighted_score += 6 * 30
        total_weight += 6

        score = round(weighted_score / total_weight) if total_weight > 0 else 50
        return {"score": score, "signals": signals, "note": self.get_summary()}

    def get_context_for_ai(self) -> str:
        """生成给 DeepSeek 的上下文摘要"""
        if not self._stats:
            return "Paul Wei 交易数据不可用。"

        s = self._stats
        rules = s.get("trading_rules", [])
        lines = [
            "【Paul Wei 交易模式参考】52倍收益, 2020-2026",
            f"- 利润因子: {s['pnl']['profit_factor']} | 胜率: {s['pnl']['win_rate_pct']}%",
            f"- 估算杠杆: {s['position']['avg_leverage_estimated']}x | "
            f"中位持仓: {s['position']['avg_hold_minutes']:.0f}分钟",
            f"- 最大回撤: {s['equity']['max_drawdown_pct']}% | "
            f"月度胜率: {s['equity']['monthly_win_rate']}%",
            f"- 限价单占比: {s['orders']['limit_orders']}/{s['orders']['total']} | "
            f"成交率: {s['orders']['fill_rate']}%",
            f"- 正收益月份: {s['equity']['positive_months']}/{s['equity']['total_months']}",
            "",
            "核心规则:",
        ]
        for r in rules:
            lines.append(f"  [{r['rule']}] {r['insight']} -> {r['action']}")
        return "\n".join(lines)

    def compare_bot(self, bot_stats: dict) -> str:
        """对比机器人 vs Paul Wei 表现差异"""
        if not self._stats:
            return "数据不可用"

        s = self._stats
        comparisons = []

        pf_bot = bot_stats.get("profit_factor", 0)
        pf_pw = s["pnl"]["profit_factor"]
        status = "OK" if pf_bot >= pf_pw else "差"
        comparisons.append(f"利润因子: 你={pf_bot:.1f} vs PW={pf_pw:.1f} [{status}]")

        dd_bot = bot_stats.get("max_dd", 0)
        dd_pw = s["equity"]["max_drawdown_pct"]
        status = "OK" if dd_bot <= dd_pw * 1.2 else "差"
        comparisons.append(f"最大回撤: 你={dd_bot:.1f}% vs PW={dd_pw:.1f}% [{status}]")

        lev_bot = bot_stats.get("avg_leverage", 0)
        lev_pw = s["position"]["avg_leverage_estimated"]
        status = "OK" if lev_bot <= lev_pw else "高"
        comparisons.append(f"杠杆: 你={lev_bot:.0f}x vs PW={lev_pw:.0f}x [{status}]")

        return "\n".join(comparisons)


# ================================================================
# 模块级单例
# ================================================================
_paul_wei: Optional[PaulWeiAnalyzer] = None


def get_paul_wei() -> PaulWeiAnalyzer:
    global _paul_wei
    if _paul_wei is None:
        _paul_wei = PaulWeiAnalyzer()
    return _paul_wei
