"""Bybit API v5 客户端 — 只读市场数据 + 账户查询

3Commas 处理所有交易执行，此模块仅用于行情 + 账户 + PnL
"""

import hmac
import hashlib
import time
from datetime import datetime, timedelta, timezone

import httpx

from .config import (
    BYBIT_API_KEY, BYBIT_SECRET_KEY, BYBIT_DEMO, PROXY_URL,
)
from .logging_ import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api-testnet.bybit.com" if BYBIT_DEMO else "https://api.bybit.com"
RECV_WINDOW = "5000"


class BybitClient:
    """Bybit 行情 + 账户查询客户端"""

    def __init__(self):
        self._client = self._build_client()

    def _build_client(self) -> httpx.Client:
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        transport_kwargs = {}
        if PROXY_URL:
            transport_kwargs["proxy"] = PROXY_URL
        return httpx.Client(
            base_url=BASE_URL,
            timeout=httpx.Timeout(15.0),
            limits=limits,
            transport=httpx.HTTPTransport(retries=2, **transport_kwargs),
        )

    def close(self):
        self._client.close()

    # ── 签名 ──────────────────────────────────────────

    def _sign(self, params: dict) -> dict:
        """HMAC-SHA256 签名 (Bybit API v5)"""
        timestamp = str(int(time.time() * 1000))
        # Bybit v5 signature: timestamp + api_key + recv_window + query_string
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
        sign_str = f"{timestamp}{BYBIT_API_KEY}{RECV_WINDOW}{query}"
        signature = hmac.new(
            BYBIT_SECRET_KEY.encode("utf-8"),
            sign_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-BAPI-API-KEY": BYBIT_API_KEY,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": RECV_WINDOW,
            "X-BAPI-SIGN": signature,
        }

    def _get(self, path: str, params: dict | None = None, signed: bool = False) -> dict:
        headers = {"Content-Type": "application/json"}
        params = params or {}
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items())) if params else ""
        if signed and BYBIT_API_KEY:
            headers.update(self._sign(params))
        url = f"{path}?{query}" if query else path
        r = self._client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
        if data.get("retCode") != 0:
            raise RuntimeError(f"Bybit API error: {data}")
        return data.get("result", {})

    # ── 市场数据 ──────────────────────────────────────

    def get_ticker(self, symbol: str) -> dict:
        """24h 行情"""
        result = self._get("/v5/market/tickers", {"category": "linear", "symbol": symbol})
        items = result.get("list", [])
        return items[0] if items else {}

    def get_market_price(self, symbol: str) -> float:
        t = self.get_ticker(symbol)
        return float(t.get("lastPrice", 0))

    def get_klines(
        self, symbol: str, interval: str = "5", limit: int = 100,
    ) -> list[dict]:
        """K线: interval = 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M"""
        result = self._get(
            "/v5/market/kline",
            {"category": "linear", "symbol": symbol, "interval": interval, "limit": limit},
        )
        return result.get("list", [])

    def get_orderbook(self, symbol: str, depth: int = 25) -> dict:
        result = self._get(
            "/v5/market/orderbook",
            {"category": "linear", "symbol": symbol, "limit": depth},
        )
        return result

    def get_funding_rate(self, symbol: str) -> float:
        result = self._get(
            "/v5/market/tickers",
            {"category": "linear", "symbol": symbol},
        )
        items = result.get("list", [])
        return float(items[0].get("fundingRate", "0")) if items else 0.0

    def get_open_interest(self, symbol: str) -> float:
        result = self._get(
            "/v5/market/open-interest",
            {"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 1},
        )
        items = result.get("list", [])
        return float(items[0].get("openInterest", "0")) if items else 0.0

    # ── 账户 ──────────────────────────────────────────

    def get_account_summary(self) -> dict:
        """统一账户余额"""
        result = self._get("/v5/account/wallet-balance", {"accountType": "UNIFIED"}, signed=True)
        items = result.get("list", [])
        if not items:
            return {"equity": 0, "available": 0, "unrealized_pnl": 0}
        coin_list = items[0].get("coin", [])
        usdt = next((c for c in coin_list if c.get("coin") == "USDT"), coin_list[0] if coin_list else {})
        equity = float(usdt.get("equity", "0"))
        available = float(usdt.get("availableToWithdraw", "0"))
        unrealized = float(usdt.get("unrealisedPnl", "0"))
        return {"equity": equity, "available": available, "unrealized_pnl": unrealized}

    def get_positions(self) -> list[dict]:
        """当前持仓"""
        result = self._get(
            "/v5/position/list",
            {"category": "linear", "settleCoin": "USDT"},
            signed=True,
        )
        return result.get("list", [])

    def get_pnl_records(self, days: int = 1) -> list[dict]:
        """已平仓盈亏记录"""
        now = datetime.now(timezone.utc)
        start = int((now - timedelta(days=days)).timestamp() * 1000)
        end = int(now.timestamp() * 1000)
        result = self._get(
            "/v5/position/closed-pnl",
            {"category": "linear", "limit": 50, "startTime": start, "endTime": end},
            signed=True,
        )
        return result.get("list", [])
