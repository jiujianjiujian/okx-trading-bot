<!-- Generated: 2026-05-28 | Files: 27 | Tokens: ~500 -->

# 外部依赖与集成

## API 服务

| 服务 | 用途 | 配置项 | 网络要求 |
|------|------|--------|---------|
| **OKX API v5** | 交易/行情/账户 | OKX_API_KEY/SECRET/PASSPHRASE | 直连(VPS) 或 HTTP代理(国内) |
| **OKX WebSocket** | 清算数据 | 无(公开) | ws://ws.okx.com 生产 / wspap.okx.com 模拟 |
| **Telegram Bot API** | 通知/指令交互 | TELEGRAM_BOT_TOKEN/CHAT_ID | 需代理(国内) |
| **NapCatQQ** | QQ消息收发 | QQ_WS_PORT/TOKEN | 本地WebSocket |
| **DeepSeek API** | AI复盘/自主决策 | DEEPSEEK_API_KEY | 直连(国内) |
| **Anthropic API** | QQ自由对话 | ANTHROPIC_API_KEY/MODEL | 需代理(国内) |

## Python 依赖

```
fastapi>=0.115.0        # Web框架
uvicorn>=0.34.0         # ASGI服务器
python-telegram-bot>=21 # Telegram SDK
python-dotenv>=1.0      # 环境变量
requests>=2.31          # HTTP客户端 (行情爬取)
pysocks/socksio         # SOCKS代理支持
anthropic>=0.39         # Claude SDK
websockets>=12          # 异步WebSocket (清算/QQ)
websocket-client>=1.8   # 同步WebSocket
httpx>=0.27             # HTTP客户端 (OKX API)
```

## 外部数据源 (auto_trader 调用)

| 来源 | API | 用途 | 频率 |
|------|-----|------|------|
| OKX Public | /api/v5/market/tickers | 全量SWAP行情 | 5s/次 |
| OKX Public | /api/v5/market/candles | K线数据 (多周期) | 按需 |
| OKX Public | /api/v5/market/books | 订单簿深度 | 按需 |
| OKX Public | /api/v5/public/funding-rate | 资金费率 | 按需 |
| OKX Public | /api/v5/public/open-interest | 持仓量 | 按需 |
| CoinGecko | /api/v3/coins/bitcoin | BTC市值 | 按需 |
| Coinbase | BTC/USD | 替代价格源 | 按需 |
| Binance | BTC/USDT | 替代价格源 | 按需 |
| Bybit | BTC/USDT | 替代价格源 | 按需 |

## 部署环境

- **VPS**: 阿里云 `43.108.48.96`，路径 `/root/okx-bot/`
- **本地**: Windows 11，路径 `D:\ClaudeCode\workspace\okx-trading-bot\`
- **Python**: 3.12+
- **SSH**: `~/.ssh/id_rsa` (主), `~/.ssh/vps_key` (备)
