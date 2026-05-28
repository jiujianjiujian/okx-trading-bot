"""
TradingView Webhook 信号解析器
支持多种信号格式，统一转换成内部格式
"""

import json
import re
from typing import Optional
from dataclasses import dataclass


@dataclass
class TradeSignal:
    """标准化交易信号"""
    symbol: str            # 原始符号，如 "BTCUSDT"
    okx_symbol: str        # OKX 格式，如 "BTC-USDT-SWAP"
    direction: str         # "long" 或 "short"
    price: float           # 信号价格
    stop_loss: float       # 止损价（如果 TV 提供了）
    take_profit: float     # 止盈价（如果 TV 提供了）
    strategy: str          # 策略名称，如 "LuxAlgo"
    interval: str          # 时间周期，如 "1h"
    comment: str           # 备注
    raw_data: dict         # 原始数据


def parse_tv_webhook(body: str) -> Optional[TradeSignal]:
    """
    解析 TradingView Webhook 请求体

    支持的格式:
    1. JSON: {"signal": "long", "symbol": "BTCUSDT", "price": 76800, ...}
    2. JSON: {"action": "buy", "ticker": "BTCUSDT.P", "close": 76800, ...}
    3. 纯文本消息（尽力解析）
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        # 纯文本格式，尝试从文本中提取信息
        return _parse_text_signal(body)

    if not isinstance(data, dict):
        return None

    # ---- 格式1：标准格式 ----
    if "signal" in data:
        return _parse_standard_format(data)

    # ---- 格式2：TradingView 默认格式 ----
    if "action" in data or "ticker" in data:
        return _parse_tv_default_format(data)

    # ---- 兜底：尝试猜测字段 ----
    return _parse_guess_format(data)


def _parse_standard_format(data: dict) -> TradeSignal:
    """解析标准格式信号"""
    raw_symbol = data.get("symbol", "BTCUSDT")
    direction = data.get("signal", "long").lower()

    # 统一方向表达
    if direction in ("buy", "买入", "做多", "多"):
        direction = "long"
    elif direction in ("sell", "卖出", "做空", "空"):
        direction = "short"

    return TradeSignal(
        symbol=raw_symbol,
        okx_symbol=_to_okx_symbol(raw_symbol),
        direction=direction,
        price=float(data.get("price", 0)),
        stop_loss=float(data.get("stop_loss", 0)) if data.get("stop_loss") else 0,
        take_profit=float(data.get("take_profit", 0)) if data.get("take_profit") else 0,
        strategy=data.get("strategy", data.get("indicator", "TradingView")),
        interval=data.get("interval", data.get("timeframe", "")),
        comment=data.get("comment", data.get("message", "")),
        raw_data=data,
    )


def _parse_tv_default_format(data: dict) -> TradeSignal:
    """解析 TV 默认警报格式"""
    raw_symbol = data.get("ticker", data.get("symbol", "BTCUSDT"))
    action = data.get("action", "").lower()

    direction = "long" if action in ("buy", "long") else "short"
    price = float(data.get("close", data.get("price", 0)))

    return TradeSignal(
        symbol=raw_symbol,
        okx_symbol=_to_okx_symbol(raw_symbol),
        direction=direction,
        price=price,
        stop_loss=float(data.get("sl", 0)) if data.get("sl") else 0,
        take_profit=float(data.get("tp", 0)) if data.get("tp") else 0,
        strategy=data.get("strategy", "TradingView"),
        interval=data.get("interval", ""),
        comment=data.get("comment", ""),
        raw_data=data,
    )


def _parse_guess_format(data: dict) -> Optional[TradeSignal]:
    """兜底解析：尝试从任意 JSON 中提取信号信息"""
    # 找方向
    direction = "long"
    for key in data:
        val = str(data[key]).lower()
        if val in ("short", "sell", "做空"):
            direction = "short"
            break
        if val in ("long", "buy", "做多"):
            direction = "long"
            break

    # 找符号
    raw_symbol = "BTCUSDT"
    for key in ("symbol", "ticker", "instId", "pair", "coin"):
        if key in data:
            raw_symbol = str(data[key])
            break

    price = 0
    for key in ("price", "close", "entry", "last"):
        if key in data:
            price = float(data[key])
            break

    return TradeSignal(
        symbol=raw_symbol,
        okx_symbol=_to_okx_symbol(raw_symbol),
        direction=direction,
        price=price,
        stop_loss=0,
        take_profit=0,
        strategy="TradingView",
        interval="",
        comment="从原始数据解析",
        raw_data=data,
    )


def _parse_text_signal(text: str) -> Optional[TradeSignal]:
    """从纯文本中尝试解析信号"""
    direction = "long"
    text_lower = text.lower()
    if any(w in text_lower for w in ("short", "做空", "卖出", "sell")):
        direction = "short"
    if any(w in text_lower for w in ("long", "做多", "买入", "buy")):
        direction = "long"

    # 尝试提取价格
    price_match = re.search(r'(\d{2,6}\.?\d*)', text)
    price = float(price_match.group(1)) if price_match else 0

    return TradeSignal(
        symbol="BTCUSDT",
        okx_symbol="BTC-USDT-SWAP",
        direction=direction,
        price=price,
        stop_loss=0,
        take_profit=0,
        strategy="TradingView",
        interval="",
        comment=text,
        raw_data={"raw_text": text},
    )


def _to_okx_symbol(raw: str) -> str:
    """
    将各种格式的交易对转为 OKX 永续合约格式

    示例:
        BTCUSDT    → BTC-USDT-SWAP
        BTCUSDT.P  → BTC-USDT-SWAP
        BTC-USDT   → BTC-USDT-SWAP
        ETHUSDT    → ETH-USDT-SWAP
    """
    symbol = raw.upper().replace(".P", "").replace("-", "").replace("/", "").replace(" ", "")

    # 常见的 USDT 本位合约
    if symbol.endswith("USDT"):
        base = symbol[:-4]
        return f"{base}-USDT-SWAP"

    # 已经是 OKX 格式就直接返回
    if "-SWAP" in symbol:
        return symbol

    # 兜底
    return f"{symbol}-SWAP"
