"""API 路由 — 账户 / 统计 / 风控 / 控制"""

from datetime import datetime

from fastapi import FastAPI, Depends
from fastapi.responses import HTMLResponse

from ..shared.di import container
from ..infrastructure.config import ADMIN_API_TOKEN, WEBHOOK_SECRET
from .webhook_routes import require_admin

api_app = FastAPI(title="Bybit Scalping Bot - API")
ADMIN = Depends(require_admin)


def _get_store():
    return container.resolve("store")

def _get_bybit():
    return container.resolve("bybit")

def _get_signal():
    return container.resolve("signal_service")


@api_app.get("/api/health")
async def health():
    """健康检查"""
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "version": "6.0.0",
    }


@api_app.get("/api/balance")
async def api_balance(_admin=ADMIN):
    """账户余额"""
    bybit = _get_bybit()
    return bybit.get_account_summary()


@api_app.get("/api/positions")
async def api_positions(_admin=ADMIN):
    """当前持仓"""
    bybit = _get_bybit()
    return bybit.get_positions()


@api_app.get("/api/stats")
async def api_stats(_admin=ADMIN):
    """今日交易统计"""
    store = _get_store()
    return store.get_today_stats()


@api_app.get("/api/trades")
async def api_trades(limit: int = 50, _admin=ADMIN):
    """最近交易记录"""
    store = _get_store()
    return store.get_recent_trades(limit)


@api_app.get("/api/decisions")
async def api_decisions(limit: int = 20, _admin=ADMIN):
    """最近 AI 决策"""
    store = _get_store()
    return store.get_recent_decisions(limit)


@api_app.get("/api/risk")
async def api_risk(_admin=ADMIN):
    """风控状态"""
    svc = _get_signal()
    return {"status": svc.get_risk_status()}


@api_app.get("/api/pause")
async def api_pause(_admin=ADMIN):
    """暂停 AI 自主交易"""
    svc = _get_signal()
    svc.stop_loop()
    return {"status": "paused"}


@api_app.get("/api/resume")
async def api_resume(_admin=ADMIN):
    """恢复 AI 自主交易"""
    svc = _get_signal()
    svc.start_loop()
    return {"status": "running"}


@api_app.get("/api/restart")
async def api_restart(_admin=ADMIN):
    """热重启"""
    import os, sys, threading, time
    def _restart():
        time.sleep(1)
        os.execv(sys.executable, [sys.executable, *sys.argv])
    threading.Thread(target=_restart, daemon=True).start()
    return {"status": "restarting"}


@api_app.get("/dashboard")
async def dashboard(_admin=ADMIN):
    """实时仪表盘"""
    bybit = _get_bybit()
    store = _get_store()

    bal = bybit.get_account_summary()
    stats = store.get_today_stats()
    pos = bybit.get_positions()

    pos_html = "".join(
        f"<tr><td>{p.get('symbol','')}</td><td>{p.get('side','')}</td>"
        f"<td>{p.get('size','0')}</td><td>{p.get('avgPrice','0')}</td>"
        f"<td>{p.get('markPrice','0')}</td>"
        f"<td style='color:{'green' if float(p.get('unrealisedPnl',0))>=0 else 'red'}'>"
        f"{float(p.get('unrealisedPnl',0)):+.2f}</td></tr>"
        for p in pos
    ) if pos else "<tr><td colspan=6>无持仓</td></tr>"

    return HTMLResponse(content=f"""<!DOCTYPE html><html><head>
<meta charset=utf-8><meta name=refresh content=30>
<title>Bybit Scalping Bot V6</title>
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
<h1>⚡ Bybit 剥头皮 Bot V6 | 日目标 $50</h1>
<div class=row>
<div class=card>
<h2>💰 账户</h2>
<p>净值: ${bal.get('equity',0):.2f} | 可用: ${bal.get('available',0):.2f}</p>
<p>浮亏: ${bal.get('unrealized_pnl',0):+.2f}</p>
</div>
<div class=card>
<h2>📊 今日</h2>
<p>交易: {stats.get('total_trades',0)} | 胜率: {stats.get('win_rate',0):.0f}%</p>
<p>净利: <b>${stats.get('net_pnl_usdt',0):+.2f}</b> / $50 {'🎯' if stats.get('net_pnl_usdt',0)>=50 else ''}</p>
<p>手续费: ${stats.get('total_fees_usdt',0):.2f}</p>
</div>
</div>
<div class=card>
<h2>📈 持仓 ({len(pos)})</h2>
<table><tr><th>币种</th><th>方向</th><th>数量</th><th>均价</th><th>标记</th><th>浮动</th></tr>{pos_html}</table>
</div>
<div class=card style=text-align:center;color:#444>
自动刷新 | {datetime.now().strftime('%H:%M:%S')}
</div></body></html>""")
