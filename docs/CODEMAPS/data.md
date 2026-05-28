<!-- Generated: 2026-05-28 | Files: 27 | Tokens: ~400 -->

# 数据存储

## SQLite: trades.db

`trade_logger.py` 管理，`DB_PATH = "trades.db"` (CWD相对路径)

### trades 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 |
| time | TEXT | ISO8601时间戳 |
| symbol | TEXT | 交易对 (BTC-USDT-SWAP) |
| direction | TEXT | long/short |
| signal_price | REAL | 信号入场价 |
| entry_price | REAL | 实际成交价 |
| exit_price | REAL | 平仓价 |
| stop_loss | REAL | 止损价 |
| take_profit | REAL | 止盈价 |
| quantity | INTEGER | 合约张数 |
| leverage | INTEGER | 杠杆倍数 |
| pnl | REAL | 盈亏 USDT |
| status | TEXT | open/closed/cancelled |
| strategy | TEXT | 策略来源 |
| signal_id | TEXT | 信号唯一ID |
| close_time | TEXT | 平仓时间 |

### 风险
- **无线程锁** — 多线程并发写入可能 `database is locked`
- `check_same_thread=False` 绕过 Python 检查但 SQLite 本身不保护

## JSON: conversations.json

`conversation_manager.py` 管理，`CONVERSATION_DB_PATH = "conversations.json"`

```json
{
  "qq_2309296843": {
    "created": "2026-05-28T10:00:00",
    "messages": [
      {"role": "user", "content": "...", "time": "..."},
      {"role": "assistant", "content": "...", "time": "..."}
    ]
  }
}
```

- 0.5s 防抖写入，崩溃可能丢最近消息

## 其他持久化

| 数据 | 位置 | 格式 |
|------|------|------|
| .env | 项目根目录 | 环境变量 |
| factor_library.json | factor_miner.py 管理 | 因子数据 |

## .env 关键配置项

```
OKX_DEMO=true          # 模拟/实盘开关
AUTO_TRADE=false       # 自动下单开关
AI_AUTO_START=true     # 启动时自动开启AI引擎
RISK_PER_TRADE=2.0     # 单笔风险%
MAX_POSITIONS=3        # 最大持仓
MAX_DAILY_LOSS=5.0     # 日亏损上限%
```
