"""
DeepSeek AI 每日复盘模块 (Pro 模式)

每日调用 DeepSeek API:
  1. 拉取当日所有交易数据
  2. 发给 DeepSeek 做深度复盘
  3. 结合当前参数给出优化建议
"""


import http_wrapper as requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, PROXY_URL
from strategy_analyzer import StrategyAnalyzer


class DeepSeekReviewer:
    """DeepSeek AI 复盘引擎"""

    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL
        self.proxies = {"https": PROXY_URL} if PROXY_URL else None
        self.kline = StrategyAnalyzer()

    # ----------------------------------------------------------------
    # DeepSeek API 调用
    # ----------------------------------------------------------------

    def _call_deepseek(self, system_prompt: str, user_prompt: str, max_tokens: int = 2048) -> str:
        """调用 DeepSeek API (OpenAI 兼容接口)"""
        if not self.api_key:
            return "DeepSeek API Key 未配置"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.7,
        }

        try:
            r = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                proxies=self.proxies,
                timeout=60,
            )
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            return f"AI 分析请求失败: {e}"

    # ----------------------------------------------------------------
    # 每日复盘
    # ----------------------------------------------------------------

    def daily_review(self, trades: list) -> str:
        """
        每日复盘: 分析当日交易 + 参数优化建议

        包含:
          1. 交易绩效回顾
          2. 策略参数优化建议
          3. 风险控制建议
        """
        if not trades:
            return "本日无交易记录"

        from config import RISK_PER_TRADE, DEFAULT_LEVERAGE, MAX_POSITIONS, MAX_DAILY_LOSS

        trade_list = []
        total_pnl = 0
        wins = 0
        losses = 0
        max_win = 0
        max_loss = 0

        for t in trades:
            pnl = t.get("pnl", 0) or 0
            total_pnl += pnl
            if pnl > 0:
                wins += 1
                max_win = max(max_win, pnl)
            else:
                losses += 1
                max_loss = min(max_loss, pnl)
            trade_list.append(
                f"• {t['symbol']} {t['direction']} "
                f"入场{t['entry_price']:.0f} → 出场{t['exit_price']:.0f} "
                f"盈亏{pnl:+.2f}USDT"
            )

        total_trades = wins + losses
        win_rate = wins / total_trades * 100 if total_trades > 0 else 0
        avg_win = sum(t.get("pnl", 0) or 0 for t in trades if (t.get("pnl", 0) or 0) > 0)
        avg_win = avg_win / wins if wins > 0 else 0
        avg_loss = sum(abs(t.get("pnl", 0) or 0) for t in trades if (t.get("pnl", 0) or 0) < 0)
        avg_loss = avg_loss / losses if losses > 0 else 0

        system_prompt = (
            "你是一位专业的加密货币量化交易顾问。请用中文进行每日复盘。\n\n"
            "分析框架:\n"
            "1. 绩效概览 (胜率、盈亏比、最大盈亏)\n"
            "2. 策略执行评估 (信号质量、入场/出场时机)\n"
            "3. 参数优化建议 (具体数值建议)\n"
            "4. 风险控制评估\n\n"
            "重点: 结合用户的当前参数给出可落地的优化方案。"
            "如果今日亏损，着重分析原因和改进方向。"
            "控制在 500 字以内。"
        )

        user_prompt = (
            f"=== 今日交易数据 ===\n"
            f"总笔数: {total_trades} | 胜: {wins} | 负: {losses} | 胜率: {win_rate:.0f}%\n"
            f"总盈亏: {total_pnl:+.2f} USDT\n"
            f"最大单笔盈利: {max_win:+.2f} | 最大单笔亏损: {max_loss:+.2f}\n"
            f"平均盈利: {avg_win:+.2f} | 平均亏损: {avg_loss:+.2f}\n\n"
            f"=== 当前策略参数 ===\n"
            f"单笔风险: {RISK_PER_TRADE}% | 杠杆: {DEFAULT_LEVERAGE}x\n"
            f"最大持仓: {MAX_POSITIONS} | 日内止损线: {MAX_DAILY_LOSS}%\n\n"
            f"=== 交易明细 ===\n" +
            "\n".join(trade_list) + "\n\n"
            "请给出每日复盘报告，并针对以上参数提出优化建议。"
        )

        return self._call_deepseek(system_prompt, user_prompt, max_tokens=1536)

    # ----------------------------------------------------------------
    # 辅助
    # ----------------------------------------------------------------

    @staticmethod
    def _build_kline_summary(candles: list) -> str:
        """构建 K 线文字摘要"""
        if not candles:
            return "无数据"

        opens = [c["open"] for c in candles]
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        volumes = [c["volume"] for c in candles]

        price_start = opens[0]
        price_end = closes[-1]
        price_high = max(highs)
        price_low = min(lows)
        change_pct = (price_end - price_start) / price_start * 100

        lines = [
            f"时间范围: {len(candles)} 根 1H K线",
            f"价格区间: ${price_low:,.1f} - ${price_high:,.1f}",
            f"涨跌幅: {change_pct:+.2f}%",
            f"开盘→收盘: {price_start:,.1f} → {price_end:,.1f}",
            f"平均成交量: {sum(volumes)/len(volumes):,.0f} USDT/根",
        ]

        # 关键 K 线形态描述
        if len(candles) >= 14:
            # 找最近的大阳/大阴线
            body_ratios = [
                abs(c["close"] - c["open"]) / abs(c["high"] - c["low"])
                if c["high"] != c["low"] else 0
                for c in candles[-14:]
            ]
            max_body_idx = body_ratios.index(max(body_ratios))
            c = candles[-14 + max_body_idx]
            direction = "大阳线" if c["close"] > c["open"] else "大阴线"
            lines.append(f"关键K线: {direction} @ ${c['close']:.1f} (实休占比{max(body_ratios)*100:.0f}%)")

        return "\n".join(lines)

    # ----------------------------------------------------------------
    # Telegram 格式化
    # ----------------------------------------------------------------

    @staticmethod
    def format_report(trade: dict, ai_report: str) -> str:
        """生成每日复盘报告"""
        emoji = "🟢" if trade.get("pnl", 0) >= 0 else "🔴"
        return f"{emoji} *DeepSeek 每日复盘*\n\n{ai_report}"
