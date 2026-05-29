"""
OKX API v5 客户端 - 合约交易
文档: https://www.okx.com/docs-v5/
"""

import json
import time
import hmac
import base64
import httpx
from config import OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE, OKX_DEMO, OKX_BASE_URL, PROXY_URL


class OKXClient:
    """OKX 交易所 API 封装"""

    def __init__(self):
        self.api_key = OKX_API_KEY
        self.secret_key = OKX_SECRET_KEY
        self.passphrase = OKX_PASSPHRASE
        self.demo = OKX_DEMO
        self.base_url = OKX_BASE_URL
        self._http = httpx.Client(proxy=PROXY_URL, timeout=15) if PROXY_URL else httpx.Client(timeout=15)

    # ----------------------------------------------------------------
    # HTTP 请求
    # ----------------------------------------------------------------

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        """生成 OKX 鉴权请求头"""
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        message = timestamp + method.upper() + path + body

        signature = base64.b64encode(
            hmac.new(
                self.secret_key.encode(),
                message.encode(),
                "sha256",
            ).digest()
        ).decode()

        headers = {
            "OK-ACCESS-KEY": self.api_key,
            "OK-ACCESS-SIGN": signature,
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type": "application/json",
        }
        # 模拟盘标识
        if self.demo:
            headers["x-simulated-trading"] = "1"

        return headers

    def _get(self, path: str) -> dict:
        """发送 GET 请求（带重试）"""
        url = self.base_url + path
        headers = self._headers("GET", path)
        last_err = None
        for attempt in range(3):
            try:
                resp = self._http.get(url, headers=headers)
                return resp.json()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1 + attempt * 2)
        raise last_err or RuntimeError("_get failed after 3 retries")

    def _post(self, path: str, data: dict) -> dict:
        """发送 POST 请求（带重试）"""
        url = self.base_url + path
        body = json.dumps(data) if data else ""
        headers = self._headers("POST", path, body)
        last_err = None
        for attempt in range(3):
            try:
                resp = self._http.post(url, headers=headers, content=body)
                return resp.json()
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(1 + attempt * 2)
        raise last_err or RuntimeError("_post failed after 3 retries")

    def _ok(self, result: dict) -> bool:
        """检查 API 返回是否成功"""
        return result.get("code") == "0"

    @staticmethod
    def _error_msg(result: dict) -> str:
        return result.get("msg", f"错误码: {result.get('code')}")

    # ----------------------------------------------------------------
    # 账户相关
    # ----------------------------------------------------------------

    def get_balance(self) -> dict:
        """获取账户 USDT 余额"""
        result = self._get("/api/v5/account/balance")
        if not self._ok(result):
            return {"error": self._error_msg(result)}

        for item in result.get("data", []):
            for detail in item.get("details", []):
                if detail.get("ccy") == "USDT":
                    return {
                        "equity": self._nf(detail.get("eq")),
                        "available": self._nf(detail.get("availBal")),
                        "frozen": self._nf(detail.get("frozenBal")),
                        "unrealized_pnl": self._nf(detail.get("upl")),
                    }
        return {"equity": 0, "available": 0, "frozen": 0, "unrealized_pnl": 0}

    @staticmethod
    def _nf(val) -> float:
        """安全转 float，空值返回 0"""
        try:
            return float(val) if val != "" and val is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def get_positions(self) -> list:
        """获取当前持仓（永续合约）"""
        result = self._get("/api/v5/account/positions?instType=SWAP")
        if not self._ok(result):
            return []

        positions = []
        for item in result.get("data", []):
            pos = self._nf(item.get("pos"))
            if pos != 0:
                positions.append({
                    "instId": item.get("instId"),
                    "side": "long" if pos > 0 else "short",
                    "quantity": abs(pos),
                    "avgPx": self._nf(item.get("avgPx")),
                    "markPx": self._nf(item.get("markPx")),
                    "upl": self._nf(item.get("upl")),
                    "lever": item.get("lever"),
                    "margin": self._nf(item.get("margin")),
                })
        return positions

    def set_leverage(self, symbol: str, leverage: int) -> tuple[bool, str]:
        """设置杠杆倍数"""
        result = self._post("/api/v5/account/set-leverage", {
            "instId": symbol,
            "lever": str(leverage),
            "mgnMode": "isolated",  # 逐仓
        })
        if not self._ok(result):
            return False, self._error_msg(result)
        return True, ""

    # ----------------------------------------------------------------
    # 交易相关
    # ----------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        direction: str,
        quantity: float,
        stop_loss: float = 0,
        take_profit: float = 0,
        ord_type: str = "limit",
        limit_price: float = 0,
    ) -> tuple[bool, str, str]:
        """
        开仓下单 + 止损止盈

        参数:
            symbol:      如 "BTC-USDT-SWAP"
            direction:   "long" 或 "short"
            quantity:    合约张数
            stop_loss:   止损价
            take_profit: 止盈价
            ord_type:    "limit"(限价入场) 或 "market"(市价入场)
            limit_price: 限价入场价格（ord_type=limit时必填）

        返回:
            (成功, 消息, 订单ID)
        """
        side = "buy" if direction == "long" else "sell"

        order_data = {
            "instId": symbol,
            "tdMode": "isolated",
            "side": side,
            "ordType": ord_type,
            "sz": str(quantity),
        }

        if ord_type == "limit":
            if limit_price <= 0:
                return False, "限价单缺少 limit_price", ""
            order_data["px"] = str(limit_price)

        # 附加止损止盈（触发后市价平仓）
        if stop_loss > 0 or take_profit > 0:
            attached = {}
            if stop_loss > 0:
                attached["slTriggerPx"] = str(stop_loss)
                attached["slOrdPx"] = "-1"
            if take_profit > 0:
                attached["tpTriggerPx"] = str(take_profit)
                attached["tpOrdPx"] = "-1"
            order_data["attachAlgoOrds"] = [attached]

        result = self._post("/api/v5/trade/order", order_data)

        if not self._ok(result):
            return False, self._error_msg(result), ""

        order_id = result["data"][0]["ordId"]
        return True, "下单成功", order_id

    def cancel_order(self, symbol: str, order_id: str) -> tuple[bool, str]:
        """撤销未成交普通委托。"""
        result = self._post("/api/v5/trade/cancel-order", {
            "instId": symbol,
            "ordId": order_id,
        })
        if not self._ok(result):
            return False, self._error_msg(result)
        return True, "撤单成功"

    def close_position(self, symbol: str) -> tuple[bool, str]:
        """市价全平指定仓位"""
        result = self._post("/api/v5/trade/close-position", {
            "instId": symbol,
            "mgnMode": "isolated",
        })
        if not self._ok(result):
            return False, self._error_msg(result)
        return True, "平仓成功"

    def get_market_price(self, symbol: str) -> float:
        """获取当前市价"""
        result = self._get(f"/api/v5/market/ticker?instId={symbol}")
        if self._ok(result) and result.get("data"):
            return self._nf(result["data"][0]["last"])
        return 0

    def get_instrument_info(self, symbol: str) -> dict:
        """获取合约信息（面值等）"""
        result = self._get(f"/api/v5/public/instruments?instType=SWAP&instId={symbol}")
        if self._ok(result) and result.get("data"):
            info = result["data"][0]
            return {
                "ctVal": self._nf(info.get("ctVal")) or 0.001,
                "minSz": self._nf(info.get("minSz")) or 1,
                "lotSz": self._nf(info.get("lotSz")) or 1,
                "tickSz": self._nf(info.get("tickSz")) or 0.1,
            }
        return {"ctVal": 0.001, "minSz": 1, "lotSz": 1, "tickSz": 0.1}

    # ----------------------------------------------------------------
    # 便捷方法
    # ----------------------------------------------------------------

    def get_position_count(self) -> int:
        """获取当前持仓数量"""
        return len(self.get_positions())

    def has_position(self, symbol: str) -> bool:
        """检查是否持有指定币种的仓位"""
        return any(pos["instId"] == symbol for pos in self.get_positions())

    def get_account_summary(self) -> str:
        """生成账户摘要文本（给 Telegram 用）"""
        balance = self.get_balance()
        positions = self.get_positions()

        text = "📊 *账户概览*\n\n"
        text += f"💰 净值: {balance.get('equity', 0):.2f} USDT\n"
        text += f"📈 可用: {balance.get('available', 0):.2f} USDT\n"
        text += f"📉 冻结: {balance.get('frozen', 0):.2f} USDT\n"
        text += f"📊 浮动盈亏: {balance.get('unrealized_pnl', 0):.2f} USDT\n\n"

        if positions:
            text += "*当前持仓:*\n"
            for pos in positions:
                emoji = "🟢" if pos["upl"] >= 0 else "🔴"
                text += (
                    f"{emoji} {pos['instId']} {pos['side'].upper()} "
                    f"{pos['quantity']}张 @ {pos['avgPx']:.1f}\n"
                    f"  浮动: {pos['upl']:.2f} USDT | 杠杆: {pos['lever']}x\n"
                )
        else:
            text += "📭 当前无持仓"

        return text
