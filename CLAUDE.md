# Bybit 剥头皮交易机器人 V6

AI 驱动的剥头皮交易系统: DeepSeek 分析 → 3Commas Webhook 执行 → Bybit 成交。

**启动**: `python main.py` → FastAPI on :8000
**目标**: 日净利 $50 USDT (扣手续费后)
**VPS**: `ssh -i ~/.ssh/id_rsa root@43.108.48.96` → `/root/okx-bot/`

## 架构速查

```
Market Data (Bybit) → AI Decision (DeepSeek) → Scalp Parameters
       → 3Commas Webhook → Bybit Execution → PnL Tracking
       → Telegram Notifications
```

| 目录 | 职责 |
|------|------|
| `src/core/` | 数据模型、枚举、接口协议 |
| `src/services/` | 业务: 分析/决策/剥头皮/风控/信号/报告 |
| `src/infrastructure/` | 外部: Bybit/3Commas/DeepSeek/SQLite/日志 |
| `src/interfaces/` | 入口: Webhook/API/仪表盘/Telegram |
| `tests/` | 测试 suite |

## 核心模块

| 模块 | 用途 |
|------|------|
| `analysis_service.py` | 纯函数技术指标 (SMA/EMA/RSI/MACD/ATR 等) |
| `market_service.py` | Bybit 行情聚合 + 多周期 K 线 + 选币 |
| `decision_service.py` | 双 AI 校验: 快速+深度 DeepSeek |
| `scalping_service.py` | ATR 自适应 SL/TP + 仓位 + 手续费计算 |
| `risk_service.py` | 风控: 日亏/连亏/黑天鹅/币种冷却 |
| `signal_service.py` | 信号处理 + AI 主循环 + PnL 同步 |
| `report_service.py` | 消息模板 (Telegram 格式) |

## 配置

所有 .env 变量见 `.env.example`，关键项:
- `BYBIT_DEMO=true` 测试网
- `THREECOMMAS_BOT_ID` / `THREECOMMAS_EMAIL_TOKEN` 3Commas Signal Bot
- `DAILY_TARGET_USDT=50` 日目标
- `SCALP_SL_PCT_MIN/MAX` 止损范围 0.15-0.50%
- `SCALP_TP_PCT_MIN/MAX` 止盈范围 0.30-1.50%
- `ESTIMATED_FEE_PCT=0.11` 双向手续费

## 常用命令

```bash
# 测试
python -m pytest tests/ -v
# 本地运行
python main.py
# 部署 VPS
scp -r src main.py .env root@43.108.48.96:/root/okx-bot/
ssh root@43.108.48.96 'cd /root/okx-bot && systemctl restart okx-bot'
```
