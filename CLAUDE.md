# OKX 合约交易机器人

**启动**: `python main.py` → FastAPI on :8000

**VPS**: `ssh -i ~/.ssh/id_rsa root@43.108.48.96` → `/root/okx-bot/`

## 架构速查 → docs/CODEMAPS/

| 文件 | 何时读取 |
|------|---------|
| [architecture.md](docs/CODEMAPS/architecture.md) | 理解系统结构、数据流 |
| [modules.md](docs/CODEMAPS/modules.md) | 定位代码所在模块 |
| [dependencies.md](docs/CODEMAPS/dependencies.md) | API/服务/依赖 |
| [data.md](docs/CODEMAPS/data.md) | 数据库/持久化 |

## 关键约束

- 模拟盘 `OKX_DEMO=true`，清算 WebSocket 走 `wspap.okx.com`
- VPS 直连 OKX/Telegram 不需代理，本地需要
- `.env` 含真实凭据，禁止提交
- auto_trader.py 114KB，使用 codemap 定位，避免全量加载

## 常用命令

```bash
# 本地检查
python -m ruff check .
# 部署
scp -i ~/.ssh/id_rsa *.py root@43.108.48.96:/root/okx-bot/
ssh -i ~/.ssh/id_rsa root@43.108.48.96 'cd /root/okx-bot && git pull --ff-only && ./venv/bin/python -m compileall -q . && systemctl restart okx-bot'
```
