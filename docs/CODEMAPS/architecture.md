<!-- Generated: 2026-05-28 | Files: 27 | Tokens: ~600 -->

# 系统架构

## 顶层数据流

```
TradingView → Webhook(/webhook) → signal_parser → strategy_analyzer
                                                      ↓
                                               risk_manager ←→ trade_logger
                                                      ↓
Telegram ← telegram_bot ← main.py → auto_trader → okx_client → OKX API
   ↑                                         ↓
QQ ← qq_bot                            deepseek_analyzer
                                    claude_chat (自由对话)
```

## 双模式

| 模式 | 触发器 | 决策路径 |
|------|--------|---------|
| **信号跟单** | TradingView Webhook | parse → analyze → 风控 → OKX 下单 |
| **AI 自主** | auto_trader.run_loop | 双 AI 分析行情 → 策略打分 → OKX 限价单 |

## 入口点

- `main.py` — FastAPI 服务，`python main.py` 启动
- `/webhook` — TradingView 信号入口
- `/dashboard` — HTML 仪表盘
- `auto_trader.py` — AI 自主交易引擎（独立线程）

## 服务边界

| 层 | 模块 | 职责 |
|----|------|------|
| **接口** | main.py | HTTP 路由、生命周期管理 |
| **通知** | telegram_bot.py, qq_bot.py | 双通道通知（Telegram + QQ） |
| **信号** | signal_parser.py | 解析 TradingView JSON/文本信号 |
| **分析** | strategy_analyzer.py, deepseek_analyzer.py | 技术分析 + AI 复盘 |
| **交易** | auto_trader.py, okx_client.py | 交易执行 + OKX API 封装 |
| **风控** | risk_manager.py, watchdog.py | 仓位管理 + VPS 守护 |
| **数据** | trade_logger.py, conversation_manager.py | SQLite 存储 + 对话管理 |

## 并发模型

```
FastAPI uvicorn (asyncio)
  ├── telegram_bot.py  → threading (polling)
  ├── qq_bot.py        → websockets (反向WS服务器)
  ├── auto_trader.py   → threading (主循环 5s)
  └── main.py          → threading (仓位日志 5min)
```
