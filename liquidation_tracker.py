"""
清算数据追踪器 — OKX WebSocket liquidation-orders 免费公开频道
聚合清算热力图，识别流动性磁吸区和止损耗点
"""

import json
import time
import threading
from collections import defaultdict


class LiquidationTracker:
    """实时追踪 OKX 合约清算数据"""

    def __init__(self):
        self._running = False
        self._ws = None
        self._thread = None

        # symbol -> {price_level: total_sz}
        self.long_liq = defaultdict(lambda: defaultdict(float))
        self.short_liq = defaultdict(lambda: defaultdict(float))

        self.recent_total = 0
        self._max_age = 3600

    # ------------------------------------------------------------------
    # WebSocket
    # ------------------------------------------------------------------

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._ws_loop, daemon=True)
        self._thread.start()
        print("[清算] 追踪器已启动")

    def stop(self):
        self._running = False

    def _ws_loop(self):
        while self._running:
            try:
                self._connect()
            except Exception as e:
                print(f"[清算] WS 断开: {e}, 10秒后重连")
                time.sleep(10)

    def _connect(self):
        import websocket
        ws = websocket.WebSocketApp(
            "wss://wspap.okx.com:8443/ws/v5/public?broker=0",
            on_message=self._on_msg,
            on_open=self._on_open,
            on_error=lambda ws, e: print(f"[清算] WS错误: {e}"),
            on_close=lambda ws, code, msg: print(f"[清算] WS关闭: {code} {msg}"),
        )
        self._ws = ws
        ws.run_forever(ping_interval=30, ping_timeout=10)

    def _on_open(self, ws):
        sub = {
            "op": "subscribe",
            "args": [{"channel": "liquidation-orders", "instType": "SWAP"}],
        }
        ws.send(json.dumps(sub))
        print("[清算] 已订阅 liquidation-orders")

    def _on_msg(self, ws, message):
        try:
            data = json.loads(message)
            if "data" not in data:
                return
            for item in data["data"]:
                inst_id = item.get("instId", "")
                for d in item.get("details", []):
                    side = d.get("side", "")
                    sz = float(d.get("sz", 0))
                    px = float(d.get("bkPx", 0))
                    if px <= 0 or sz <= 0:
                        continue

                    bucket = self._bucket(px)
                    if side == "buy":
                        self.short_liq[inst_id][bucket] += sz
                    else:
                        self.long_liq[inst_id][bucket] += sz

                    self.recent_total += sz
        except Exception:
            pass

    @staticmethod
    def _bucket(price: float) -> float:
        if price > 1000:
            return round(price / 50) * 50
        elif price > 10:
            return round(price / 1) * 1
        else:
            return round(price / 0.01) * 0.01

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------

    def get_heatmap(self, symbol: str, levels: int = 10) -> dict:
        long_data = dict(sorted(self.long_liq.get(symbol, {}).items(),
                                key=lambda x: -x[1])[:levels])
        short_data = dict(sorted(self.short_liq.get(symbol, {}).items(),
                                 key=lambda x: -x[1])[:levels])
        return {"long_liq": long_data, "short_liq": short_data}

    def liquidation_pressure(self, symbol: str, current_price: float, window_pct: float = 1.0) -> dict:
        low = current_price * (1 - window_pct / 100)
        high = current_price * (1 + window_pct / 100)

        long_sz = sum(sz for px, sz in self.long_liq.get(symbol, {}).items() if low <= px <= high)
        short_sz = sum(sz for px, sz in self.short_liq.get(symbol, {}).items() if low <= px <= high)

        total = long_sz + short_sz
        if total == 0:
            return {"pressure": "neutral", "ratio": 0, "total_liq": 0}

        ratio = short_sz / total
        if ratio > 0.6:
            pressure = "bullish"
        elif ratio < 0.4:
            pressure = "bearish"
        else:
            pressure = "neutral"

        return {"pressure": pressure, "ratio": round(ratio, 2), "total_liq": round(total, 1)}

    def nearest_cluster(self, symbol: str, price: float) -> dict:
        all_levels = {}
        for px, sz in self.long_liq.get(symbol, {}).items():
            all_levels[px] = all_levels.get(px, 0) + sz
        for px, sz in self.short_liq.get(symbol, {}).items():
            all_levels[px] = all_levels.get(px, 0) + sz

        if not all_levels:
            return {"above": None, "below": None}

        above = [(p, s) for p, s in all_levels.items() if p > price]
        below = [(p, s) for p, s in all_levels.items() if p < price]

        above.sort(key=lambda x: x[0])
        below.sort(key=lambda x: -x[0])

        return {
            "above": {"price": above[0][0], "size": round(above[0][1], 1)} if above else None,
            "below": {"price": below[0][0], "size": round(below[0][1], 1)} if below else None,
        }

    def summary(self, symbol: str, current_price: float) -> str:
        _h = self.get_heatmap(symbol, levels=5)
        p = self.liquidation_pressure(symbol, current_price)
        c = self.nearest_cluster(symbol, current_price)

        lines = [f"清算压力: {p['pressure']} (ratio={p['ratio']}, 总量={p['total_liq']}张)"]

        if c["below"]:
            lines.append(f"下方清算聚集: ${c['below']['price']:,.1f} ({c['below']['size']}张)")
        if c["above"]:
            lines.append(f"上方清算聚集: ${c['above']['price']:,.1f} ({c['above']['size']}张)")

        top_long = sorted(self.long_liq.get(symbol, {}).items(), key=lambda x: -x[1])[:3]
        top_short = sorted(self.short_liq.get(symbol, {}).items(), key=lambda x: -x[1])[:3]

        if top_long:
            lines.append("最大多头清算区: " + ", ".join(f"${p:,.1f}({s:.0f})" for p, s in top_long))
        if top_short:
            lines.append("最大空头清算区: " + ", ".join(f"${p:,.1f}({s:.0f})" for p, s in top_short))

        return "\n".join(lines)
