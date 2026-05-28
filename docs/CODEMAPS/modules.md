<!-- Generated: 2026-05-28 | Files: 27 | Tokens: ~900 -->

# 模块详解

## main.py (16K) — FastAPI 入口

```
GET  /           → 重定向 /dashboard
GET  /dashboard  → HTML 面板
GET  /api/state  → 系统状态 JSON
POST /webhook    → TradingView 信号处理
POST /api/restart → 热重启
POST /api/stop   → 停止自动交易
```

初始化顺序: `OKXClient → RiskManager → TradeLogger → StrategyAnalyzer → DeepSeekReviewer → AutoTrader → TelegramBot → QQBot`

## auto_trader.py (114K) — AI 自主交易引擎

核心类 `AutoTrader`，主循环 `run_loop()` 每 5s 一次:

```
_candidate_coins() → 筛选候选币种 (全量SWAP行情 → 排序)
  ↓
execute(d) → 单币种分析:
  1. _market_structure()  → 市场微观结构 (买卖深度/价差/流动性)
  2. _market_regime()     → 宏观环境 (BTC主导/黑天鹅/时段)
  3. _multi_timeframe()   → 多周期分析 (1H/15m/5m K线)
  4. _smc_analysis()      → SMC 订单流分析 (FVG/流动性猎杀)
  5. _onchain_signal()    → 链上信号 (资金费率/OI/持仓变化)
  6. _dual_ai_decision()  → 双 AI 校验 (快速+深度)
  7. _pre_trade_checks()  → 强制规则检查 (日亏/连亏/关联性)
```

关键方法:
- `execute(d, send)` — 决策→下单完整流程，B:1882
- `_dual_ai_decision(snapshot)` — 快速AI+深度AI双向校验，B:1140
- `daily_optimize()` — 动态参数优化 (时段/波动自适应)，B:2140

## telegram_bot.py (17K) — Telegram Bot

`TelegramBot` 类，`python-telegram-bot` 库:
- 命令: `/start /help /balance /positions /close /review /market /settings /start_ai /stop_ai /scan`
- 通知: `send()` 方法全局发送，`send_admin()` 仅管理员

## qq_bot.py (24K) — QQ Bot (NapCatQQ)

`QQBot` 类，OneBot v11 反向 WebSocket:
- 接收 QQ 消息 → 解析指令 → 调用同上业务方法
- 需在 VPS 上运行 NapCatQQ 客户端

## signal_parser.py (6K) — 信号解析

```python
parse_tv_webhook(body: str) → TradeSignal | None
  ├── JSON 解析 → _parse_standard_format / _parse_tv_default_format
  └── 纯文本回退 → _parse_text_signal / _parse_guess_format
```

TradeSignal 字段: `symbol, price, direction, stop_loss, take_profit, strategy, interval`

## strategy_analyzer.py (6K) — 技术分析

```python
StrategyAnalyzer.analyze(signal) → AnalysisResult
  评分维度: EMA趋势(25) + RSI(15) + 布林带(10) + 成交量(10) + 支撑阻力(10)
```
通过线: 60分

## okx_client.py (10K) — OKX API 客户端

封装 httpx Client，所有 OKX v5 API 调用:
- `get_positions/set_leverage/place_order/cancel_order/amend_order`
- `get_account_summary/get_instrument_info/get_market_data`
- 签名: HMAC-SHA256，时间戳 ISO8601

## deepseek_analyzer.py (7K) — AI 复盘

`DeepSeekReviewer.daily_review(today_trades)` → 生成每日复盘报告

## claude_chat.py (6K) — AI 对话

`ClaudeChat` — 支持 Anthropic + DeepSeek 双后端，供 QQ Bot 自由对话

## 辅助模块

| 文件 | 行 | 功能 |
|------|-----|------|
| risk_manager.py | 124 | 仓位计算 + 日亏损检查 |
| trade_logger.py | 183 | SQLite 交易日志 |
| watchdog.py | 122 | VPS 守护：存活检测 + 云助手修复 |
| liquidation_tracker.py | 189 | 实时清算数据 (websocket) |
| http_wrapper.py | 61 | 代理HTTP封装 |
| conversation_manager.py | 84 | 会话持久化 (JSON) |
| config.py | 79 | .env 配置加载 |
| factor_miner.py | — | 因子发现引擎 |
| backtest_engine.py | — | 回测引擎 |
| paul_wei_analyzer.py | — | Paul Wei 分析器 |
