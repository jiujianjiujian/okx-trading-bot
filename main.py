"""
OKX 合约交易机器人 - 主程序入口

架构:
  TradingView 警报 → Webhook → 信号解析 → 风控检查 → OKX下单 → Telegram通知

启动:
  python main.py
"""

import contextlib
import asyncio
import hmac
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse

# Windows 终端 UTF-8 编码兼容
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from config import (
    WEBHOOK_PORT,
    WEBHOOK_SECRET,
    AUTO_TRADE,
    DEFAULT_LEVERAGE,
    DB_PATH,
    QQ_BOT_ENABLED,
    ANTHROPIC_API_KEY,
    ANTHROPIC_MODEL,
    ANTHROPIC_MAX_TOKENS,
    CONVERSATION_DB_PATH,
    MAX_CONVERSATION_HISTORY,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    AI_AUTO_START,
    ADMIN_API_TOKEN,
)
from signal_parser import parse_tv_webhook, TradeSignal
from risk_manager import RiskManager
from trade_logger import TradeLogger
from okx_client import OKXClient
from telegram_bot import TelegramBot
from strategy_analyzer import StrategyAnalyzer
from deepseek_analyzer import DeepSeekReviewer
from auto_trader import AutoTrader
from qq_bot import QQBot
from conversation_manager import ConversationManager
from claude_chat import ClaudeChat


def acquire_instance_lock(path: str = ".okx_bot.lock"):
    """Prevent duplicate bot processes from running the trading loop."""
    lock_file = open(path, "a+", encoding="utf-8")  # noqa: SIM115 - lock must stay open
    try:
        lock_file.seek(0)
        if not lock_file.read(1):
            lock_file.write(" ")
            lock_file.flush()
        lock_file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        lock_file.close()
        raise RuntimeError("已有 OKX Trading Bot 实例在运行，拒绝重复启动") from exc

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


INSTANCE_LOCK = None
if __name__ == "__main__":
    INSTANCE_LOCK = acquire_instance_lock()


# ============================================================
# 初始化模块
# ============================================================

okx = OKXClient()
risk_mgr = RiskManager()
logger = TradeLogger()
analyzer = StrategyAnalyzer()
reviewer = DeepSeekReviewer()
auto_trader = AutoTrader()
telegram = TelegramBot()
telegram.set_dependencies(okx, logger, reviewer, auto_trader)

# ---- QQ Bot + Claude 对话 ----
conversation_mgr = ConversationManager(
    db_path=CONVERSATION_DB_PATH,
    max_history=MAX_CONVERSATION_HISTORY,
)
claude_chat = ClaudeChat(
    anthropic_key=ANTHROPIC_API_KEY,
    deepseek_key=DEEPSEEK_API_KEY,
    deepseek_base=DEEPSEEK_BASE_URL,
    model=ANTHROPIC_MODEL,
    max_tokens=ANTHROPIC_MAX_TOKENS,
)
qq_bot = QQBot()
qq_bot.set_dependencies(
    okx=okx,
    logger=logger,
    reviewer=reviewer,
    auto_trader=auto_trader,
    claude=claude_chat,
    conversation_mgr=conversation_mgr,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动 Telegram Bot"""
    print("=" * 50)
    print("🚀 OKX 合约交易机器人 启动中...")
    print(f"   模拟盘: {'是' if okx.demo else '否'}")
    print(f"   自动交易: {'开启' if AUTO_TRADE else '关闭(仅通知)'}")
    print(f"   数据库: {DB_PATH}")
    print(f"   Webhook: http://0.0.0.0:{WEBHOOK_PORT}/webhook")
    print("=" * 50)

    # 启动 Telegram 后台线程
    telegram.start()

    # 启动 QQ Bot (OneBot v11 Reverse WebSocket 服务器)
    if QQ_BOT_ENABLED:
        qq_bot.start()

    # 自动启动 AI 自主交易
    if AI_AUTO_START:
        def ai_notify(msg):
            """AI 引擎通知：尝试 Telegram，失败则打日志"""
            with contextlib.suppress(Exception):
                telegram.send(msg)
            with contextlib.suppress(Exception):
                qq_bot.send(msg)
            print(f"[AI] {msg}")
        await asyncio.sleep(2)
        auto_trader.start(ai_notify)
        print("[启动] AI 自主交易引擎已自动启动")

    # 仓位日志监控（每5分钟记录持仓状态）
    monitor_active = True

    def position_logger():
        while monitor_active:
            try:
                positions = okx.get_positions()
                if positions:
                    pids = [f"{p['instId']}({p['side']})" for p in positions]
                    print(f"[持仓] {', '.join(pids)}")
            except Exception:
                pass
            time.sleep(300)

    threading.Thread(target=position_logger, daemon=True).start()

    # 等待 Telegram 初始化
    await asyncio.sleep(3)
    print("[启动完成] 请在 Telegram 给 @okx_trading_assistant_bot 发 /start 激活通知")

    yield

    monitor_active = False
    print("机器人已停止")
    qq_bot.stop()
    conversation_mgr.force_save()
    logger.close()


app = FastAPI(title="OKX Trading Bot", lifespan=lifespan)


async def require_admin(request: Request):
    """保护账户查询、重启和仪表盘等后台接口。"""
    expected = ADMIN_API_TOKEN or WEBHOOK_SECRET
    if not expected:
        raise HTTPException(403, "后台访问密钥未配置")

    token = request.headers.get("X-Admin-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("token", "")

    if not hmac.compare_digest(token, expected):
        raise HTTPException(403, "后台访问密钥错误")


ADMIN_DEP = Depends(require_admin)


# ============================================================
# Webhook 端点 - TradingView 信号入口
# ============================================================

@app.post("/webhook")
async def webhook(request: Request):
    """
    接收 TradingView 警报 Webhook

    TV 警报设置:
      - Webhook URL: http://你的服务器IP:8000/webhook
      - 消息格式: JSON (见下方示例)

    JSON 格式示例:
    {
      "signal": "long",
      "symbol": "BTCUSDT",
      "price": 76800,
      "stop_loss": 76000,
      "take_profit": 79000,
      "strategy": "LuxAlgo",
      "interval": "1h",
      "comment": "趋势反转信号"
    }
    """
    # 1. 验证密钥
    secret = request.headers.get("X-Webhook-Secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(403, "Webhook 密钥错误")

    # 2. 读取请求体
    body = await request.body()
    body_str = body.decode("utf-8")
    print(f"\n📩 收到 Webhook: {body_str[:200]}")

    # 3. 解析信号
    signal = parse_tv_webhook(body_str)
    if signal is None:
        raise HTTPException(400, "无法解析信号")

    # 4. 记录信号到数据库
    signal_id = logger.log_signal(signal)

    # 5. 策略分析（静默，不通知）
    analysis = analyzer.analyze(signal)
    logger.update_signal_status(signal_id, f"score_{analysis.score}")

    # 6. 未通过分析 → 静默拒绝（不通知，非交易事件无需打扰）
    if not analysis.passed:
        return {"status": "rejected", "score": analysis.score, "reason": analysis.summary}

    # 7. 如果未开启自动交易
    if not AUTO_TRADE:
        return {
            "status": "analyzed",
            "score": analysis.score,
            "message": "信号通过分析，自动交易未开启",
            "reasons": analysis.reasons,
        }

    # 10. 处理交易
    result = await process_trade_signal(signal, signal_id)
    result["score"] = analysis.score
    return result


@app.get("/webhook/test")
async def webhook_test(_admin=ADMIN_DEP):
    """测试端点：发送一个模拟信号"""
    test_signal = TradeSignal(
        symbol="BTCUSDT",
        okx_symbol="BTC-USDT-SWAP",
        direction="long",
        price=76800.0,
        stop_loss=76000.0,
        take_profit=79000.0,
        strategy="测试信号",
        interval="1h",
        comment="这是一条测试信号",
        raw_data={"test": True},
    )

    signal_id = logger.log_signal(test_signal)
    telegram.notify_signal(test_signal)

    # 测试也跑分析
    analysis = analyzer.analyze(test_signal)
    logger.update_signal_status(signal_id, f"score_{analysis.score}")
    report = StrategyAnalyzer.format_report(test_signal, analysis)
    telegram.send(report)

    return {
        "status": "test",
        "signal_id": signal_id,
        "score": analysis.score,
        "passed": analysis.passed,
        "reasons": analysis.reasons,
        "warnings": analysis.warnings,
        "signal": {
            "symbol": test_signal.okx_symbol,
            "direction": test_signal.direction,
            "price": test_signal.price,
        },
    }


# ============================================================
# API 端点 - 查询
# ============================================================

@app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "demo": okx.demo,
        "auto_trade": AUTO_TRADE,
    }


@app.get("/api/restart")
async def api_restart(_admin=ADMIN_DEP):
    """热重启机器人（加载最新代码）"""
    def _restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable, *sys.argv])
    threading.Thread(target=_restart, daemon=True).start()
    return {"status": "restarting"}

@app.get("/api/balance")
async def api_balance(_admin=ADMIN_DEP):
    """查询账户余额"""
    return okx.get_balance()


@app.get("/api/positions")
async def api_positions(_admin=ADMIN_DEP):
    """查询当前持仓"""
    return okx.get_positions()


@app.get("/api/trades")
async def api_trades(_admin=ADMIN_DEP):
    """查询今日交易"""
    return logger.get_today_trades()


@app.get("/api/stats")
async def api_stats(_admin=ADMIN_DEP):
    """查询交易统计"""
    return logger.get_trade_stats()


@app.get("/api/ai-decisions")
async def api_ai_decisions(limit: int = 100, _admin=ADMIN_DEP):
    """查询最近 AI 扫描/观望原因"""
    return logger.get_recent_ai_decisions(limit)


@app.get("/api/ai-decision-stats")
async def api_ai_decision_stats(hours: int = 24, _admin=ADMIN_DEP):
    """统计 AI 扫描通过率和主要观望原因"""
    return logger.get_ai_decision_stats(hours)


@app.get("/dashboard")
async def dashboard(_admin=ADMIN_DEP):
    """实时仪表盘 HTML"""
    balance = okx.get_balance()
    positions = okx.get_positions()
    stats = logger.get_trade_stats()

    pos_html = "".join(
        f"<tr><td>{p['instId']}</td><td>{p['side']}</td>"
        f"<td>{p['quantity']}</td><td>{p['avgPx']:.1f}</td>"
        f"<td>{p['markPx']:.1f}</td><td style='color:{'green' if p['upl']>=0 else 'red'}'>"
        f"{p['upl']:+.2f}</td></tr>"
        for p in positions
    ) if positions else "<tr><td colspan=6>无持仓</td></tr>"

    return HTMLResponse(content=f"""<!DOCTYPE html><html><head>
<meta charset=utf-8><meta name=refresh content=30>
<title>OKX AI Trading Bot</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:monospace;background:#0a0a0a;color:#0f0;padding:20px}}
h1{{color:#0f0;margin-bottom:10px;font-size:20px}}
.card{{background:#111;border:1px solid#333;border-radius:8px;padding:15px;margin:10px 0}}
.card h2{{font-size:14px;color:#0a0;margin-bottom:8px}}
.row{{display:flex;gap:10px;flex-wrap:wrap}}
.row>.card{{flex:1;min-width:200px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th,td{{padding:6px 8px;text-align:left;border-bottom:1px solid#222}}
th{{color:#0a0}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px}}
.badge-g{{background:#003300;color:#0f0}} .badge-r{{background:#330000;color:#f00}}
</style></head><body>
<h1> AI Trading Bot V4</h1>
<div class=row>
<div class=card>
<h2> 账户</h2>
<p>净值: {balance.get('equity',0):.2f} USDT | 可用: {balance.get('available',0):.2f}</p>
<p>浮动盈亏: {balance.get('unrealized_pnl',0):+.2f} USDT</p>
</div>
<div class=card>
<h2> 统计</h2>
<p>总交易: {stats.get('total_trades',0)} | 胜率: {stats.get('win_rate','N/A')}</p>
<p>总盈亏: {stats.get('total_pnl','0')}</p>
</div>
</div>
<div class=card>
<h2> 持仓 ({len(positions)})</h2>
<table><tr><th>币种</th><th>方向</th><th>张数</th><th>均价</th><th>标记</th><th>浮动</th></tr>{pos_html}</table>
</div>
<div class=card style=text-align:center;color:#444>
自动刷新 | {datetime.now().strftime('%H:%M:%S')}
</div></body></html>""")


# ============================================================
# 核心交易逻辑
# ============================================================

async def process_trade_signal(signal: TradeSignal, signal_id: int):
    """处理交易信号的完整流程"""

    # ---- Step 1: 获取账户状态 ----
    balance = okx.get_balance()
    if "error" in balance:
        msg = f"获取余额失败: {balance['error']}"
        telegram.notify_reject(signal, msg)
        qq_bot.notify_reject(signal, msg)
        logger.update_signal_status(signal_id, "error")
        return {"status": "error", "message": msg}

    equity = balance.get("equity", 0)
    positions = okx.get_positions()

    # ---- Step 2: 风控检查 ----
    ok, reason = risk_mgr.validate_signal(signal, equity, len(positions))
    if not ok:
        telegram.notify_reject(signal, reason)
        qq_bot.notify_reject(signal, reason)
        logger.update_signal_status(signal_id, "rejected")
        return {"status": "rejected", "message": reason}

    # ---- Step 3: 检查是否已有同币种仓位 ----
    if okx.has_position(signal.okx_symbol):
        msg = f"已持有 {signal.okx_symbol} 仓位，跳过重复开仓"
        telegram.notify_reject(signal, msg)
        qq_bot.notify_reject(signal, msg)
        logger.update_signal_status(signal_id, "rejected")
        return {"status": "rejected", "message": msg}

    # ---- Step 4: 获取市价（如果信号价格与市价偏差太大则告警） ----
    market_price = okx.get_market_price(signal.okx_symbol)
    if market_price > 0:
        deviation = abs(signal.price - market_price) / market_price
        signal.price = market_price  # 用市价更新
        if deviation > 0.02:  # 偏差超过 2%
            print(f"⚠️ 信号价与市价偏差: {deviation:.2%}")

    # ---- Step 5: 计算仓位大小 ----
    leverage = DEFAULT_LEVERAGE
    instrument = okx.get_instrument_info(signal.okx_symbol)
    quantity, notional = risk_mgr.calculate_position_size(
        equity,
        signal.price,
        signal.stop_loss,
        contract_value=instrument.get("ctVal", 0.001),
        min_size=instrument.get("minSz", 1),
        lot_size=instrument.get("lotSz", 1),
    )
    if quantity <= 0:
        msg = "仓位计算异常"
        telegram.notify_reject(signal, msg)
        qq_bot.notify_reject(signal, msg)
        logger.update_signal_status(signal_id, "error")
        return {"status": "error", "message": msg}

    # ---- Step 6: 设置杠杆 ----
    ok, err = okx.set_leverage(signal.okx_symbol, leverage)
    if not ok:
        msg = f"设置杠杆失败: {err}"
        telegram.notify_reject(signal, msg)
        qq_bot.notify_reject(signal, msg)
        logger.update_signal_status(signal_id, "error")
        return {"status": "error", "message": msg}

    # ---- Step 7: 下单 ----
    ok, err, order_id = okx.place_order(
        signal.okx_symbol, signal.direction, quantity,
        stop_loss=signal.stop_loss,
        take_profit=signal.take_profit,
        ord_type="market",
    )
    if not ok:
        msg = f"下单失败: {err}"
        telegram.notify_reject(signal, msg)
        qq_bot.notify_reject(signal, msg)
        logger.update_signal_status(signal_id, "error")
        return {"status": "error", "message": msg}

    # ---- Step 8: 记录交易 ----
    trade_id = logger.log_trade(signal_id, signal, quantity, leverage, order_id)
    logger.update_signal_status(signal_id, "executed")

    # ---- Step 9: 通知成功 ----
    telegram.notify_trade(signal, quantity, leverage, order_id)
    qq_bot.notify_trade(signal, quantity, leverage, order_id)

    print(f"✅ 交易完成: {signal.okx_symbol} {signal.direction} {quantity}张 | 订单: {order_id}")
    return {
        "status": "executed",
        "message": "下单成功",
        "order_id": order_id,
        "trade_id": trade_id,
        "details": {
            "symbol": signal.okx_symbol,
            "direction": signal.direction,
            "quantity": quantity,
            "leverage": leverage,
            "notional": f"{notional:.2f} USDT",
        },
    }


# ============================================================
# 主程序入口
# ============================================================

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=WEBHOOK_PORT,
        log_level="info",
    )
