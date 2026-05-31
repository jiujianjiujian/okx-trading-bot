"""Webhook 路由 — TradingView 信号入口"""

import hmac

from fastapi import FastAPI, Request, HTTPException, Depends

from ..core.models import TradeSignal
from ..infrastructure.config import WEBHOOK_SECRET, ADMIN_API_TOKEN
from ..shared.di import container


webhook_app = FastAPI(title="Bybit Scalping Bot - Webhook")


async def require_admin(request: Request):
    """后台 API 鉴权"""
    expected = ADMIN_API_TOKEN or WEBHOOK_SECRET
    if not expected:
        raise HTTPException(403, "后台密钥未配置")
    token = request.headers.get("X-Admin-Token", "")
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        token = request.query_params.get("token", "")
    if not hmac.compare_digest(token, expected):
        raise HTTPException(403, "密钥错误")


ADMIN = Depends(require_admin)


def _get_signal_service():
    return container.resolve("signal_service")


@webhook_app.post("/webhook")
async def webhook(request: Request):
    """
    TradingView Webhook → AI 分析 → 3Commas

    TV 警报 JSON 格式:
    {
      "signal": "long",
      "symbol": "BTCUSDT",
      "price": 87000,
      "stop_loss": 86800,
      "take_profit": 87500,
      "strategy": "ScalpBot",
      "interval": "5m",
      "comment": ""
    }
    """
    # 验证密钥
    secret = request.headers.get("X-Webhook-Secret", "")
    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        raise HTTPException(403, "Webhook 密钥错误")

    body = await request.body()
    body_str = body.decode("utf-8")

    # 解析信号
    from signal_parser import parse_tv_webhook
    signal = parse_tv_webhook(body_str)
    if signal is None:
        raise HTTPException(400, "无法解析信号, 检查 JSON 格式")

    # 转换 symbol 为 Bybit 格式
    if not signal.bybit_symbol or signal.bybit_symbol == signal.symbol:
        signal.bybit_symbol = signal.symbol.upper()
        if not signal.bybit_symbol.endswith("USDT"):
            signal.bybit_symbol += "USDT"
        # 替换 OKX 格式
        for suffix in ("-USDT-SWAP", "-USDT"):
            signal.bybit_symbol = signal.bybit_symbol.replace(suffix, "USDT")

    svc = _get_signal_service()
    result = svc.process_tv_signal(signal)
    return result


@webhook_app.get("/webhook/test")
async def webhook_test(_admin=ADMIN):
    """发送测试信号"""
    from signal_parser import TradeSignal as TVSignal

    test = TVSignal(
        symbol="BTCUSDT",
        okx_symbol="BTCUSDT",
        direction="long",
        price=87000.0,
        stop_loss=86800.0,
        take_profit=87500.0,
        strategy="TEST",
        interval="5m",
        comment="测试信号",
        raw_data={"test": True},
    )

    svc = _get_signal_service()
    result = svc.process_tv_signal(test)
    return result
