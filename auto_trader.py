"""
AI 自主交易引擎 v5 — 全功能增强版

安全系统:
  1. 双 AI 校验 (快速+深度，方向一致才下单)
  2. 移动止盈 (浮盈>1.5%自动推止损到成本)
  3. 黑天鹅保护 (BTC 10分跌>3% → 暂停30分)
  4. 时段自适应 (亚盘/欧美/重叠 → 动态调参)
  5. 连亏缩仓 (连亏3→减半，连亏5→暂停1H)
  6. 关联性过滤 (BTC大跌时不单独做多山寨)
  7. 链上数据 (资金费率+持仓量+OI背离)
  8. 清算热力图 (流动性磁吸+止损验证)
  9. ATR波动率自适应仓位
  10. 每周期复盘 (AI 总结+调优)
"""

import json
import time
import threading
from datetime import datetime, date, timedelta
from typing import ClassVar, Optional
import http_wrapper as requests

from config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, PROXY_URL,
    MAX_DAILY_LOSS, AUTO_TRADE,
)
from risk_manager import RiskManager
from okx_client import OKXClient
from trade_logger import TradeLogger
from strategy_analyzer import StrategyAnalyzer
from liquidation_tracker import LiquidationTracker
from factor_miner import FactorMiner
from paul_wei_analyzer import get_paul_wei
from bayesian_tracker import BayesianTracker


TOP_COINS = [
    "BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP",
    "BNB-USDT-SWAP", "XRP-USDT-SWAP", "DOGE-USDT-SWAP",
    "ADA-USDT-SWAP", "AVAX-USDT-SWAP", "LINK-USDT-SWAP",
    "DOT-USDT-SWAP",
]

# 交易时段 (UTC)
SESSIONS = {
    "asia":   (0, 8),     # 亚盘 08:00-16:00 北京时间
    "eu":     (7, 15),    # 欧盘 15:00-23:00 北京
    "us":     (12, 20),   # 美盘 20:00-04:00 北京
    "overlap":(12, 15),   # 欧美好时段 20:00-23:00 北京
}

class AutoTrader:

    def __init__(self):
        self.api_key = DEEPSEEK_API_KEY
        self.base_url = DEEPSEEK_BASE_URL
        self.proxies = {"https": PROXY_URL} if PROXY_URL else None
        self.kline = StrategyAnalyzer()
        self.okx = OKXClient()
        self.risk = RiskManager()
        self.logger = TradeLogger()
        self.liq_tracker = LiquidationTracker()
        self.bayesian = BayesianTracker()
        self._running = False
        self._factor_miner = None  # 延迟初始化，避免 API key 检查

        # ---- 参数 ----
        self.scalp = {"enabled":True,"leverage":15,"risk_pct":0.5,"min_confidence":70,
                      "min_rr":2.0,"tp_sl_ratio":2.5,"trailing_pct":0.3,
                      "max_positions":3,"interval":300,
                      "timeframes":["1m","5m","15m"],"atr_mult_sl":1.5,"atr_mult_tp":4.0}
        self.swing = {"enabled":True,"leverage":8,"risk_pct":1.5,"min_confidence":65,
                      "min_rr":2.5,"tp_sl_ratio":3.0,"trailing_pct":1.0,
                      "max_positions":2,"interval":3600,
                      "timeframes":["1H","4H","1D"],"atr_mult_sl":2.0,"atr_mult_tp":6.0}

        self.safety = {"max_leverage":25,"max_risk_pct":2.0,"max_positions_total":4,
                       "max_daily_loss":MAX_DAILY_LOSS,"min_liquidation_buffer":0.5}

        # ---- 状态 ----
        self.consecutive_losses = 0
        self.reduced_mode = False
        self.blackout_until = None
        self.btc_crash = False
        self.breakeven_moved = {}  # order_id -> True
        self.pending_orders = {}   # order_id -> {symbol, entry, qty, time, notified}
        self.experience = []
        self.paul_wei = get_paul_wei()  # Paul Wei 交易模式分析器

    # ================================================================
    # DeepSeek
    # ================================================================

    def _call_ds(self, system: str, user: str, max_tokens: int = 2048) -> str:
        if not self.api_key: return ""
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        p = {"model":"deepseek-chat","messages":[{"role":"system","content":system},
             {"role":"user","content":user}],"max_tokens":max_tokens,"temperature":0.4}
        try:
            r = requests.post(f"{self.base_url}/chat/completions", headers=h, json=p,
                              proxies=self.proxies, timeout=150)
            return r.json()["choices"][0]["message"]["content"]
        except Exception: return ""

    # ================================================================
    # 市场数据
    # ================================================================

    @staticmethod
    def _sma(data: list, period: int) -> list:
        r = [0.0]*len(data)
        for i in range(period-1, len(data)):
            r[i] = sum(data[i-period+1:i+1])/period
        for i in range(period-1): r[i] = r[period-1]
        return r

    @staticmethod
    def _bollinger(closes: list, period: int = 20, mult: float = 2.0) -> dict:
        sma = AutoTrader._sma(closes, period)
        upper = [0.0]*len(closes); lower = [0.0]*len(closes)
        for i in range(period-1, len(closes)):
            std = (sum((closes[j]-sma[i])**2 for j in range(i-period+1,i+1))/period)**0.5
            upper[i] = sma[i] + mult*std; lower[i] = sma[i] - mult*std
        for i in range(period-1): upper[i]=upper[period-1]; lower[i]=lower[period-1]
        return {"mid":sma[-1],"upper":upper[-1],"lower":lower[-1],
                "width_pct":(upper[-1]-lower[-1])/sma[-1]*100 if sma[-1]>0 else 0}

    @staticmethod
    def _vwap(candles: list) -> float:
        """成交量加权均价"""
        cum_pv = 0.0; cum_v = 0.0
        for c in candles:
            typical = (c["high"]+c["low"]+c["close"])/3
            v = c["volume"] if c["volume"]>0 else 1
            cum_pv += typical*v; cum_v += v
        return cum_pv/cum_v if cum_v>0 else 0

    @staticmethod
    def _vol_expansion(closes: list, period: int = 20) -> bool:
        """波动率扩张检测: 最近3根ATR > 前20根ATR×1.5"""
        if len(closes)<period+3: return False
        trs = []
        for i in range(1,len(closes)):
            trs.append(abs(closes[i]-closes[i-1]))
        recent_atr = sum(trs[-3:])/3
        base_atr = sum(trs[-period:-3])/(period-3) if len(trs)>=period else recent_atr
        return recent_atr > base_atr*1.5

    @staticmethod
    def _supertrend(highs: list, lows: list, closes: list, period: int = 10, mult: float = 3.0) -> dict:
        """SuperTrend — ATR 通道趋势跟踪"""
        n = len(closes)
        if n < period+1: return {"trend":"unknown","value":0,"flip":False}
        # ATR
        trs = []
        for i in range(1,n):
            trs.append(max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])))
        atr = sum(trs[-period:])/period
        # 基础通道
        upper = [0.0]*n; lower = [0.0]*n
        for i in range(period,n):
            hl2 = (highs[i]+lows[i])/2
            upper[i] = hl2 + mult*atr; lower[i] = hl2 - mult*atr
        # SuperTrend
        st = [0.0]*n; trend = [True]*n  # True=up
        for i in range(1,n):
            if closes[i] > upper[i] if i>=period else True:
                st[i] = lower[i] if i>=period else lower[-1]; trend[i] = True
            elif closes[i] < lower[i] if i>=period else True:
                st[i] = upper[i] if i>=period else upper[-1]; trend[i] = False
            else:
                st[i] = st[i-1]; trend[i] = trend[i-1]
            # 通道调整
            if trend[i] and i>=period:
                if lower[i] > st[i-1] if trend[i-1] else upper[i] > st[i-1]:
                    st[i] = max(st[i-1], lower[i]) if trend[i-1] else st[i]
            elif not trend[i] and i>=period and (upper[i] < st[i-1] if not trend[i-1] else lower[i] < st[i-1]):
                    st[i] = min(st[i-1], upper[i]) if not trend[i-1] else st[i]
        flip = trend[-1] != trend[-2] if len(trend)>1 else False
        return {"trend":"up" if trend[-1] else "down", "value":round(st[-1],1),
                "flip":flip, "streak":sum(1 for t in trend[-10:] if t==trend[-1]) if len(trend)>=10 else 0}

    # ================================================================
    # K线形态识别
    # ================================================================

    @staticmethod
    def _candle_patterns(candles: list) -> list:
        """识别近5根K线的经典形态"""
        patterns = []
        if len(candles) < 5: return patterns
        c = candles
        # 当前K线
        o, h, lo, cl = c[-1]["open"], c[-1]["high"], c[-1]["low"], c[-1]["close"]
        body = abs(cl-o); upper_wick = h-max(cl,o); lower_wick = min(cl,o)-lo; total = h-lo if h>lo else 0.0001
        body_r = body/total; prev_cl = c[-2]["close"] if len(c)>=2 else cl

        # 锤子线 (长下影>实体2倍, 实体在顶部)
        if lower_wick>body*2 and upper_wick<lower_wick*0.3 and body_r>0.05:
            patterns.append({"type":"hammer","signal":"bullish_reversal","desc":"锤子线,看涨反转"})
        # 上吊线 (长下影,但处于上涨后)
        if lower_wick>body*2 and upper_wick<lower_wick*0.3 and cl>prev_cl:
            patterns.append({"type":"hanging_man","signal":"bearish_reversal","desc":"上吊线,看跌反转"})
        # 射击之星 (长上影)
        if upper_wick>body*2 and lower_wick<upper_wick*0.3 and body_r>0.05:
            patterns.append({"type":"shooting_star","signal":"bearish_reversal","desc":"射击之星,看跌反转"})
        # 倒锤子 (长上影,下跌后)
        if upper_wick>body*2 and lower_wick<upper_wick*0.3 and cl<prev_cl:
            patterns.append({"type":"inverted_hammer","signal":"bullish_reversal","desc":"倒锤子,看涨反转"})
        # 十字星 (实体极小)
        if body_r<0.1 and total>0:
            patterns.append({"type":"doji","signal":"reversal_warning","desc":"十字星,趋势可能反转"})

        # 2K线形态
        if len(c)>=3:
            c1_o,c1_c=c[-2]["open"],c[-2]["close"]; c2_o,c2_c=c[-3]["open"],c[-3]["close"]
            # 吞没
            if cl>c1_o and c1_c<c1_o and c2_c<c2_o:  # 阳包阴
                patterns.append({"type":"bullish_engulfing","signal":"bullish_reversal","desc":"看涨吞没"})
            if cl<c1_o and c1_c>c1_o and c2_c>c2_o:  # 阴包阳
                patterns.append({"type":"bearish_engulfing","signal":"bearish_reversal","desc":"看跌吞没"})

        # 3K线形态
        if len(c)>=5:
            b3,b2,b1 = c[-5:-2]  # 前三根
            # 三白兵: 3根连续阳线,实体递增
            if all(x["close"]>x["open"] for x in [b3,b2,b1,c[-2]]) and \
               (b2["close"]-b2["open"])>(b3["close"]-b3["open"]) and \
               (b1["close"]-b1["open"])>(b2["close"]-b2["open"]):
                patterns.append({"type":"three_soldiers","signal":"bullish_continue","desc":"三白兵,强多头"})
            # 三乌鸦
            if all(x["close"]<x["open"] for x in [b3,b2,b1,c[-2]]) and \
               (b2["open"]-b2["close"])>(b3["open"]-b3["close"]) and \
               (b1["open"]-b1["close"])>(b2["open"]-b2["close"]):
                patterns.append({"type":"three_crows","signal":"bearish_continue","desc":"三乌鸦,强空头"})

        return patterns

    # ================================================================
    # 多TF信号融合打分
    # ================================================================

    def _multi_tf_score(self, market: dict, direction: str) -> float:
        """多周期信号融合: 每个周期独立打分,加权平均"""
        weights = {"1m":0.05,"5m":0.10,"15m":0.15,"1H":0.25,"4H":0.25,"1D":0.20}
        scores = []
        for tf, w in weights.items():
            if tf not in market: continue
            d = market[tf]; s = 0.5
            trend_ok = (direction=="long" and d.get("ema20",0)>d.get("ema50",0)) or \
                       (direction=="short" and d.get("ema20",0)<d.get("ema50",0))
            rsi_ok = (direction=="long" and 30<d.get("rsi",50)<70) or \
                     (direction=="short" and 30<d.get("rsi",50)<70)
            st = d.get("supertrend",{})
            st_ok = (direction=="long" and st.get("trend")=="up") or \
                    (direction=="short" and st.get("trend")=="down")
            vol_ok = d.get("vol_ratio",1.0)>0.7
            bb = d.get("bb_width",5)
            bb_ok = bb>2 and bb<15  # 布林带不过窄不过宽

            if trend_ok: s+=0.15
            if rsi_ok: s+=0.10
            if st_ok: s+=0.15
            if vol_ok: s+=0.05
            if bb_ok: s+=0.05
            s = min(1.0,s)
            scores.append(s*w)
        return sum(scores)/(sum(weights.get(tf,0) for tf in market)) if scores else 0.5

    @staticmethod
    def _adx(highs: list, lows: list, closes: list, period: int = 14) -> dict:
        """ADX — 趋势强度"""
        n = len(closes)
        if n < period*2: return {"adx":25,"di_plus":25,"di_minus":25,"trend":"weak"}
        trs=[]; plus_dm=[]; minus_dm=[]
        for i in range(1,n):
            trs.append(max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),abs(lows[i]-closes[i-1])))
            up=highs[i]-highs[i-1]; down=lows[i-1]-lows[i]
            plus_dm.append(up if up>down and up>0 else 0)
            minus_dm.append(down if down>up and down>0 else 0)
        # Wilder's smoothing
        atr_smooth=sum(trs[:period]); pdm_smooth=sum(plus_dm[:period]); ndm_smooth=sum(minus_dm[:period])
        for i in range(period,len(trs)):
            atr_smooth=(atr_smooth*(period-1)+trs[i])/period
            pdm_smooth=(pdm_smooth*(period-1)+plus_dm[i])/period
            ndm_smooth=(ndm_smooth*(period-1)+minus_dm[i])/period
        pdi=100*pdm_smooth/atr_smooth if atr_smooth>0 else 25; ndi=100*ndm_smooth/atr_smooth if atr_smooth>0 else 25
        dx=abs(pdi-ndi)/(pdi+ndi)*100 if (pdi+ndi)>0 else 25
        # ADX = smoothed DX (simplified)
        trend="strong_up" if pdi>ndi and dx>25 else "strong_down" if ndi>pdi and dx>25 else "weak" if dx<20 else "ranging"
        return {"adx":round(dx,0),"di_plus":round(pdi,0),"di_minus":round(ndi,0),"trend":trend}

    def _market(self, symbol: str, timeframes: list) -> dict:
        data = {}
        for tf in timeframes:
            lim = {"1m":90,"5m":72,"15m":64,"1H":100,"4H":72,"1D":60}.get(tf,100)
            k = self.kline.fetch_klines(symbol, tf, lim)
            if k and len(k)>=20:
                c = [x["close"] for x in k]
                bb = self._bollinger(c, 20, 2.0)
                vwap = self._vwap(k)
                vol_exp = self._vol_expansion(c)
                # 支撑阻力
                recent = k[-50:] if len(k)>=50 else k
                highs_sr = sorted(set(round(x["high"],1) for x in recent), reverse=True)
                lows_sr = sorted(set(round(x["low"],1) for x in recent))
                cur = c[-1]
                res = next((h for h in highs_sr if h>cur), None)
                sup = next((lo for lo in reversed(lows_sr) if lo<cur), None)

                data[tf] = {
                    "price":cur, "high":max(x["high"] for x in k[-24:]),
                    "low":min(x["low"] for x in k[-24:]),
                    "ema20":self.kline.ema(c,min(20,len(c)))[-1],
                    "ema50":self.kline.ema(c,min(50,len(c)))[-1] if len(c)>=50 else c[-1],
                    "vwap":round(vwap,1),
                    "rsi":self.kline.rsi(c,min(14,len(c)-1)),
                    "macd":self.kline.macd(c),
                    "atr":self.kline.atr(k,min(14,len(k)-1)) if len(k)>14 else 0,
                    "vol_ratio":self.kline.volume_ratio(k),
                    "bb_upper":round(bb["upper"],1),"bb_mid":round(bb["mid"],1),
                    "bb_lower":round(bb["lower"],1),"bb_width":round(bb["width_pct"],1),
                    "support":sup,"resistance":res,
                    "vol_expansion":vol_exp,
                    "supertrend":self._supertrend([x["high"] for x in k],[x["low"] for x in k],c,10,3.0),
                    "adx":self._adx([x["high"] for x in k],[x["low"] for x in k],c,14),
                }
        return data

    # ================================================================
    # 链上数据
    # ================================================================

    def _onchain(self, symbol: str) -> dict:
        """拉资金费率 + 持仓量 + OI 变化"""
        try:
            r = requests.get(f"https://www.okx.com/api/v5/public/funding-rate?instId={symbol}",
                             proxies=self.proxies, timeout=10)
            fr = r.json()
            rate = float(fr["data"][0]["fundingRate"]) if fr.get("code")=="0" and fr.get("data") else 0
        except Exception: rate = 0

        try:
            r = requests.get(f"https://www.okx.com/api/v5/public/open-interest?instId={symbol}",
                             proxies=self.proxies, timeout=10)
            oi = r.json()
            oi_val = float(oi["data"][0]["oi"]) if oi.get("code")=="0" and oi.get("data") else 0
            _oi_ts = int(oi["data"][0]["ts"]) if oi.get("code")=="0" and oi.get("data") else 0
        except Exception: oi_val = 0; _oi_ts = 0

        # OI 变化趋势 (对比1小时前)
        oi_change = 0
        hist = None
        try:
            r = requests.get(
                f"https://www.okx.com/api/v5/public/open-interest?instId={symbol}&limit=12",
                proxies=self.proxies, timeout=10,
            )
            hist = r.json()
            if hist.get("code")=="0" and len(hist.get("data",[])) >= 2:
                oi_now = float(hist["data"][0]["oi"])
                oi_prev = float(hist["data"][-1]["oi"])
                if oi_prev > 0: oi_change = (oi_now - oi_prev) / oi_prev * 100
        except Exception: oi_change = 0

        # ---- OI + 费率 + 价格综合解读 ----
        fr_extreme = abs(rate) > 0.005
        oi_surging = oi_change > 20   # OI 1H暴增20%+
        oi_dropping = oi_change < -15

        signal = "neutral"
        explanation = ""

        # OI-价格背离检测
        oi_divergence = ""
        try:
            current_price = float(hist["data"][0].get("markPrice", 0)) if hist.get("data") else 0
            prev_price = float(hist["data"][-1].get("markPrice", 0)) if hist.get("data") and len(hist["data"]) >= 2 else 0
            if prev_price > 0 and current_price > 0:
                price_change = (current_price - prev_price) / prev_price * 100
                if price_change > 1 and oi_change < -3:
                    oi_divergence = "⚠️价涨OI跌=弱势反弹，降低多头信心"
                elif price_change < -1 and oi_change < -3:
                    oi_divergence = "✅价跌OI跌=空头衰竭，可能触底"
                elif price_change > 1 and oi_change > 5:
                    oi_divergence = "✅价涨OI涨=强势突破"
                elif price_change < -1 and oi_change > 5:
                    oi_divergence = "⚠️价跌OI涨=主力出货，禁止做多"
        except Exception:
            pass

        if fr_extreme and oi_surging:
            signal = "danger"
            explanation = "OI暴增+费率极端，大波动前兆，降低仓位观望"
        elif rate > 0.003 and oi_change > 10:
            signal = "crowded_long"
            explanation = "多头拥挤(费率{:.2f}%)，不追多，防插针".format(rate*100)
        elif rate < -0.003 and oi_change > 10:
            signal = "crowded_short"
            explanation = "空头拥挤(费率{:.2f}%)，防逼空".format(rate*100)
        elif oi_dropping:
            explanation = "OI下降中去杠杆，不接飞刀，等企稳"

        if oi_divergence:
            explanation = (explanation + " | " + oi_divergence)

        crowd_map = {"neutral": "中性", "danger": "危险",
                     "crowded_long": "多头拥挤", "crowded_short": "空头拥挤"}
        return {
            "funding_rate": rate, "open_interest": oi_val,
            "oi_val": oi_val,
            "oi_change_pct": round(oi_change, 1),
            "signal": signal, "explanation": explanation,
            "fr_extreme": fr_extreme, "oi_surging": oi_surging,
            "crowd_signal": crowd_map.get(signal, "中性"),
        }

    # ================================================================
    # 黑天鹅检测
    # ================================================================

    # ================================================================
    # 盘口微观结构
    # ================================================================

    def _microstructure(self, symbol: str) -> dict:
        """盘口微观分析: 价差、深度失衡、流动性断层、CVD"""
        ms = {"spread": 0, "imbalance": 0.5, "depth_1pct": 0, "mid_price": 0,
              "buy_sell_ratio": 0.5, "cvd": 0, "liquidity_gap": False,
              "summary": ""}

        try:
            # ---- 订单簿深度 ----
            r = requests.get(
                f"https://www.okx.com/api/v5/market/books?instId={symbol}&sz=50",
                proxies=self.proxies, timeout=5,
            )
            book = r.json()
            if book.get("code") != "0" or not book.get("data"):
                return ms

            bids = [(float(b[0]), float(b[1])) for b in book["data"][0].get("bids", [])]
            asks = [(float(a[0]), float(a[1])) for a in book["data"][0].get("asks", [])]

            if not bids or not asks: return ms

            bid1_price, _bid1_qty = bids[0]
            ask1_price, _ask1_qty = asks[0]
            mid_price = (bid1_price + ask1_price) / 2
            ms["mid_price"] = mid_price

            # 1. 价差
            ms["spread"] = round((ask1_price - bid1_price) / bid1_price * 100, 4)

            # 2. 订单簿失衡 (前10档)
            bid_depth_10 = sum(b[1] for b in bids[:10])
            ask_depth_10 = sum(a[1] for a in asks[:10])
            total_10 = bid_depth_10 + ask_depth_10
            ms["imbalance"] = round(bid_depth_10 / total_10, 3) if total_10 > 0 else 0.5

            # 3. 1% 深度
            upper = mid_price * 1.01
            lower = mid_price * 0.99
            depth_1pct = 0.0
            for price, qty in bids:
                if price >= lower: depth_1pct += qty
            for price, qty in asks:
                if price <= upper: depth_1pct += qty
            ms["depth_1pct"] = round(depth_1pct, 1)

            # 4. 流动性断层 (相邻价位 > 0.2%)
            for i in range(1, min(10, len(bids))):
                gap = (bids[i-1][0] - bids[i][0]) / bids[i][0] * 100
                if gap > 0.2:
                    ms["liquidity_gap"] = True
                    break
            if not ms["liquidity_gap"]:
                for i in range(1, min(10, len(asks))):
                    gap = (asks[i][0] - asks[i-1][0]) / asks[i-1][0] * 100
                    if gap > 0.2:
                        ms["liquidity_gap"] = True
                        break

            # 5. 主动买卖比 + CVD (最近成交)
            r2 = requests.get(
                f"https://www.okx.com/api/v5/market/trades?instId={symbol}&limit=100",
                proxies=self.proxies, timeout=5,
            )
            trades = r2.json()
            if trades.get("code") == "0" and trades.get("data"):
                buy_vol = 0.0
                sell_vol = 0.0
                cvd_sum = 0.0
                for t in trades["data"]:
                    sz = float(t[3])
                    px = float(t[2])
                    side = t[4]  # "buy" or "sell"
                    if side == "buy":
                        buy_vol += sz * px
                        cvd_sum += sz
                    else:
                        sell_vol += sz * px
                        cvd_sum -= sz
                total_vol = buy_vol + sell_vol
                ms["buy_sell_ratio"] = round(buy_vol / total_vol, 3) if total_vol > 0 else 0.5
                ms["cvd"] = round(cvd_sum, 1) if abs(cvd_sum) > 1 else 0.0

            # ---- 综合解读 ----
            warnings = []
            if ms["spread"] > 0.1:
                warnings.append(f"价差{ms['spread']:.3f}%偏大，流动性差")
            if ms["imbalance"] > 0.65:
                warnings.append("买盘深度占优，短期偏多")
            elif ms["imbalance"] < 0.35:
                warnings.append("卖盘深度占优，短期偏空")
            if ms["depth_1pct"] < 50000:
                warnings.append(f"1%深度仅{ms['depth_1pct']:.0f}，价格易被推动")
            if ms["liquidity_gap"]:
                warnings.append("⚠️存在流动性断层，止损易被击穿")
            if ms["buy_sell_ratio"] > 0.65:
                warnings.append("主动买入占比高，多头积极")
            elif ms["buy_sell_ratio"] < 0.35:
                warnings.append("主动卖出占比高，空头积极")
            if abs(ms["cvd"]) > 500:
                direction = "多头" if ms["cvd"] > 0 else "空头"
                warnings.append(f"CVD={ms['cvd']:.0f}，{direction}持续放量")
            ms["summary"] = " | ".join(warnings) if warnings else "盘口正常"

        except Exception as e:
            ms["summary"] = f"获取失败: {e}"

        # ATR 波动率: 当前 ATR / 20日均ATR，判断是否异常波动
        try:
            klines = self.kline.fetch_klines(symbol, "1H", 30) if hasattr(self, 'kline') else None
            if klines and len(klines) >= 20:
                atr_now = self._calc_atr(klines[-14:])
                atr_avg = self._calc_atr(klines[-30:])
                ms["atr_ratio"] = round(atr_now / atr_avg, 2) if atr_avg > 0 else 1.0
            else:
                ms["atr_ratio"] = 1.0
        except Exception:
            ms["atr_ratio"] = 1.0

        return ms

    # ================================================================
    # 假突破检测
    # ================================================================

    def _fake_breakout_check(self, symbol: str, direction: str) -> dict:
        """多维度检测假突破"""
        result = {"is_fake": False, "risk_level": "low", "reasons": []}

        try:
            # 价格突破前高
            k = self.kline.fetch_klines(symbol, "1H", 48)
            if len(k) < 20: return result
            closes = [c["close"] for c in k]
            highs = [c["high"] for c in k]
            current = closes[-1]
            prev_high = max(highs[-24:-2])  # 24小时前高（排除最近2根）

            is_breakout = current > prev_high if direction == "long" else current < min(c["low"] for c in k[-24:-2])
            if not is_breakout: return result

            # 多维度验证
            ms = self._microstructure(symbol)
            chain = self._onchain(symbol)

            # CVD 确认
            cvd_confirm = (direction == "long" and ms["cvd"] > 0) or (direction == "short" and ms["cvd"] < 0)
            if not cvd_confirm:
                result["reasons"].append(f"CVD未同步({ms['cvd']:.0f})")
                result["risk_level"] = "high"

            # OI 验证
            if chain["oi_change_pct"] > 15:
                result["reasons"].append(f"OI暴增{chain['oi_change_pct']:.0f}%")
                result["risk_level"] = "high"

            # 费率验证
            if abs(chain["funding_rate"]) > 0.003:
                result["reasons"].append(f"费率极端{chain['funding_rate']*100:.2f}%")
                result["risk_level"] = "high"

            # 价差验证
            if ms["spread"] > 0.08:
                result["reasons"].append(f"价差扩大{ms['spread']:.3f}%")
                result["risk_level"] = "high"

            # 深度失衡
            if direction == "long" and ms["imbalance"] < 0.4:
                result["reasons"].append("ask侧挂单撤单/变薄")
                result["risk_level"] = "high"
            elif direction == "short" and ms["imbalance"] > 0.6:
                result["reasons"].append("bid侧挂单撤单/变薄")
                result["risk_level"] = "high"

            # 综合判断
            high_count = len(result["reasons"])
            if result["risk_level"] == "high" and high_count >= 2:
                result["is_fake"] = True
                result["reasons"].insert(0, f"⚠️高风险假突破: 价格突破但{high_count}个指标不确认")

        except Exception as e:
            result["reasons"].append(f"检测异常: {e}")

        return result

    # ================================================================
    # 市场状态机 — 综合所有维度输出统一决策框架
    # ================================================================

    def _market_regime(self, symbol: str) -> dict:
        """
        综合市场状态评估
        整合: 趋势 + 波动 + OI/费率 + 盘口微观 + 庄家行为
        输出统一决策框架 JSON
        """
        ms = self._microstructure(symbol)
        chain = self._onchain(symbol)
        struct = self._market_structure(symbol)

        k1h = self.kline.fetch_klines(symbol, "1H", 24)
        closes = [c["close"] for c in k1h] if k1h else []
        _current = closes[-1] if closes else 0

        # ---- 判断市场状态 ----
        regime = "range"
        trend_strength = struct.get("trend_strength", "不明")

        # 趋势判定
        if trend_strength in ("强多头", "强空头") or trend_strength in ("偏多头", "偏空头"):
            regime = "trend_up" if "多" in trend_strength else "trend_down"
        elif struct.get("range_bound"):
            regime = "range"

        # 波幅判定
        if k1h and len(k1h) >= 12:
            recent = k1h[-12:]
            h = max(c["high"] for c in recent)
            lo = min(c["low"] for c in recent)
            range_pct = (h - lo) / lo * 100 if lo > 0 else 0
            if range_pct > 8: regime = "squeeze"  # 宽幅震荡=挤压
            if chain["oi_surging"] and regime == "range": regime = "squeeze"

        # 流动性
        if ms["spread"] > 0.15 or ms["depth_1pct"] < 30000:
            regime = "low_liquidity" if regime == "range" else regime

        # 黑天鹅/消息冲击
        if struct.get("dump_warning") and struct.get("pump_warning"):
            regime = "news_shock"

        # 去杠杆
        if chain.get("oi_dropping") and regime == "trend_down":
            regime = "liquidation"

        # ---- 方向偏置 ----
        bias = "neutral"
        if regime in ("trend_up",):
            bias = "long"
        elif regime in ("trend_down", "liquidation"):
            bias = "short"

        # 杠杆拥挤修正
        if chain["signal"] == "crowded_long": bias = "neutral"
        if chain["signal"] == "crowded_short": bias = "neutral"

        # ---- 风险等级 ----
        risk = "medium"
        danger_signals = 0
        if chain["signal"] == "danger": danger_signals += 2
        if regime == "squeeze": danger_signals += 1
        if regime == "low_liquidity": danger_signals += 1
        if regime == "news_shock": danger_signals += 2
        if regime == "liquidation": danger_signals += 1
        if ms["liquidity_gap"]: danger_signals += 1
        if struct.get("pump_warning") or struct.get("dump_warning"): danger_signals += 1

        if danger_signals >= 3: risk = "extreme"
        elif danger_signals >= 2: risk = "high"
        elif danger_signals == 0: risk = "low"

        # ---- 允许动作 ----
        if risk == "extreme":
            allowed = "no_trade"
            pos_mult = 0.0
        elif risk == "high":
            allowed = "reduce"
            pos_mult = 0.3
        elif regime == "low_liquidity":
            allowed = "reduce"
            pos_mult = 0.5
        else:
            allowed = "open"
            pos_mult = 1.0

        # ---- 无效条件 ----
        invalid = []
        if chain["signal"] == "danger": invalid.append("OI暴增+费率极端")
        if regime == "news_shock": invalid.append("消息冲击/异常波动")
        if ms["liquidity_gap"]: invalid.append("流动性断层")
        if ms["spread"] > 0.2: invalid.append(f"价差过大({ms['spread']:.2f}%)")

        # ---- 决策理由 ----
        reasons = []
        reasons.append(f"趋势:{trend_strength}")
        reasons.append(f"OI变化:{chain['oi_change_pct']:.1f}% 费率:{chain['funding_rate']*100:.3f}%")
        reasons.append(f"盘口:{ms['summary']}")
        if struct.get("range_bound"): reasons.append("窄幅横盘,等突破")

        return {
            "market_regime": regime,
            "direction_bias": bias,
            "confidence": round(0.5 + (0.15 * (1 if bias != "neutral" else 0)), 2),
            "risk_level": risk,
            "allowed_action": allowed,
            "position_size_multiplier": pos_mult,
            "reason": " | ".join(reasons),
            "invalid_condition": " | ".join(invalid) if invalid else "无",
        }

    # ================================================================
    # SMC 市场结构分析
    # ================================================================

    def _smc_structure(self, symbol: str) -> dict:
        """SMC 风格市场结构: BOS/CHoCH/OrderBlock/FVG/Liquidity"""
        k1h = self.kline.fetch_klines(symbol, "1H", 72)
        k15m = self.kline.fetch_klines(symbol, "15m", 96)
        smc = {"trend": "range", "bos": [], "choch": None, "order_blocks": [],
               "fvg": [], "liquidity_sweep": None, "summary": ""}

        if len(k1h) < 30 or len(k15m) < 20: return smc

        _closes_1h = [c["close"] for c in k1h]
        highs_1h = [c["high"] for c in k1h]
        lows_1h = [c["low"] for c in k1h]

        # 1. 市场结构识别: HH/HL(多头) or LH/LL(空头)
        swings_high = []; swings_low = []
        for i in range(2, len(k1h)-2):
            if highs_1h[i] > highs_1h[i-1] and highs_1h[i] > highs_1h[i-2] and \
               highs_1h[i] > highs_1h[i+1] and highs_1h[i] > highs_1h[i+2]:
                swings_high.append({"price": highs_1h[i], "idx": i})
            if lows_1h[i] < lows_1h[i-1] and lows_1h[i] < lows_1h[i-2] and \
               lows_1h[i] < lows_1h[i+1] and lows_1h[i] < lows_1h[i+2]:
                swings_low.append({"price": lows_1h[i], "idx": i})

        # 趋势判断
        if len(swings_high) >= 2 and len(swings_low) >= 2:
            recent_hh = swings_high[-2:]
            recent_ll = swings_low[-2:]
            hh_up = recent_hh[-1]["price"] > recent_hh[0]["price"]
            hl_up = recent_ll[-1]["price"] > recent_ll[0]["price"]

            if hh_up and hl_up: smc["trend"] = "bullish"
            elif not hh_up and not hl_up: smc["trend"] = "bearish"
            # CHoCH 检测: 趋势转换
            if smc["trend"] == "bullish" and recent_ll[-1]["price"] < recent_ll[0]["price"] - (recent_ll[0]["price"]*0.005):
                smc["choch"] = {"type": "bearish_choch", "price": recent_ll[-1]["price"],
                                "desc": "低点被刷新,多头结构破坏"}
            elif smc["trend"] == "bearish" and recent_hh[-1]["price"] > recent_hh[0]["price"] + (recent_hh[0]["price"]*0.005):
                smc["choch"] = {"type": "bullish_choch", "price": recent_hh[-1]["price"],
                                "desc": "高点被刷新,空头结构破坏"}

        # 2. Order Block: 趋势反转前的最后一根反向K线
        if len(k1h) >= 20:
            recent = k1h[-20:]
            for i in range(3, len(recent)-1):
                # 大阳线后反转 = 供应区OB
                if recent[i]["close"] > recent[i]["open"] and \
                   (recent[i]["close"]-recent[i]["open"]) > (recent[i]["high"]-recent[i]["low"])*0.6 and \
                   recent[i+1]["close"] < recent[i+1]["open"] and \
                   recent[i+1]["close"] < recent[i]["close"]:
                    smc["order_blocks"].append({
                        "type": "supply", "price": round((recent[i]["high"]+recent[i]["low"])/2, 1),
                        "desc": f"做空OB@{recent[i]['high']:.1f}"})
                    break

            for i in range(3, len(recent)-1):
                if recent[i]["close"] < recent[i]["open"] and \
                   (recent[i]["open"]-recent[i]["close"]) > (recent[i]["high"]-recent[i]["low"])*0.6 and \
                   recent[i+1]["close"] > recent[i+1]["open"] and \
                   recent[i+1]["close"] > recent[i]["close"]:
                    smc["order_blocks"].append({
                        "type": "demand", "price": round((recent[i]["high"]+recent[i]["low"])/2, 1),
                        "desc": f"做多OB@{recent[i]['low']:.1f}"})
                    break

        # 3. FVG (公允价值缺口): 3K线中的跳空
        for i in range(2, min(len(k1h), 48)):
            c1, c2, c3 = k1h[-i-2], k1h[-i-1], k1h[-i]
            # 上涨FVG: c1.high < c3.low (跳空)
            if c1["high"] < c3["low"] and c3["low"] > c2["high"]:
                gap = c3["low"] - c1["high"]
                if gap > c1["high"] * 0.002:  # >0.2%
                    smc["fvg"].append({"type": "bullish_fvg", "top": c3["low"], "bottom": c1["high"],
                                       "desc": f"多头FVG@{c1['high']:.1f}-{c3['low']:.1f}"})
                    if len(smc["fvg"]) >= 2: break
            # 下跌FVG: c1.low > c3.high
            if c1["low"] > c3["high"] and c3["high"] < c2["low"]:
                gap = c1["low"] - c3["high"]
                if gap > c1["low"] * 0.002:
                    smc["fvg"].append({"type": "bearish_fvg", "top": c1["low"], "bottom": c3["high"],
                                       "desc": f"空头FVG@{c3['high']:.1f}-{c1['low']:.1f}"})
                    if len(smc["fvg"]) >= 2: break

        # 4. 流动性猎杀: 快速突破前高/低后反转
        if len(k15m) >= 20:
            recent_15 = k15m[-20:]
            highs_15 = [c["high"] for c in recent_15]
            lows_15 = [c["low"] for c in recent_15]
            prev_high = max(highs_15[:15])
            prev_low = min(lows_15[:15])
            cur_high = highs_15[-1]
            cur_low = lows_15[-1]
            # 上破前高后回落 = 多头陷阱
            if cur_high > prev_high and recent_15[-1]["close"] < prev_high:
                smc["liquidity_sweep"] = {"type": "long_trap", "price": prev_high,
                                          "desc": f"突破前高${prev_high:.1f}后回落,多头陷阱"}
            elif cur_low < prev_low and recent_15[-1]["close"] > prev_low:
                smc["liquidity_sweep"] = {"type": "short_trap", "price": prev_low,
                                          "desc": f"跌破前低${prev_low:.1f}后反弹,空头陷阱"}

        # 综合摘要
        parts = [f"趋势:{smc['trend']}"]
        if smc["choch"]: parts.append(f"CHoCH:{smc['choch']['desc']}")
        if smc["order_blocks"]: parts.append(smc["order_blocks"][0]["desc"])
        if smc["fvg"]: parts.append(smc["fvg"][0]["desc"])
        if smc["liquidity_sweep"]: parts.append(smc["liquidity_sweep"]["desc"])
        smc["summary"] = " | ".join(parts)

        return smc

    # ---- SMC 转AI提示 ----

    def _smc_text(self, smc: dict) -> str:
        lines = [f"SMC趋势: {smc['trend']}"]
        if smc["choch"]:
            lines.append(f"⚠️ 结构转变(CHoCH): {smc['choch']['desc']}")
        if smc["order_blocks"]:
            for ob in smc["order_blocks"]:
                tag = "阻力" if ob["type"]=="supply" else "支撑"
                lines.append(f"OB({tag}): {ob['desc']}")
        if smc["fvg"]:
            for f in smc["fvg"]:
                lines.append(f"FVG缺口: {f['desc']}")
        if smc["liquidity_sweep"]:
            lines.append(f"⚠️ 流动性猎杀: {smc['liquidity_sweep']['desc']}")
        return "\n".join(lines) if len(lines)>1 else "SMC结构不明"

    # ================================================================
    # 市场结构分析 — 庄家行为识别
    # ================================================================

    def _market_structure(self, symbol: str) -> dict:
        """分析市场结构: 趋势强度、插针、横盘、放量异动"""
        k5m = self.kline.fetch_klines(symbol, "5m", 72)
        k15m = self.kline.fetch_klines(symbol, "15m", 64)
        k1h = self.kline.fetch_klines(symbol, "1H", 48)

        result = {"trend_strength": "不明", "wicks": [], "volume_anomaly": False,
                  "range_bound": False, "pump_warning": False, "dump_warning": False}

        if len(k5m) < 30 or len(k1h) < 20:
            return result

        # ---- 趋势强度 (多周期EMA对齐) ----
        closes_1h = [c["close"] for c in k1h]
        ema12 = self.kline.ema(closes_1h, 12)[-1]
        ema26 = self.kline.ema(closes_1h, 26)[-1]
        ema50 = self.kline.ema(closes_1h, min(50, len(closes_1h)))[-1]
        current = closes_1h[-1]

        if current > ema12 > ema26 > ema50:
            result["trend_strength"] = "强多头"
        elif current > ema12 > ema26:
            result["trend_strength"] = "偏多头"
        elif current < ema12 < ema26 < ema50:
            result["trend_strength"] = "强空头"
        elif current < ema12 < ema26:
            result["trend_strength"] = "偏空头"
        else:
            result["trend_strength"] = "震荡"

        # ---- 插针检测 (上下影线 > 实体 3倍) ----
        for c in k5m[-24:]:
            body = abs(c["close"] - c["open"])
            upper_wick = c["high"] - max(c["close"], c["open"])
            lower_wick = min(c["close"], c["open"]) - c["low"]
            price_level = c["close"]

            # 上插针（庄家砸盘前兆）
            if upper_wick > body * 3 and upper_wick > price_level * 0.003:
                result["wicks"].append({
                    "type": "上插针", "price": round(c["high"], 1),
                    "time": datetime.fromtimestamp(c["timestamp"]/1000).strftime("%H:%M"),
                })
            # 下插针（爆多军后拉升）
            if lower_wick > body * 3 and lower_wick > price_level * 0.003:
                result["wicks"].append({
                    "type": "下插针", "price": round(c["low"], 1),
                    "time": datetime.fromtimestamp(c["timestamp"]/1000).strftime("%H:%M"),
                })

        # ---- 横盘检测 (近12根1H振幅<2%) ----
        if len(k1h) >= 12:
            recent = k1h[-12:]
            h_range = (max(c["high"] for c in recent) - min(c["low"] for c in recent))
            mid_price = (max(c["high"] for c in recent) + min(c["low"] for c in recent)) / 2
            if h_range / mid_price < 0.02:
                result["range_bound"] = True  # 横盘，可能在吸筹/派发

        # ---- 放量异动 (量>均值3倍) ----
        vols_5m = [c["volume"] for c in k5m[-36:]]
        avg_vol = sum(vols_5m) / len(vols_5m) if vols_5m else 1
        recent_vols = vols_5m[-6:]
        if avg_vol > 0:
            for v in recent_vols:
                if v > avg_vol * 3:
                    result["volume_anomaly"] = True
                    # 判定方向
                    idx = vols_5m.index(v)
                    if idx >= 0 and idx < len(k5m):
                        change = (k5m[idx]["close"] - k5m[max(0,idx-3)]["close"]) / k5m[max(0,idx-3)]["close"]
                        if change > 0.02: result["pump_warning"] = True
                        elif change < -0.02: result["dump_warning"] = True
                    break

        # ---- 15分钟内急涨/急跌 (>2%) ----
        if len(k15m) >= 6:
            price_15m_ago = k15m[-7]["close"] if len(k15m) >= 7 else k15m[0]["close"]
            change_15m = (k15m[-1]["close"] - price_15m_ago) / price_15m_ago * 100
            if change_15m > 2: result["pump_warning"] = True
            elif change_15m < -2: result["dump_warning"] = True

        return result

    # ---- 结构分析转AI提示 ----

    def _structure_text(self, structure: dict) -> str:
        """把结构分析转成 AI 可读的文字"""
        parts = [f"趋势强度: {structure['trend_strength']}"]

        if structure["range_bound"]:
            parts.append("⚠️ 当前处于窄幅横盘，可能在吸筹/派发，等待放量突破再入场")

        if structure["wicks"]:
            recent = structure["wicks"][-3:]
            for w in recent:
                tag = "⚠️"
                parts.append(f"{tag} {w['time']} {w['type']}${w['price']:.1f}，可能流动性猎杀")

        if structure["pump_warning"]:
            parts.append("🚨 检测到放量急涨！警惕拉高出货")
        if structure["dump_warning"]:
            parts.append("🚨 检测到放量急跌！可能是砸盘吸筹或恐慌抛售")
        if structure["volume_anomaly"] and not structure["pump_warning"] and not structure["dump_warning"]:
            parts.append("📊 放量但方向不明，需观察确认")

        return "\n".join(parts)

    def _blackswan_check(self) -> bool:
        """BTC 10分钟内跌超3% → 黑天鹅"""
        try:
            k = self.kline.fetch_klines("BTC-USDT-SWAP", "1m", 15)
            if len(k) < 12: return False
            now = k[-1]["close"]
            ago10 = k[-11]["close"]
            drop = (now - ago10) / ago10 * 100
            return drop < -3.0
        except Exception: return False

    # ================================================================
    # 时段检测
    # ================================================================

    def _current_session(self) -> str:
        """判断当前时段"""
        beijing_hour = (datetime.utcnow().hour + 8) % 24
        if SESSIONS["overlap"][0] <= beijing_hour < SESSIONS["overlap"][1]:
            return "overlap"
        elif SESSIONS["us"][0] <= beijing_hour < SESSIONS["us"][1]:
            return "us"
        elif SESSIONS["eu"][0] <= beijing_hour < SESSIONS["eu"][1]:
            return "eu"
        else: return "asia"

    def _session_adjust(self, params: dict) -> dict:
        """时段自适应调参 — 用历史胜率动态调整"""
        p = dict(params)
        session = self._current_session()
        # 从历史数据学习时段偏好
        try:
            stats = self.logger.get_session_stats(days=30)
            sess_stat = stats.get(session, {})
            if sess_stat.get("total", 0) >= 5:  # ≥5笔才有统计意义
                wr = sess_stat["win_rate"]
                if wr > 0.60:
                    p["min_confidence"] -= 5  # 高胜率时段放宽
                    print(f"[时段] {session} 胜率{wr:.0%} → 放宽置信度")
                elif wr < 0.35:
                    p["min_confidence"] += 15  # 低胜率时段大幅收紧
                    print(f"[时段] {session} 胜率{wr:.0%} → 收紧置信度")
                return p
        except Exception:
            pass  # fallback 到硬编码
        # 数据不足时用硬编码
        if session == "overlap":
            p["min_confidence"] -= 3
        elif session == "asia":
            p["min_confidence"] += 5
            p["min_rr"] += 0.3
        return p

    # ================================================================
    # 引擎1: 4H宏观组合信号 — 隔夜期货+费率+时段
    # ================================================================

    def _engine1_macro_signal(self, symbol: str) -> dict:
        """引擎1: BTC 4H方向 + 资金费率 + 上午时段 → 组合信号
        P(LONG赢 | BTC涨, 费率<0, 8-12AM) 条件贝叶斯
        """
        result = {"signal": "none", "confidence_boost": 0, "reason": ""}
        chain = self._onchain(symbol)
        rate = chain["funding_rate"]
        session = self._current_session()
        bj_hour = (datetime.utcnow().hour + 8) % 24

        # 条件1: 8-12 AM 北京时段
        is_morning = 8 <= bj_hour < 12
        if not is_morning:
            return result

        # 条件2: 隔夜 BTC 4H 方向
        try:
            k = self.kline.fetch_klines("BTC-USDT-SWAP", "4H", 3)
            if len(k) >= 3:
                btc_prev = k[-2]["close"]
                btc_before = k[-3]["close"]
                btc_up = btc_prev > btc_before
            else:
                btc_up = False
        except Exception:
            btc_up = False

        # 条件3: 资金费率 < 0 (空头拥挤)
        funding_neg = rate < 0

        # 条件贝叶斯查询
        cond = {"btc_4h": "up" if btc_up else "down",
                "funding": "neg" if funding_neg else "pos",
                "session": session}
        combo_p = self.bayesian.conditional_probability("direction::LONG", cond, min_samples=3)
        if combo_p is not None and combo_p > 0.55:
            result["confidence_boost"] = int((combo_p - 0.5) * 40)  # 0.55→+2, 0.7→+8
            result["signal"] = "LONG"
            result["reason"] = f"引擎1: BTC{'涨' if btc_up else '跌'}+费率{rate*100:.2f}%+{session} → P(LONG)={combo_p:.0%}"
            return result

        # 如果组合样本不足，用逻辑判断
        if btc_up and funding_neg:
            result["signal"] = "LONG"
            result["confidence_boost"] = 8
            result["reason"] = f"引擎1: BTC涨+负费率{session} → 高确信LONG"
        elif funding_neg:
            result["signal"] = "LONG"
            result["confidence_boost"] = 5
            result["reason"] = f"引擎1: 负费率{session} → 倾向LONG"

        return result

    # ================================================================
    # 爆仓计算
    # ================================================================

    @staticmethod
    def _calc_atr(klines: list, period: int = 14) -> float:
        """计算 ATR (Average True Range)"""
        if len(klines) < period:
            return 1.0
        trs = []
        for i in range(1, len(klines)):
            h = klines[i][2] if len(klines[i]) > 2 else klines[i].get("high", 0)
            lo = klines[i][3] if len(klines[i]) > 3 else klines[i].get("low", 0)
            pc = klines[i-1][4] if len(klines[i-1]) > 4 else klines[i-1].get("close", 0)
            try:
                h, lo, pc = float(h), float(lo), float(pc)
            except (ValueError, TypeError):
                continue
            tr = max(h - lo, abs(h - pc), abs(lo - pc))
            trs.append(tr)
        return sum(trs[-period:]) / len(trs[-period:]) if trs else 1.0

    def _check_pending_orders(self, send):
        """检查限价单是否超时未成交，超时则取消并通知"""
        expired = []
        now = time.time()
        for oid, info in list(self.pending_orders.items()):
            if now - info["time"] > info["timeout"]:
                # 检查是否已成交
                positions = self.okx.get_positions()
                filled = any(p["instId"] == info["symbol"] for p in positions)
                if not filled:
                    self.okx.close_position(info["symbol"])  # 取消挂单
                    if not info["notified"]:
                        send(f"⏰ *限价单超时已取消*\n"
                             f"📌 {info['symbol']} | 挂单${info['entry']:,.1f}\n"
                             f"⏱ 超时{info['timeout']}秒未成交，已撤销")
                expired.append(oid)
        for oid in expired:
            self.pending_orders.pop(oid, None)

    @staticmethod
    def liq_price(entry: float, lev: int, direction: str) -> float:
        if direction == "long": return entry * (1.0 - 1.0/lev + 0.005)
        return entry * (1.0 + 1.0/lev - 0.005)

    @staticmethod
    def liq_safe(entry: float, sl: float, lev: int, direction: str) -> tuple:
        liq = AutoTrader.liq_price(entry, lev, direction)
        if direction == "long":
            if sl <= liq: return False, liq, 0
            buf = (sl - liq) / liq * 100
        else:
            if sl >= liq: return False, liq, 0
            buf = (liq - sl) / liq * 100
        return buf >= 0.5, liq, buf

    # ================================================================
    # 移动止盈
    # ================================================================

    def _check_trailing(self, send):
        """检查所有持仓，盈利达标则推止损到成本价"""
        positions = self.okx.get_positions()
        for pos in positions:
            if pos["upl"] <= 0: continue
            pnl_pct = pos["upl"] / pos["margin"] * 100 if pos["margin"] > 0 else 0
            if pnl_pct > 1.5 and pos["instId"] not in self.breakeven_moved:
                # 推止损到成本价 = 均价，方向正确
                send(f"🔒 [{pos['instId']}] 浮盈{pnl_pct:.1f}%, 止损推到成本价")
                self.breakeven_moved[pos["instId"]] = True
                # 注意: OKX 需要通过 amend order 来移动止损，这里简化标记

    # ================================================================
    # 关联性过滤
    # ================================================================

    def _btc_correlation_check(self, symbol: str, direction: str) -> tuple:
        """BTC 大跌时山寨不单独做多"""
        if "BTC" in symbol: return True, ""
        try:
            k = self.kline.fetch_klines("BTC-USDT-SWAP", "1H", 24)
            if len(k) < 10: return True, ""
            btc_change = (k[-1]["close"] - k[-6]["close"]) / k[-6]["close"] * 100
            if btc_change < -2.0 and direction == "long":
                return False, f"BTC 6H跌{btc_change:.1f}%, 不做多山寨"
        except Exception: pass
        return True, ""

    # ================================================================
    # 双 AI 校验
    # ================================================================

    def _dual_ai_decision(self, symbol: str, mode: str, params: dict) -> Optional[dict]:
        """快速AI + 深度AI 方向一致才通过
        管线: 市场状态→交易可行性→AI方向判断→风控闸门"""
        adjusted = self._session_adjust(params)

        # 引擎1: 4H宏观组合信号 (仅短线模式)
        engine1 = {"signal": "none", "confidence_boost": 0, "reason": ""}
        if mode == "swing":
            engine1 = self._engine1_macro_signal(symbol)
            if engine1["confidence_boost"] != 0:
                print(f"[引擎1] {engine1['reason']}")

        # === 第0步: 市场状态机 ===
        regime = self._market_regime(symbol)
        if regime["allowed_action"] == "no_trade":
            return {"action": "WAIT", "reason": f"市场不可交易: {regime['invalid_condition']}",
                    "mode": mode, "regime": regime}
        if regime["risk_level"] == "high":
            adjusted["min_confidence"] += 5   # 高风险时更严
            adjusted["min_rr"] += 0.5

        market = self._market(symbol, params["timeframes"])
        if not market: return None

        main = list(market.values())[-1]
        current = main.get("price", 0)
        if current <= 0: return None

        chain = self._onchain(symbol)
        positions = self.okx.get_positions()
        balance = self.okx.get_balance()
        equity = balance.get("equity", 0)
        pos_count = len(positions)
        has_pos = any(p["instId"] == symbol for p in positions)
        session = self._current_session()

        tf_text = []
        for tf, d in market.items():
            tr = "多" if d["ema20"] > d["ema50"] else "空"
            st = d.get("supertrend",{})
            adx = d.get("adx",{})
            st_tag = "ST↑" if st.get("trend")=="up" else "ST↓"
            adx_tag = f"ADX{adx.get('adx',25):.0f}({'强' if adx.get('trend','') in ('strong_up','strong_down') else '弱'})"
            tf_text.append(f"{tf}:${d['price']:.1f} RSI{d['rsi']:.0f} {tr} {st_tag} {adx_tag} 量{d['vol_ratio']:.1f}x")

        # ---- 快速 AI (轻量 prompt) ----
        quick_sys = "你是短线交易员。快速判断方向。只输出: LONG|SHORT|WAIT"
        quick_prompt = (
            f"[{mode}] {symbol} ${current:.1f} RSI{main.get('rsi',50):.0f} "
            f"{'多头' if main.get('ema20',0)>main.get('ema50',0) else '空头'}"
        )
        quick_resp = self._call_ds(quick_sys, quick_prompt, 10).strip().upper()
        quick_dir = quick_resp if quick_resp in ("LONG","SHORT","WAIT") else "WAIT"

        if quick_dir == "WAIT":
            return {"action":"WAIT","reason":"快速AI判断观望","market":main,"mode":mode}

        # ---- 深度 AI (完整分析) ----
        safety = (
            f"=== 硬规则 ===\n0.⛔仅逐仓\n1.杠杆≤{self.safety['max_leverage']}x 风险≤{adjusted['risk_pct']}%\n"
            f"2.止损必设|距爆仓≥0.5%\n3.盈亏比≥{adjusted['min_rr']}:1\n"
            f"4.{'⚠️已持有,仅浮盈可加仓' if has_pos else '✅可开仓'}\n"
            f"5.时段:{session}{'(好时段)' if session in ('overlap','us') else '(平淡)'}\n"
            f"6.持仓分析: OI变化{chain['oi_change_pct']:.1f}% | 费率{chain['funding_rate']*100:.3f}% | {chain['explanation']}\n"
            f"7.净值{equity:.0f}U|仓位{pos_count}/{adjusted['max_positions']}\n"
        )

        # 市场状态
        is_weekend = datetime.now().weekday() >= 5
        btc_market = self._market("BTC-USDT-SWAP", ["1H","4H"])
        btc_1h = btc_market.get("1H", {})
        btc_4h = btc_market.get("4H", {})
        btc_trend = "多头" if btc_4h.get("ema20",0) > btc_4h.get("ema50",0) else "空头"
        _btc_change_6h = ((btc_1h.get("price",0) - btc_4h.get("low",0)) / btc_4h.get("low",1) * 100) if btc_4h.get("low") else 0

        # SMC 结构分析
        smc = self._smc_structure(symbol)
        smc_text = self._smc_text(smc)

        # 庄家行为分析
        structure = self._market_structure(symbol)
        structure_text = self._structure_text(structure)

        market_ctx = (
            f"=== 市场环境 ===\n"
            f"BTC趋势: {btc_trend} | {'周末低流动性' if is_weekend else '工作日正常'}\n"
            f"BTC关键位: 75000(强支撑) 80000(强阻力) 85000(中期阻力)\n"
        )

        # 清算热力图数据
        liq_summary = self.liq_tracker.summary(symbol, current)
        liq_ctx = f"=== 清算数据 ===\n{liq_summary}" if liq_summary else ""

        # 因子挖掘信号（轻量增强）
        factor_ctx = ""
        if self._factor_miner is None and DEEPSEEK_API_KEY:
            self._factor_miner = FactorMiner()
        if self._factor_miner:
            factor_score = self._factor_miner.quick_scan(symbol)
            if factor_score and factor_score["weight"] > 0:
                f_dir = factor_score["direction"]
                f_weight = factor_score["weight"]
                f_details = ", ".join(d["factor"].split(":")[-1] for d in factor_score["details"][:5])
                factor_ctx = (
                    f"=== 因子信号 ===\n"
                    f"综合方向: {f_dir} | 置信度: {f_weight:.2f}\n"
                    f"活跃因子: {f_details}\n"
                    f"(因子信号仅作参考,不改变止损止盈决策)\n"
                )
                # 因子方向与 AI 方向一致时加分
                if f_dir == "long" or f_dir == "short":
                    adjusted["min_confidence"] -= 2

        deep_sys = (
            "你是一个加密货币实盘交易决策系统。\n\n"
            "你的目标不是频繁交易，也不是预测每一次涨跌，而是在风险可控的前提下识别高质量交易机会。\n\n"
            "*核心原则:*\n"
            "1.保住本金 > 2.控制回撤 > 3.避免错误交易 > 4.寻找盈利机会\n"
            "5.不确定 → no_trade\n"
            "6.不允许单一指标开仓\n"
            "7.不允许无止损/无失效条件/无风控批准时开仓\n"
            "8.不允许高滑点/高延迟/低流动性/数据异常时开仓\n"
            "9.风控优先级永远高于交易信号\n\n"
            "*market_regime 必须从以下选择:*\n"
            "trend_up/trend_down/range/fake_breakout/liquidation_cascade/\n"
            "long_squeeze/short_squeeze/stop_hunt/accumulation/distribution/\n"
            "news_shock/low_liquidity/high_volatility/unclear\n\n"
            "*强制规则:*\n"
            "regime=unclear → no_trade\n"
            "risk_level=extreme → no_trade\n"
            "confidence<70 → no_trade\n"
            "data_quality=poor → no_trade\n"
            "无stop_loss → no_trade\n"
            "无invalid_condition → no_trade\n\n"
            "*策略规则(10条):*\n"
            "1.突破关键位但量/CVD/OI/深度不确认→假突破,不追\n"
            "2.涨+OI升+费率极端→多头拥挤禁多;跌+OI升+费率负→空头拥挤禁空\n"
            "3.上方空头密集清算+CVD→轻仓多;下方多头清算+盘口薄→降多仓\n"
            "4.主动买大但价不涨→吸收禁多;主动卖大但价不跌→吸收禁空\n"
            "5.合约涨现货不跟→降信心;现货量放大→提权重\n"
            "6.IV急升/skew/到期→降仓位;gamma集中→回避\n"
            "7.稳定币交易所余额升→购买力改善;余额降→降多信心\n"
            "8.解锁/项目方转CEX/DEX降→禁多;社交热+大户出货→接盘风险\n"
            "9.API延迟/断流/滑点异常→禁开新仓\n"
            "10.日亏/回撤/连亏超阈值→信号全部失效\n"
            "11.相关性: BTC+ETH+SOL同向=3x系统性风险;多个山寨同向=集中风险;必须考虑现有仓位方向再开仓\n\n"
            "*SMC规则:*\n"
            "BOS(结构突破)+CHoCH(趋势转变)→顺势\n"
            "触及OB(订单块)+确认反转→入场\n"
            "FVG(公允价值缺口)→价格回补缺口时入场\n"
            "流动性猎杀(假突破)→反方向交易\n"
            "HH/HL=多头 LH/LL=空头,结构不变不逆势\n\n"
            f"快速AI判断:{quick_dir} | 当前regime:{regime['market_regime']} | "
            f"风险:{regime['risk_level']} | 偏置:{regime['direction_bias']}\n"
            f"允许:{regime['allowed_action']} | 仓位系数:{regime['position_size_multiplier']}\n\n"
            "必须输出严格JSON，不允许自然语言。格式:\n"
            '{"action":"LONG|SHORT|WAIT","market_regime":"str","direction_bias":"str",'
            '"confidence":int,"risk_level":"str","leverage":int,"entry":float,'
            '"stop_loss":float,"take_profit":float,"trailing_pct":float,'
            '"invalid_condition":"str","data_quality":"good|ok|poor","reason":"str"}'
        )
        # K线形态
        k1h = self.kline.fetch_klines(symbol, "1H", 10)
        patterns = self._candle_patterns(k1h) if k1h else []
        pattern_text = ", ".join(p["desc"] for p in patterns) if patterns else "无明显形态"

        # 多TF融合打分
        tf_score_long = self._multi_tf_score(market, "long")
        tf_score_short = self._multi_tf_score(market, "short")

        # Paul Wei 交易模式参考
        paul_ctx = self.paul_wei.get_context_for_ai()

        deep_prompt = (f"=== {mode} [{symbol}] ===\n"+"\n".join(tf_text)+"\n\n"
                       f"=== SMC结构 ===\n{smc_text}\n\n"
                       f"=== 市场结构 ===\n{structure_text}\n\n"
                       f"K线形态: {pattern_text}\n"
                       f"多TF融合: 多头={tf_score_long:.2f} 空头={tf_score_short:.2f}\n\n"
                       +market_ctx+"\n"+liq_ctx+"\n"+factor_ctx+"\n"+paul_ctx+"\n\n"+safety+"\n分析并输出JSON。")

        deep_resp = self._call_ds(deep_sys, deep_prompt, 1536)
        if not deep_resp: return None

        try:
            s = max(deep_resp.find("{"),0)
            e = deep_resp.rfind("}")+1
            decision = json.loads(deep_resp[s:e])
        except Exception: return None

        action = decision.get("action","WAIT").upper()
        data_quality = decision.get("data_quality", "ok")

        # 新字段校验
        regime_ai = decision.get("market_regime", "")
        if regime_ai == "unclear":
            return {"action":"WAIT","reason":"AI判断市场状态unclear","market":main,"mode":mode}
        if data_quality == "poor":
            return {"action":"WAIT","reason":"数据质量poor","market":main,"mode":mode}
        if not decision.get("invalid_condition"):
            return {"action":"WAIT","reason":"未指定失效条件","market":main,"mode":mode}

        # ---- 双校验：方向必须一致 ----
        if action != quick_dir and action != "WAIT" and quick_dir != "WAIT":
            return {"action":"WAIT","reason":f"双AI分歧(快:{quick_dir} vs 深:{action})","market":main,"mode":mode}

        if action == "WAIT":
            return {"action":"WAIT","reason":decision.get("reason",""),"market":main,"mode":mode}

        # 贝叶斯调整: 根据历史条件概率修正置信度
        b_cond = {
            "session": self._current_session(),
            "direction": action,
            "smc_trend": smc.get("trend", ""),
            "dual_ai": "agree" if quick_dir == action else "diverged",
        }
        bayes_adj = self.bayesian.get_confidence_adjustment(b_cond)
        if bayes_adj != 0:
            print(f"[贝叶斯] 置信度调整 {bayes_adj:+d}: {b_cond}")

        # 校验参数
        conf = int(decision.get("confidence",0)) + bayes_adj
        conf = max(10, min(100, conf))  # 夹在10-100
        if conf < adjusted["min_confidence"]:
            return {"action":"WAIT", "reason":f"自信{conf}<{adjusted['min_confidence']}", "market":main, "mode":mode}

        try:
            entry = float(decision["entry"]); sl = float(decision["stop_loss"])
            tp = float(decision["take_profit"])
            lev = min(int(decision.get("leverage",params["leverage"])), self.safety["max_leverage"])
            direction = action.lower()
        except Exception: return None

        if (direction=="long" and sl>=entry) or (direction=="short" and sl<=entry): return None

        rr = abs(tp-entry)/abs(sl-entry) if abs(sl-entry)>0 else 0
        if rr < adjusted["min_rr"]:
            return {"action":"WAIT","reason":f"盈亏比{rr:.1f}<{adjusted['min_rr']}","market":main,"mode":mode}

        safe, liq, buf = self.liq_safe(entry, sl, lev, direction)
        if not safe:
            return {"action":"WAIT","reason":f"止损距爆仓仅{buf:.1f}%","market":main,"mode":mode}

        # 关联性过滤
        corr_ok, corr_reason = self._btc_correlation_check(symbol, direction)
        if not corr_ok:
            return {"action":"WAIT","reason":corr_reason,"market":main,"mode":mode}

        # 引擎1宏观信号: 调整置信度
        if engine1["confidence_boost"] != 0:
            if engine1["signal"] == action:
                conf = min(100, conf + engine1["confidence_boost"])
            elif engine1["signal"] in ("LONG", "SHORT"):
                conf = max(10, conf - 5)  # 引擎1方向冲突, 减信心

        # Paul Wei 模式契合度
        pw_alignment = self.paul_wei.get_alignment_score({
            "leverage": lev,
            "rr_ratio": rr,
            "is_limit_order": True,
            "symbol": symbol,
        })
        pw_score = pw_alignment.get("score", 50)
        if pw_score < 30:
            conf -= 10  # 严重偏离 Paul Wei 模式，降低信心
        elif pw_score >= 70:
            conf = min(100, conf + 5)  # 高度契合，加信心

        return {"action":action,"direction":direction,"mode":mode,"entry":entry,
                "stop_loss":sl,"take_profit":tp,"leverage":lev,"confidence":conf,
                "trailing_pct":float(decision.get("trailing_pct",params["trailing_pct"])),
                "risk_reward":round(rr,2),"liquidation":round(liq,1),
                "liq_buffer":round(buf,1),"risk_pct":adjusted["risk_pct"],
                "reason":decision.get("reason",""),"market":main,"regime":regime,
                "paul_wei_score": pw_score}

    # ================================================================
    # 执行
    # ================================================================

    # ================================================================
    # 综合风控闸门
    # ================================================================

    def _risk_gate(self, d: dict, send) -> tuple:
        """
        执行前过风控闸门，8 条硬规则
        返回: (通过, 拒绝原因)
        """
        balance = self.okx.get_balance()
        equity = balance.get("equity", 0)
        if equity <= 0: return False, "账户权益异常"

        # ---- 0. 交易所健康 ----
        ex_health = self._exchange_health()
        if ex_health.get("error"):
            return False, f"交易所不可用: {ex_health['summary']}"

        # ---- 1. 日亏损 > 2%: 停止开仓 ----
        today_pnl = self.logger.get_today_pnl()
        if equity > 0 and today_pnl < 0 and abs(today_pnl) / equity > 0.02:
            return False, f"日亏损{abs(today_pnl)/equity*100:.1f}%>2%，今日停止开仓"

        # ---- 2. 总回撤 > 5%: 减仓50% ----
        # 用当日累积盈亏近似
        drawdown_pct = abs(today_pnl) / equity * 100 if today_pnl < 0 and equity > 0 else 0
        if drawdown_pct > 8:
            return False, f"总回撤{drawdown_pct:.1f}%>8%，停止实盘"

        # ---- 3. 连续亏损 > 3: 冷却期 ----
        if self.consecutive_losses >= 3:
            return False, f"连续亏损{self.consecutive_losses}笔，冷却期"

        # ---- 4. OI+费率: 禁止拥挤方向追单 / 大波动降仓 ----
        chain = self._onchain(d["symbol"])
        sig = chain["signal"]
        if sig == "danger":
            return False, f"OI暴增+费率极端，大波动前兆: {chain['explanation']}"
        if sig == "crowded_long" and d["direction"] == "long":
            return False, f"多头拥挤: {chain['explanation']}"
        if sig == "crowded_short" and d["direction"] == "short":
            return False, f"空头拥挤: {chain['explanation']}"

        # ---- 4.5. 假突破检测 ----
        fb = self._fake_breakout_check(d["symbol"], d["direction"])
        if fb["is_fake"]:
            return False, "; ".join(fb["reasons"])

        # ---- 5. 策略健康: 严重失效则停止 ----
        if self._last_health and self._last_health.get("status") == "stop":
            return False, f"策略失效: {self._last_health['message']}"

        # ---- 6. 单笔预估滑点 > 0.15%: 限价单放宽 ----
        try:
            r = requests.get(
                f"https://www.okx.com/api/v5/market/ticker?instId={d['symbol']}",
                proxies=self.proxies, timeout=5,
            )
            ticker = r.json()
            if ticker.get("code") == "0" and ticker.get("data"):
                bid = float(ticker["data"][0].get("bidPx", 0))
                ask = float(ticker["data"][0].get("askPx", 0))
                if bid > 0 and ask > 0:
                    spread = (ask - bid) / bid * 100
                    if spread > 0.3:  # 双向滑点>0.15%*2
                        # 限价单放宽容忍度，不禁止
                        pass
        except Exception: pass

        # ---- 6. 盘口深度下降 > 50%: 禁止追单 ----
        try:
            r = requests.get(
                f"https://www.okx.com/api/v5/market/books?instId={d['symbol']}&sz=25",
                proxies=self.proxies, timeout=5,
            )
            book = r.json()
            if book.get("code") == "0" and book.get("data"):
                bids = sum(float(b[1]) for b in book["data"][0].get("bids", [])[:10])
                asks = sum(float(a[1]) for a in book["data"][0].get("asks", [])[:10])
                total_depth = bids + asks
                if total_depth < 50000:  # 深度极低
                    return False, f"盘口深度不足({total_depth:.0f})，禁止交易"
        except Exception: pass

        # ---- 7. 数据延迟 > 3秒: 禁止交易 ----
        # 用 OKX 服务器时间比较
        try:
            r = requests.get("https://www.okx.com/api/v5/public/time", proxies=self.proxies, timeout=3)
            server_time = int(r.json()["data"][0]["ts"]) / 1000
            local_time = __import__('time').time()
            if abs(local_time - server_time) > 5:
                return False, f"数据延迟{abs(local_time-server_time):.0f}s>5s，禁止交易"
        except Exception: pass

        # ---- 8. Kill Switch: 盘口/流动性突变 ----
        ms = self._microstructure(d["symbol"])
        if ms["spread"] > 0.3:
            return False, f"价差异常扩大({ms['spread']:.2f}%)，可能重大事件/维护"
        if ms["depth_1pct"] < 10000:
            return False, f"深度极低({ms['depth_1pct']:.0f})，禁止交易"
        if ms["liquidity_gap"]:
            return False, "流动性断层，止损可能被击穿"

        # ---- 9. 总回撤 > 5%: 减仓50%（在execute里处理） ----
        return True, ""

    # ================================================================
    # 免费外部数据聚合
    # ================================================================

    def _free_data(self, symbol: str) -> dict:
        """聚合免费外部数据: 恐慌指数/Coinbase溢价/期权/funding"""
        fd = {"fear_greed": 50, "coinbase_premium": 0, "btc_dominance": 0,
              "options_skew": 0, "summary": ""}
        base = symbol.replace("-USDT-SWAP","").replace("-SWAP","")

        # 1. Fear & Greed Index (免费)
        try:
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            fd["fear_greed"] = int(r.json()["data"][0]["value"])
        except Exception: pass

        # 2. Coinbase BTC溢价 (免费)
        try:
            r = requests.get("https://api.exchange.coinbase.com/products/BTC-USD/ticker", timeout=5)
            cb = float(r.json()["price"])
            fd["coinbase_premium"] = round((cb - fd.get("okx_btc", cb)) / cb * 100, 3) if fd.get("okx_btc") else 0
        except Exception: pass

        # 3. BTC Dominance (免费 CoinGecko)
        try:
            r = requests.get("https://api.coingecko.com/api/v3/global", timeout=5)
            fd["btc_dominance"] = round(r.json()["data"]["market_cap_percentage"]["btc"], 1)
        except Exception: pass

        # 4. OKX 期权偏斜 (免费, 如果有期权数据)
        if base in ("BTC","ETH"):
            try:
                r = requests.get(
                    f"https://www.okx.com/api/v5/public/opt-summary?"
                    f"uly={base}-USD&family={base}-USD", proxies=self.proxies, timeout=5,
                )
                opt = r.json()
                if opt.get("code")=="0" and opt.get("data"):
                    fd["has_options"] = True
            except Exception: pass

        fd["summary"] = (f"恐慌{fd['fear_greed']} | "
                         f"BTC占比{fd['btc_dominance']}%")
        return fd

    # ================================================================
    # 交易所健康检查
    # ================================================================

    def _exchange_health(self) -> dict:
        """交易所运维风险检测"""
        health = {"ok": True, "warnings": [], "error": False}

        # 1. API 延迟
        try:
            t0 = time.time()
            r = requests.get("https://www.okx.com/api/v5/public/time", proxies=self.proxies, timeout=3)
            latency = (time.time() - t0) * 1000
            health["api_latency_ms"] = round(latency, 0)
            if latency > 3000: health["warnings"].append(f"API延迟{latency:.0f}ms>3s"); health["ok"] = False
            elif latency > 1000: health["warnings"].append(f"API延迟偏高{latency:.0f}ms")
        except Exception: health["warnings"].append("API不可达"); health["error"] = True; health["ok"] = False

        # 2. 交易所状态 — 仅拦截影响合约交易的维护
        try:
            r = requests.get("https://www.okx.com/api/v5/system/status", proxies=self.proxies, timeout=5)
            status = r.json()
            if status.get("code") == "0":
                for s in status.get("data", []):
                    title = s.get("title", "")
                    state = s.get("state", "")
                    if state != "ongoing" and state != "scheduled":
                        continue
                    # 只关心影响合约/永续/SWAP/衍生品的维护
                    relevant_keywords = ["swap", "futures", "contract", "perpetual",
                                         "derivative", "合约", "永续", "交割", "衍生品",
                                         "trading system", "order", "match", "交易系统",
                                         "撮合", "下单"]
                    is_relevant = any(kw in title.lower() for kw in relevant_keywords)
                    if is_relevant:
                        tag = "维护中" if state == "ongoing" else "计划维护"
                        health["warnings"].append(f"{tag}: {title}")
                        if state == "ongoing":
                            health["ok"] = False
                    else:
                        # 无关维护（如跟单、理财等），不拦截
                        pass
        except Exception: pass

        # 3. 保险基金
        try:
            r = requests.get("https://www.okx.com/api/v5/public/insurance-fund?type=all",
                             proxies=self.proxies, timeout=5)
            fund = r.json()
            if fund.get("code") == "0":
                total = sum(float(f.get("balance", 0)) for f in fund.get("data", []))
                health["insurance_fund"] = round(total / 1e6, 1)  # 百万美元
                if total < 1e7: health["warnings"].append("保险基金偏低")
        except Exception: pass

        health["summary"] = " | ".join(health["warnings"]) if health["warnings"] else "交易所状态正常"
        return health

    # ================================================================
    # 高级市场数据 (liquidation/基差/成交量分布/滑点)
    # ================================================================

    def _liquidation_heat(self, symbol: str) -> dict:
        """清算热力图 — 最近爆仓数据"""
        try:
            r = requests.get(
                f"https://www.okx.com/api/v5/public/liquidation-orders?"
                f"instId={symbol}&instType=SWAP&limit=50",
                proxies=self.proxies, timeout=5,
            )
            data = r.json()
            if data.get("code") != "0" or not data.get("data"):
                return {"total_loss": 0, "long_liq": 0, "short_liq": 0, "warning": False}

            longs = sum(float(o["sz"]) for o in data["data"] if o["posSide"] == "long")
            shorts = sum(float(o["sz"]) for o in data["data"] if o["posSide"] == "short")
            warning = max(longs, shorts) > 50000
            return {
                "total_loss": round(longs + shorts, 1),
                "long_liq": round(longs, 1), "short_liq": round(shorts, 1),
                "warning": warning,
                "summary": f"多爆{longs:.0f} | 空爆{shorts:.0f}" +
                          (" ⚠️爆仓激增" if warning else ""),
            }
        except Exception: return {"total_loss": 0, "long_liq": 0, "short_liq": 0, "warning": False, "summary": ""}

    def _cross_exchange(self, symbol: str) -> dict:
        """跨交易所价差: OKX vs Binance vs Bybit"""
        base = symbol.replace("-USDT-SWAP","").replace("-SWAP","")
        result = {"okx":0,"binance":0,"bybit":0,"max_divergence":0,"summary":""}
        try:
            # OKX
            r = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}",
                             proxies=self.proxies, timeout=3)
            if r.json().get("code")=="0": result["okx"]=float(r.json()["data"][0]["last"])
        except Exception: pass
        try:
            # Binance
            sym = f"{base}USDT"
            r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={sym}",
                             proxies=self.proxies, timeout=3)
            result["binance"] = float(r.json()["price"])
        except Exception: pass
        try:
            # Bybit
            r = requests.get(f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={base}USDT",
                             proxies=self.proxies, timeout=3)
            d = r.json()
            if d.get("retCode")==0 and d.get("result","list"):
                result["bybit"] = float(d["result"]["list"][0]["lastPrice"])
        except Exception: pass

        prices = [v for v in [result["okx"],result["binance"],result["bybit"]] if v>0]
        if len(prices)>=2:
            result["max_divergence"] = round((max(prices)-min(prices))/min(prices)*100, 3)
            result["summary"] = f"价差{result['max_divergence']:.3f}% "+\
                ("⚠️跨所分歧" if result["max_divergence"]>0.5 else "✓一致")
        return result

    def _perp_spot_basis(self, symbol: str) -> dict:
        """合约-现货基差"""
        try:
            spot_sym = symbol.replace("-SWAP", "-USDT")
            r1 = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={symbol}",
                              proxies=self.proxies, timeout=5)
            r2 = requests.get(f"https://www.okx.com/api/v5/market/ticker?instId={spot_sym}",
                              proxies=self.proxies, timeout=5)
            perp = r1.json()
            spot = r2.json()
            if perp.get("code") == "0" and spot.get("code") == "0":
                perp_px = float(perp["data"][0]["last"])
                spot_px = float(spot["data"][0]["last"])
                basis_pct = (perp_px - spot_px) / spot_px * 100 if spot_px > 0 else 0
                return {
                    "perp_price": perp_px, "spot_price": spot_px,
                    "basis_pct": round(basis_pct, 4),
                    "summary": f"基差{basis_pct:+.4f}% " +
                              ("(期货溢价)" if basis_pct > 0.05 else "(期货折价)" if basis_pct < -0.05 else "(正常)"),
                }
        except Exception: pass
        return {"basis_pct": 0, "summary": ""}

    def _volume_profile(self, symbol: str) -> dict:
        """成交量分布 — 最近24H的主要成交密集区"""
        k = self.kline.fetch_klines(symbol, "1H", 24)
        if len(k) < 12: return {"poc": 0, "val": 0, "vah": 0, "summary": ""}

        volumes = [c["volume"] for c in k]
        prices = [(c["high"] + c["low"] + c["close"]) / 3 for c in k]
        total_vol = sum(volumes)
        if total_vol == 0: return {"poc": 0, "val": 0, "vah": 0, "summary": ""}

        # POC = 最大成交量价格点
        max_vol_idx = volumes.index(max(volumes))
        poc = prices[max_vol_idx]

        # VA = 70% 成交量区域
        sorted_data = sorted(zip(prices, volumes, strict=False), key=lambda x: x[0])
        cum = 0
        val = vah = poc
        for p, v in sorted_data:
            cum += v
            if cum <= total_vol * 0.15: val = p
            if cum <= total_vol * 0.85: vah = p

        return {
            "poc": round(poc, 1), "val": round(val, 1), "vah": round(vah, 1),
            "summary": f"POC:{poc:.0f} VA:{val:.0f}-{vah:.0f}",
        }

    def _advanced_market_report(self, symbol: str) -> str:
        """综合高级市场报告"""
        liq = self._liquidation_heat(symbol)
        basis = self._perp_spot_basis(symbol)
        vp = self._volume_profile(symbol)
        ms = self._microstructure(symbol)
        chain = self._onchain(symbol)

        lines = [
            "*🔬 高级市场数据*",
            f"爆仓: {liq.get('summary','?')}",
            f"基差: {basis.get('summary','?')}",
            f"成交量分布: {vp.get('summary','?')}",
            f"价差: {ms['spread']:.3f}% | 深度: {ms['depth_1pct']:.0f}",
            f"买卖比: {ms['buy_sell_ratio']:.2f} | CVD: {ms['cvd']:.0f}",
            f"OI变化: {chain['oi_change_pct']:.1f}% | 费率: {chain['funding_rate']*100:.3f}%",
        ]

        # 综合风险提示
        warnings = []
        if liq.get("warning"): warnings.append("爆仓激增,停止接飞刀")
        if abs(basis.get("basis_pct", 0)) > 0.5: warnings.append("基差异常")
        if ms["spread"] > 0.15: warnings.append("高滑点")
        if ms["liquidity_gap"]: warnings.append("流动性断层")
        if chain["signal"] == "danger": warnings.append("OI+费率极端")

        if warnings:
            lines.append(f"\n⚠️ *风险:* {' | '.join(warnings)}")

        return "\n".join(lines)

    # ================================================================
    # 相关性风险 + 账户风险汇总
    # ================================================================

    # BTC 与其他币种的近似相关系数 (基于历史数据估算)
    BTC_CORRELATION: ClassVar[dict] = {
        "ETH": 0.85, "SOL": 0.78, "BNB": 0.72, "XRP": 0.55,
        "DOGE": 0.45, "ADA": 0.60, "AVAX": 0.70, "LINK": 0.65, "DOT": 0.68,
    }

    def _correlation_exposure(self, positions: list) -> dict:
        """计算相关性调整后的净风险敞口"""
        if not positions: return {"net_delta":0,"gross_exposure":0,"corr_warning":"无持仓"}

        gross = sum(abs(p["margin"]) for p in positions)
        # 净方向敞口: 多头+ 空头-
        net = 0
        longs_btc = 0
        for p in positions:
            base = p["instId"].replace("-USDT-SWAP","").replace("-SWAP","")
            corr = self.BTC_CORRELATION.get(base, 0.5)
            direction = 1 if p["side"] == "long" else -1
            # 折算成 BTC 等效敞口
            btc_equiv = p["margin"] * abs(float(p.get("lever", self.safety["max_leverage"]))) * direction * corr
            net += btc_equiv
            if direction > 0: longs_btc += p["margin"]

        warning = ""
        # 相关性集中风险
        if longs_btc > 0 and gross > 0 and longs_btc/gross > 0.8:
            warning = "⚠️ 80%+仓位同向(多),系统性风险高"
        elif len(positions) >= 3:
            longs = sum(1 for p in positions if p["side"]=="long")
            if longs == len(positions) or longs == 0:
                warning = f"⚠️ {len(positions)}个仓位全部同向,无对冲"

        return {
            "net_delta": round(net, 0), "gross_exposure": round(gross, 0),
            "corr_warning": warning if warning else "✓仓位分散",
            "position_count": len(positions),
        }

    def _account_risk_summary(self) -> dict:
        """账户综合风险快照"""
        balance = self.okx.get_balance()
        positions = self.okx.get_positions()
        equity = balance.get("equity", 0)
        margin_used = sum(p["margin"] for p in positions)
        _margin_pct = margin_used / equity * 100 if equity > 0 else 0

        # 杠杆风险
        total_notional = sum(p["margin"] * abs(float(p.get("lever", 1))) for p in positions)

        # 相关性
        corr = self._correlation_exposure(positions)

        # 日风险
        today_pnl = self.logger.get_today_pnl()
        max_dd = abs(today_pnl) / equity * 100 if today_pnl < 0 and equity > 0 else 0

        warnings = []
        if _margin_pct > 30: warnings.append(f"保证金占用{_margin_pct:.0f}%偏高")
        if self.consecutive_losses >= 2: warnings.append(f"连亏{self.consecutive_losses}笔")
        if max_dd > 3: warnings.append(f"日回撤{max_dd:.1f}%")
        if corr["corr_warning"].startswith("⚠"): warnings.append(corr["corr_warning"])

        risk_level = "low"
        if len(warnings) >= 3: risk_level = "extreme"
        elif len(warnings) >= 2: risk_level = "high"
        elif len(warnings) >= 1: risk_level = "medium"

        return {
            "equity": equity, "margin_used": round(margin_used, 0),
            "margin_pct": round(_margin_pct, 1), "total_notional": round(total_notional, 0),
            "leverage_ratio": round(total_notional/equity, 1) if equity>0 else 0,
            "correlation": corr, "daily_pnl": today_pnl,
            "max_drawdown_pct": round(max_dd, 1),
            "consecutive_losses": self.consecutive_losses,
            "warnings": warnings, "risk_level": risk_level,
        }

    # ================================================================
    # 综合风险报告
    # ================================================================

    def _risk_report(self, d: dict) -> str:
        """每笔交易前的完整风险评估报告"""
        balance = self.okx.get_balance()
        equity = balance.get("equity", 0)
        positions = self.okx.get_positions()
        today_pnl = self.logger.get_today_pnl()
        chain = self._onchain(d["symbol"])
        ms = self._microstructure(d["symbol"])
        regime = d.get("regime", {})

        # 仓位统计
        total_margin = sum(p["margin"] for p in positions)
        _margin_pct = total_margin / equity * 100 if equity > 0 else 0
        _total_lev = sum(p.get("lever", 0) for p in positions)

        # 最大回撤近似
        max_dd = abs(today_pnl) / equity * 100 if today_pnl < 0 and equity > 0 else 0

        # 本次交易风险
        entry = d["entry"]
        sl = d["stop_loss"]
        lev = d["leverage"]
        sl_dist = abs(entry - sl) / entry * 100
        max_loss_pct = sl_dist * lev
        qty_risk = d.get("quantity", 1)

        lines = []

        # === 1. 账户风险 ===
        acct = self._account_risk_summary()
        corr = acct["correlation"]
        lines.append("*1. 账户风险*")
        lines.append(f"总仓位: {len(positions)}个 | 保证金: {acct['margin_pct']:.1f}% | 杠杆率: {acct['leverage_ratio']:.1f}x")
        lines.append(f"净值: {equity:.0f}U | 敞口: {acct['total_notional']:.0f}U")
        lines.append(f"相关性: {corr['corr_warning']} | 净Delta: {corr['net_delta']:.0f}")
        lines.append(f"连续亏损: {acct['consecutive_losses']}笔 | 风险等级: {acct['risk_level']}")
        lines.append(f"今日盈亏: {today_pnl:+.2f}U | 最大回撤: {max_dd:.1f}%")
        lines.append(f"连续亏损: {self.consecutive_losses}笔")

        # === 2. 交易风险 ===
        lines.append("\n*2. 交易风险*")
        lines.append(f"最大亏损: {max_loss_pct:.1f}% ({sl_dist:.2f}%x{lev})")
        lines.append(f"止损距离: {sl_dist:.2f}% {'✓合理' if sl_dist < 3 else '⚠️偏大'}")
        lines.append(f"杠杆: {lev}x {'✓合理' if lev <= 10 else '⚠️偏高'}")
        lines.append(f"仓位: {qty_risk}张")
        today_loss_limit = equity * 0.02
        trade_max_loss = max_loss_pct / 100 * equity
        lines.append(f"本次最大亏: {trade_max_loss:.1f}U | 日限额: {today_loss_limit:.0f}U"
                     f" {'✓' if trade_max_loss < today_loss_limit else '⚠️超限'}")

        # === 3. 市场风险 ===
        lines.append("\n*3. 市场风险*")
        lines.append(f"市场状态: {regime.get('market_regime','?')} | 风险: {regime.get('risk_level','?')}")
        lines.append(f"波动: {'高' if ms['spread'] > 0.08 else '正常'} | "
                     f"深度: {ms['depth_1pct']:.0f} {'✓' if ms['depth_1pct'] > 30000 else '⚠️不足'}")
        lines.append(f"费率: {chain['funding_rate']*100:.3f}% "
                     f"{'⚠️极端' if abs(chain['funding_rate']) > 0.005 else '✓正常'}")
        lines.append(f"OI变化: {chain['oi_change_pct']:.1f}% "
                     f"{'⚠️异常' if abs(chain['oi_change_pct']) > 15 else '✓正常'}")
        lines.append(f"CVD: {ms['cvd']:.0f} | 买卖比: {ms['buy_sell_ratio']:.2f}")
        lines.append(f"价差: {ms['spread']:.3f}% | 断层: {'有' if ms['liquidity_gap'] else '无'}")
        # 高级数据
        liq = self._liquidation_heat(d["symbol"])
        basis = self._perp_spot_basis(d["symbol"])
        vp = self._volume_profile(d["symbol"])
        if liq.get("warning"): lines.append(f"⚠️爆仓激增: {liq['summary']}")
        if abs(basis.get("basis_pct", 0)) > 0.3: lines.append(f"⚠️基差: {basis['summary']}")
        if vp.get("poc"): lines.append(f"VA: {vp['val']:.0f}-{vp['vah']:.0f}")

        # === 4. 决策判定 ===
        lines.append("\n*4. 强制规则检查*")
        checks = []
        checks.append(f"{'✅' if today_pnl > -equity*0.02 else '❌'}日亏<2%")
        checks.append(f"{'✅' if max_dd < 5 else '❌'}回撤<5%")
        checks.append(f"{'✅' if max_dd < 8 else '❌'}回撤<8%")
        checks.append(f"{'✅' if self.consecutive_losses < 3 else '❌'}连亏<3")
        checks.append(f"{'✅' if sl > 0 else '❌'}止损已设")
        checks.append(f"{'✅' if d['confidence'] >= 70 else '❌'}信心≥70")
        checks.append(f"{'✅' if regime.get('risk_level') != 'extreme' else '❌'}非extreme")
        lines.append(" | ".join(checks))

        if all("✅" in c for c in checks):
            lines.append("\n✅ *全部通过，允许交易*")
        else:
            lines.append("\n❌ *存在未通过项，交易被拦截*")

        return "\n".join(lines)

    def execute(self, d: dict, send) -> bool:
        if not AUTO_TRADE: send("⏸ 自动交易关闭"); return False
        if self.blackout_until and datetime.now() < self.blackout_until:
            send("⛔ 黑天鹅保护中"); return False

        # 综合风控闸门（快速拒绝）
        passed, reason = self._risk_gate(d, send)
        if not passed:
            send(f"⛔ 风控拦截: {reason}")
            return False

        equity = self.okx.get_balance().get("equity",0)
        positions = self.okx.get_positions()
        if len(positions) >= self.safety["max_positions_total"]:
            send("⛔ 仓位已满"); return False

        self.okx.set_leverage(d["symbol"], d["leverage"])

        # 获取盘口微观数据（后续多处使用）
        ms = self._microstructure(d["symbol"])

        # ================================================================
        # 凯利仓位: f* = (p×b−q) / b
        confidence = d.get("confidence", 50)
        p = confidence / 100                 # 胜率估计
        b = max(d.get("risk_reward", 1.5), 1.1)  # 盈亏比 (最低1.1防止除零)
        q = 1 - p
        kelly_f = (p * b - q) / b
        kelly_f = max(0.005, min(kelly_f, 0.05))  # 夹0.5%-5% (quarter-Kelly安全边际)
        risk_pct = kelly_f * 0.5                   # Half-Kelly 再折半
        risk_amount = equity * risk_pct
        price_risk = abs(d["entry"]-d["stop_loss"])
        ct_val = 0.001 if "BTC" in d["symbol"] else 0.01
        qty = max(1, int(risk_amount/(price_risk*ct_val)))

        # ================================================================
        # 波动率过滤: ATR%极端时跳过
        atr_ratio = ms.get("atr_ratio", 1.0)
        atr_1h = ms.get("atr_val", 0)
        if atr_1h > 0:
            atr_pct = atr_1h / d["entry"] * 100
            if atr_pct > 5.0:
                send(f"🌋 波动率极高(ATR{atr_pct:.1f}%)，暂停交易"); return False
            if atr_pct > 3.0:
                qty = max(1, int(qty * 0.3))
                send(f"⚠️ 高波动(ATR{atr_pct:.1f}%)，仓位压缩至30%")
        # ATR比率自适应
        if atr_ratio > 2.0:
            qty = max(1, int(qty * 0.5))
            send(f"⚠️ ATR波动率极高({atr_ratio:.1f}x)，仓位减半")
        elif atr_ratio > 1.5:
            qty = max(1, int(qty * 0.75))
        elif atr_ratio < 0.6:
            qty = min(qty, int(qty * 1.2))
        # 市场状态仓位系数
        regime = d.get("regime", {})
        if regime:
            qty = max(1, int(qty * regime.get("position_size_multiplier", 1.0)))

        # 总回撤 > 5%: 减仓50%
        today_pnl = self.logger.get_today_pnl()
        if equity > 0 and today_pnl < 0 and abs(today_pnl) / equity > 0.05:
            qty = max(1, qty // 2)
            send("⚠️ 总回撤>5%，仓位减半")
        # 周末降仓
        if datetime.now().weekday() >= 5:
            qty = max(1, qty // 2)
        # 同向仓位叠加→减仓 (相关性风险)
        corr = self._correlation_exposure(positions)
        if corr.get("corr_warning", "").startswith("⚠") and not d.get("add_position"):
            qty = max(1, int(qty * 0.5))
            send(f"⚠️ 同向仓位叠加,仓位减半: {corr['corr_warning']}")
        # 策略健康减仓
        if self._last_health and self._last_health.get("status") == "reduce":
            qty = max(1, qty // 2)
            send("⚠️ 策略表现不佳，仓位减半")
        # 连亏缩仓
        if self.consecutive_losses >= 3:
            qty = max(1, qty // 2)

        d["quantity"] = qty

        # === 执行质量检查 ===
        # entry_deviation calculated but reserved for future use

        # 盘口薄 → 禁止市价
        if ms["depth_1pct"] < 30000:
            send(f"❌ 盘口深度不足({ms['depth_1pct']:.0f})，取消交易")
            return False

        # 波动剧烈 → 限价优先
        if ms["spread"] > 0.1:
            # 放宽限价容忍度
            d["entry"] = ms.get("mid_price", d["entry"]) * 0.998 if d["direction"] == "long" else ms.get("mid_price", d["entry"]) * 1.002

        # 价格偏离入场区间 > 1%
        current_mid = ms.get("mid_price", 0)
        if current_mid > 0 and abs(current_mid - d["entry"]) / d["entry"] > 0.01:
            send(f"❌ 价格偏离{abs(current_mid-d['entry'])/d['entry']*100:.1f}%，取消交易")
            return False

        # 大仓位分批提示
        if qty > 100:
            send(f"⚠️ 大仓位({qty}张)，建议分批执行")

        # 清算区止损验证: 止损不能太靠近清算聚集区
        clusters = self.liq_tracker.nearest_cluster(d["symbol"], d["entry"])
        if clusters["below"] and d["direction"] == "long":
            cluster_px = clusters["below"]["price"]
            sl_dist_pct = abs(d["stop_loss"] - cluster_px) / d["entry"] * 100
            if sl_dist_pct < 0.3:
                send(f"⚠️ 止损距下方清算聚集区仅{sl_dist_pct:.2f}%，做市商可能扫止损")
        if clusters["above"] and d["direction"] == "short":
            cluster_px = clusters["above"]["price"]
            sl_dist_pct = abs(d["stop_loss"] - cluster_px) / d["entry"] * 100
            if sl_dist_pct < 0.3:
                send(f"⚠️ 止损距上方清算聚集区仅{sl_dist_pct:.2f}%，做市商可能扫止损")

        ok, msg, oid = self.okx.place_order(
            d["symbol"], d["direction"], qty,
            stop_loss=d["stop_loss"], take_profit=d["take_profit"],
            ord_type="limit", limit_price=d["entry"],
        )

        if ok:
            dc = "做多" if d["direction"]=="long" else "做空"
            em = "⚡" if d["mode"]=="scalp" else "📊"
            mode_cn = "超短线" if d["mode"]=="scalp" else "短线"
            # 关键分析摘要
            regime_info = d.get("regime", {})
            analysis_summary = (
                f"📊 *{mode_cn} {d['symbol']} {dc}* | 置信{d['confidence']}/100\n\n"
                f"💰 入场: ${d['entry']:,.4f} | {qty}张 | {d['leverage']}x杠杆\n"
                f"🛑 止损: ${d['stop_loss']:,.4f} | 🎯 止盈: ${d['take_profit']:,.4f}\n"
                f"📐 盈亏比: {d['risk_reward']}:1 | 爆仓距: {d['liq_buffer']:.1f}%\n\n"
                f"🧠 *分析:* {d.get('reason','')}\n\n"
                f"🏷 市场状态: {regime_info.get('market_regime','')} | "
                f"风险: {regime_info.get('risk_level','')}\n"
                f"📍 偏置: {regime_info.get('direction_bias','')}"
            )
            send(f"{em} {analysis_summary}")
            # 追踪限价单
            timeout = 120 if d["mode"] == "scalp" else 300
            self.pending_orders[oid] = {
                "symbol": d["symbol"], "entry": d["entry"], "qty": qty,
                "time": time.time(), "timeout": timeout, "notified": False,
            }
        else:
            send(f"⛔ [{d['mode']}] 下单失败: {msg}")
        return ok

    # ================================================================
    # 连亏追踪
    # ================================================================

    def _update_loss_streak(self, send):
        trades = self.logger.get_today_trades()
        closed = [t for t in trades if t[9]=="closed"]
        streak = 0
        for t in reversed(closed):
            pnl = t[13] or 0
            if pnl < 0: streak += 1
            else: break
        self.consecutive_losses = streak
        if streak >= 5:
            self.reduced_mode = True
            send("🔴 连亏5笔! 暂停1小时")
            threading.Timer(3600, self._clear_reduced, args=[send]).start()
        elif streak >= 3:
            send(f"🟡 连亏{streak}笔, 仓位减半")

    def _clear_reduced(self, send):
        self.reduced_mode = False
        self.consecutive_losses = 0
        send("🟢 交易恢复")

    # ================================================================
    # AI 策略健康诊断
    # ================================================================

    def _strategy_health_check(self, send) -> dict:
        """分析最近20笔交易，诊断策略是否失效"""
        trades = self.logger.get_today_trades()
        closed = [t for t in trades if t[9] == "closed"]
        # 也查昨天数据
        all_trades = closed
        recent = all_trades[-20:] if len(all_trades) >= 20 else all_trades

        if len(recent) < 5:
            return {"status": "ok", "message": "交易太少，暂不评估"}

        pnls = [t[13] for t in recent if t[13] is not None]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total = len(pnls)
        win_rate = len(wins) / total * 100 if total > 0 else 0
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(abs(loss) for loss in losses) / len(losses) if losses else 0
        profit_factor = sum(wins) / sum(abs(loss) for loss in losses) if losses else float('inf')
        total_pnl = sum(pnls)

        # 最大连续亏损
        max_consec = 0
        cur_consec = 0
        for p in pnls:
            if p < 0: cur_consec += 1
            else: cur_consec = 0
            max_consec = max(max_consec, cur_consec)

        # 最大回撤 (累计PNL的峰谷差)
        cum = 0; peak = 0; max_dd = 0
        for p in pnls:
            cum += p
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd

        # ---- 诊断 ----
        issues = []
        severity = "normal"

        if win_rate < 40:
            issues.append(f"胜率仅{win_rate:.0f}% (<40%)，信号质量严重下降")
            severity = "critical"
        elif win_rate < 50:
            issues.append(f"胜率{win_rate:.0f}%偏低")
            severity = "warning"

        if profit_factor < 1.0 and profit_factor != float('inf'):
            issues.append(f"盈亏比{profit_factor:.2f} (<1)，亏多赚少")
            severity = "critical"

        if max_consec >= 3:
            issues.append(f"最大连续亏损{max_consec}笔")

        if max_dd > 100 and total > 5:
            issues.append(f"最大回撤{max_dd:.1f}U，策略可能失效")

        # 策略状态判定
        status = "ok"
        message = ""
        if severity == "critical":
            status = "stop"
            message = "策略严重失效，建议停止实盘"
        elif severity == "warning":
            status = "reduce"
            message = "策略表现不佳，建议降低仓位50%"
        elif win_rate >= 55 and profit_factor >= 2.0:
            status = "good"
            message = f"策略表现良好 (胜率{win_rate:.0f}%, 盈亏比{profit_factor:.2f})"

        result = {
            "status": status, "message": message,
            "win_rate": win_rate, "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
            "max_consecutive_loss": max_consec, "max_drawdown": round(max_dd, 1),
            "total_trades": total, "total_pnl": round(total_pnl, 2),
            "issues": issues,
        }

        # 报告
        report = (
            f"🩺 *AI 策略健康诊断*\n\n"
            f"样本: {total}笔 | 胜率: {win_rate:.0f}% | 盈亏比: {profit_factor:.2f}\n"
            f"均盈: {avg_win:+.2f}U | 均亏: -{avg_loss:.2f}U | 总盈亏: {total_pnl:+.2f}U\n"
            f"最大连亏: {max_consec}笔 | 最大回撤: {max_dd:.1f}U\n"
        )
        if issues:
            report += "\n*⚠️ 发现问题:*\n" + "\n".join(f"• {i}" for i in issues)
        report += f"\n\n*判定: {message}*"

        send(report)
        return result

    # ================================================================
    # 每日优化 + 周报
    # ================================================================

    def daily_optimize(self, send):
        trades = self.logger.get_today_trades()
        closed = [t for t in trades if t[9]=="closed"]
        if len(closed)<2: return

        pnls = [t[13] for t in closed if t[13] is not None]
        wins = sum(1 for p in pnls if p>0)
        total = len(pnls)
        wr = wins/total*100

        sys = ("你是量化交易参数优化专家。基于交易表现给出JSON:"
               '{"scalp_leverage":int,"scalp_risk":float,"scalp_conf":int,'
               '"swing_leverage":int,"swing_risk":float,"swing_conf":int,"advice":"str"}')

        # Paul Wei 参考指标
        pw_summary = self.paul_wei.get_summary()

        prompt = (f"今日{total}笔|胜率{wr:.0f}%|盈亏{sum(pnls):+.2f}U|"
                  f"超短线:杆{self.scalp['leverage']}x险{self.scalp['risk_pct']}%信{self.scalp['min_confidence']}|"
                  f"短线:杆{self.swing['leverage']}x险{self.swing['risk_pct']}%信{self.swing['min_confidence']}|"
                  f"边界:杆≤{self.safety['max_leverage']}x险≤{self.safety['max_risk_pct']}%|"
                  f"PaulWei参考: {pw_summary}")

        resp = self._call_ds(sys, prompt, 800)
        if not resp: return
        try:
            s = max(resp.find("{"),0); e = resp.rfind("}")+1
            opt = json.loads(resp[s:e])
            for mode, keys in [("scalp",self.scalp), ("swing",self.swing)]:
                for k, v in [("leverage",int(opt.get(f"{mode}_leverage",keys["leverage"]))),
                             ("risk_pct",float(opt.get(f"{mode}_risk",keys["risk_pct"]))),
                             ("min_confidence",int(opt.get(f"{mode}_conf",keys["min_confidence"])))]:
                    _prev = keys[k]
                    keys[k] = max(1,min(self.safety["max_leverage"],v)) if k=="leverage" else \
                              max(0.1,min(self.safety["max_risk_pct"],v)) if k=="risk_pct" else \
                              max(55,min(85,v))
            send(f"🔄 *每日优化完成*\n💡 {opt.get('advice','')}")
        except Exception: pass

    def weekly_report(self, send):
        """周日21点跑周报"""
        now = datetime.now()
        if now.weekday() != 6 or now.hour != 21: return  # 周日 21:00

        trades = self.logger.get_today_trades()
        sys = "你是量化交易周报分析师。用中文总结本周表现，给改进方向。400字。"
        prompt = f"本周交易明细({len(trades)}笔)\n请给周报。"
        resp = self._call_ds(sys, prompt, 800)
        if resp: send(f"📊 *AI 周报*\n\n{resp}")

    # ================================================================
    # 动态选币
    # ================================================================

    def _candidate_coins(self) -> list:
        """按成交量+流动性动态筛选可交易币种，返回排序列表"""
        candidates = []
        try:
            r = requests.get(
                "https://www.okx.com/api/v5/market/tickers?instType=SWAP",
                proxies=self.proxies, timeout=10,
            )
            data = r.json()
            if data.get("code") != "0": return TOP_COINS

            all_tickers = data.get("data", [])
            filtered = []

            for t in all_tickers:
                inst_id = t.get("instId", "")
                if not inst_id.endswith("-USDT-SWAP"): continue
                base = inst_id.replace("-USDT-SWAP", "")

                vol_24h = float(t.get("vol24h", 0))
                ask_px = float(t.get("askPx", 0))
                bid_px = float(t.get("bidPx", 0))
                ask_sz = float(t.get("askSz", 0))
                bid_sz = float(t.get("bidSz", 0))
                open_px = float(t.get("open24h", 0))

                if vol_24h < 5_000_000: continue  # 日成交量 < 5M USDT 跳过
                if ask_px <= 0 or bid_px <= 0: continue
                spread = (ask_px - bid_px) / bid_px * 100
                if spread > 0.3: continue  # 价差 > 0.3% 跳过 (流动性差)
                if open_px <= 0: continue
                change_24h = (ask_px - open_px) / open_px * 100

                # 排除极端波动 (>±25%)
                if abs(change_24h) > 25: continue

                # 流动性过滤：盘口第一档名义价值均需 >= 10000 USDT
                ask_notional = ask_sz * ask_px
                bid_notional = bid_sz * bid_px
                if ask_notional < 10000 or bid_notional < 10000: continue

                filtered.append({
                    "symbol": inst_id, "base": base,
                    "volume": vol_24h, "spread": round(spread, 4),
                    "change_24h": round(change_24h, 1),
                })

            # 按成交量降序排列
            filtered.sort(key=lambda x: x["volume"], reverse=True)
            candidates = filtered[:20]  # 取前20候选

        except Exception as e:
            print(f"[选币] 异常: {e}")
            return [{"symbol": s, "base": s.replace("-USDT-SWAP",""), "volume": 0, "spread": 0.1, "change_24h": 0} for s in TOP_COINS]

        if candidates: return candidates
        # fallback: 补全必需字段，防止 _pick_best_candidates KeyError
        return [{"symbol": s, "base": s.replace("-USDT-SWAP",""), "volume": 0, "spread": 0.1, "change_24h": 0} for s in TOP_COINS]

    def _pick_best_candidates(self, candidates: list, count: int, mode: str) -> list:
        """AI 从候选池中选出最佳交易机会"""
        if len(candidates) <= count: return candidates

        # 简要评分每个候选
        scored = []
        for coin in candidates:
            market = self._market(coin["symbol"], ["1H"])
            m1h = market.get("1H", {})
            score = 50

            # 成交量加分
            vol = coin.get("volume", 0)
            if vol > 50_000_000: score += 15
            elif vol > 20_000_000: score += 10
            elif vol > 10_000_000: score += 5

            # 有趋势加分 (ADX>25)
            adx = m1h.get("adx", {}).get("trend", "")
            if adx in ("strong_up", "strong_down"): score += 20
            elif adx == "weak": score -= 10

            # 波动适中加分
            bb_w = m1h.get("bb_width", 5)
            if 3 < bb_w < 12: score += 10
            elif bb_w >= 15: score -= 5

            # RSI 不极端
            rsi = m1h.get("rsi", 50)
            if 30 < rsi < 70: score += 5

            # SuperTrend 一致加分
            st = m1h.get("supertrend", {})
            if st.get("trend") != "unknown": score += 5

            scored.append({"coin": coin, "score": score})

        scored.sort(key=lambda x: x["score"], reverse=True)
        return [s["coin"] for s in scored[:count]]

    # ================================================================
    # 主循环
    # ================================================================

    def run_loop(self, send):
        self._running = True
        last_opt = date.today()
        last_scalp = last_swing = last_blackswan = last_trail = last_weekly = last_health = 0
        self._last_health = None

        print(f"[AI] v5 引擎启动 | 超短{self.scalp['interval']//60}分 短线{self.swing['interval']//3600}时 | 清算追踪已就绪")

        # 启动清算追踪
        self.liq_tracker.start()

        while self._running:
            try:
                now_ts = time.time()
                now_dt = datetime.now()
                today = date.today()

                # 每日优化
                if today != last_opt:
                    send("🤖 每日自动优化...")
                    self.daily_optimize(send)
                    last_opt = today

                # 因子挖掘 (每天一次，UTC 0点)
                if now_dt.hour == 0 and now_ts - getattr(self, '_last_factor_run', 0) > 3600:
                    self._last_factor_run = now_ts
                    if self._factor_miner is None and DEEPSEEK_API_KEY:
                        self._factor_miner = FactorMiner()
                    if self._factor_miner:
                        print("🔬 启动每日因子挖掘...")
                        try:
                            self._factor_miner.run_discovery(
                                symbols=["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"],
                                send=send,
                            )
                        except Exception as e:
                            print(f"因子挖掘异常: {e}")

                # 周报
                if now_dt.weekday() == 6 and now_dt.hour == 21 and now_ts - last_weekly > 3600:
                    self.weekly_report(send)
                    last_weekly = now_ts

                # 交易所健康 (每10分钟)
                if now_ts - getattr(self, '_last_ex_check', 0) > 600:
                    self._last_ex_check = now_ts
                    ex = self._exchange_health()
                    if ex["warnings"]:
                        send(f"⚠️ 交易所: {ex['summary']}")

                # 黑天鹅检测 (每5分钟)
                if now_ts - last_blackswan > 300:
                    last_blackswan = now_ts
                    if self._blackswan_check():
                        self.blackout_until = datetime.now() + timedelta(minutes=30)
                        send("🚨 *黑天鹅警报* BTC 10分钟跌超3%! 暂停30分钟")

                # 黑天鹅期间跳过
                if self.blackout_until and datetime.now() < self.blackout_until:
                    time.sleep(30); continue
                elif self.blackout_until and datetime.now() >= self.blackout_until:
                    self.blackout_until = None
                    send("🟢 黑天鹅保护解除")

                # 策略健康诊断 (每4小时)
                if now_ts - last_health > 14400:
                    last_health = now_ts
                    self._last_health = self._strategy_health_check(send)

                # 移动止盈 + 限价单超时检查 (每2分钟)
                if now_ts - last_trail > 120:
                    last_trail = now_ts
                    self._check_trailing(send)
                    self._check_pending_orders(send)

                # 连亏追踪
                self._update_loss_streak(send)

                # 选币 (每轮刷新)
                candidates = self._candidate_coins()
                scalps = self._pick_best_candidates(candidates, 5, "scalp")
                swings = self._pick_best_candidates(candidates, 8, "swing")

                # 超短线
                if now_ts - last_scalp >= self.scalp["interval"] and self.scalp["enabled"] \
                   and not self.reduced_mode:
                    last_scalp = now_ts
                    for coin in scalps:
                        if not self._running: break
                        sym = coin["symbol"]
                        d = self._dual_ai_decision(sym, "scalp", self.scalp)
                        if d and d["action"]!="WAIT":
                            ok = self.execute(d, send)
                            emoji = "✅" if ok else "⛔"
                            status = "已下单" if ok else "被拦截"
                            send(f"{emoji} *[超短]* {sym} {d['action']} | 信{d['confidence']} | {status}\n"
                                 f"💵 入场: ${d['entry']:,.4f} | 🛑 止损: ${d['stop_loss']:,.4f}\n"
                                 f"🎯 止盈: ${d['take_profit']:,.4f} | 📐 盈亏比: {d['risk_reward']}:1\n"
                                 f"💬 {d['reason']}")

                # 短线
                if now_ts - last_swing >= self.swing["interval"] and self.swing["enabled"] \
                   and not self.reduced_mode:
                    last_swing = now_ts
                    for coin in swings:
                        if not self._running: break
                        sym = coin["symbol"]
                        d = self._dual_ai_decision(sym, "swing", self.swing)
                        if d and d["action"]!="WAIT":
                            ok = self.execute(d, send)
                            emoji = "✅" if ok else "⛔"
                            status = "已下单" if ok else "被拦截"
                            send(f"{emoji} *[短线]* {sym} {d['action']} | 信{d['confidence']} | {status}\n"
                                 f"💵 入场: ${d['entry']:,.4f} | 🛑 止损: ${d['stop_loss']:,.4f}\n"
                                 f"🎯 止盈: ${d['take_profit']:,.4f} | 📐 盈亏比: {d['risk_reward']}:1\n"
                                 f"💬 {d['reason']}")

                time.sleep(5)

            except Exception as ex:
                import traceback
                print(f"[循环] {ex}")
                traceback.print_exc()
                time.sleep(60)

    def _calc_direction_score(self, market, chain, smc, liq_text, structure) -> dict:
        """规则评分: 综合多维度给出方向建议"""
        score = 0
        direction = "WAIT"
        details = []

        # 1. 多周期 EMA 对齐 (25分)
        tf_bullish = sum(1 for tf in ["1H","4H","1D"]
                         if tf in market and market[tf].get("ema20",0) > market[tf].get("ema50",0))
        if tf_bullish == 3:
            score += 25; direction = "LONG"; details.append("3周期EMA多头对齐")
        elif tf_bullish == 2:
            score += 18; details.append("2周期EMA偏多")
        elif tf_bullish == 1:
            score += 8; details.append("EMA方向分歧")
        else:
            score += 25; direction = "SHORT"; details.append("3周期EMA空头对齐")

        # 2. MACD 动量 (15分)
        macd_bullish = 0
        for tf in ["1H","4H","1D"]:
            if tf in market:
                m = market[tf].get("macd", {})
                if m.get("is_bullish"): macd_bullish += 1
                if m.get("crossed_up"): details.append(f"{tf} MACD金叉")
                if m.get("crossed_down"): details.append(f"{tf} MACD死叉")
        if macd_bullish >= 2:
            score += 15
        elif macd_bullish == 1:
            score += 8
        else:
            score += 15 if direction == "SHORT" else 10

        # 3. 布林带位置 (10分)
        main = market.get("1D", next(iter(market.values())))
        bb_range = main.get("bb_upper", 0) - main.get("bb_lower", 0)
        if bb_range > 0:
            price_pos = (main.get("price", 0) - main.get("bb_lower", 0)) / bb_range
            if price_pos < 0.2:
                score += 10; details.append("价格触及布林下轨(超卖)")
            elif price_pos > 0.8:
                score += 10; details.append("价格触及布林上轨(超买)")
            elif 0.4 <= price_pos <= 0.6:
                score += 5; details.append("布林中轨附近(中性)")

        # 4. 链上数据 (15分)
        rate = chain.get("funding_rate", 0)
        oi_change = chain.get("oi_change_pct", 0)
        if rate > 0.01 and oi_change > 5:
            details.append("费率偏高+OI增(多头拥挤)")
        elif rate < -0.005 and oi_change > 3:
            details.append("负费率+OI增(空头拥挤)")
            score += 10
        elif abs(rate) < 0.005:
            score += 8; details.append("费率中性")
        score += min(7, max(0, int((2 - abs(rate) * 200))))

        # 5. SMC 结构 (15分)
        smc_trend = smc.get("trend", "")
        if smc_trend == "bullish":
            score += 15 if direction == "LONG" else 8
        elif smc_trend == "bearish":
            score += 15 if direction == "SHORT" else 8
        else:
            score += 5

        # 5b. SMC 流动性陷阱 — 反转预警
        sweep = smc.get("liquidity_sweep", {})
        if sweep:
            sweep_type = sweep.get("type", "")
            if sweep_type == "short_trap":
                details.append("SMC空头陷阱(反转预警)")
                if direction == "SHORT": score -= 15  # 做空遇空头陷阱=危险
            elif sweep_type == "long_trap":
                details.append("SMC多头陷阱(反转预警)")
                if direction == "LONG": score -= 15

        # 6. 清算压力 (10分)
        if "bullish" in liq_text.lower():
            score += 10
        elif "bearish" in liq_text.lower():
            score += 10 if direction == "SHORT" else 5

        # 7. 趋势强度 (10分)
        ts = structure.get("trend_strength", "")
        if "强多" in ts: score += 10
        elif "偏多" in ts: score += 7
        elif "强空" in ts: score += 10
        elif "偏空" in ts: score += 7
        elif "震荡" in ts: score += 3

        # 最终判定
        if direction == "WAIT":
            direction = "LONG" if tf_bullish >= 2 else "SHORT"

        icon_map = {"LONG": "\U0001f7e2", "SHORT": "\U0001f534"}
        if score >= 65:
            conclusion = f"{icon_map.get(direction, '')} {direction} (强信号)"
        elif score >= 50:
            dir_cn = "多" if direction == "LONG" else "空"
            conclusion = f"\U0001f7e1 偏{dir_cn} (中等)"
        else:
            conclusion = "⚪ 观望"

        return {"score": score, "direction": direction, "conclusion": conclusion,
                "details": details, "tf_bullish": tf_bullish}

    def market_report(self, symbol: str) -> str:
        """盘面分析报告 v2 — MACD/Boll/VWAP + 方向建议 + 关键位"""
        timeframes = ["1H", "4H", "1D"]
        market = self._market(symbol, timeframes)
        if not market:
            return "无法获取市场数据"

        main = market.get("1D", next(iter(market.values())))
        current = main.get("price", 0)

        # 多周期信号行
        structures = []
        for tf, d in market.items():
            macd = d.get("macd", {})
            macd_tag = "多" if macd.get("is_bullish") else "空"
            if macd.get("crossed_up"): macd_tag = "金叉↑"
            if macd.get("crossed_down"): macd_tag = "死叉↓"
            bb_range = d.get("bb_upper", 0) - d.get("bb_lower", 0)
            bb_pct = (d["price"] - d.get("bb_lower", d["price"])) / bb_range * 100 if bb_range > 0 else 50
            vwap_dev = (d["price"] - d.get("vwap", d["price"])) / d.get("vwap", d["price"]) * 100 if d.get("vwap") else 0
            ema_dir = "多" if d["ema20"] > d["ema50"] else "空"
            st_tag = "ST↑" if d.get("supertrend", {}).get("trend") == "up" else "ST↓"
            adx_val = d.get("adx", {}).get("adx", 0)
            structures.append(
                f"*{tf}*: ${d['price']:.1f} | MACD:{macd_tag} | BB:{bb_pct:.0f}% | "
                f"VWAP:{vwap_dev:+.1f}% | EMA:{ema_dir} | {st_tag} | ADX{adx_val:.0f}"
            )

        # 支撑阻力
        sup_1h = market.get("1H", {}).get("support")
        res_1h = market.get("1H", {}).get("resistance")
        vwap_1h = market.get("1H", {}).get("vwap", 0)

        chain = self._onchain(symbol)
        structure = self._market_structure(symbol)
        smc = self._smc_structure(symbol)
        ms = self._microstructure(symbol)  # 订单流数据
        liq = self.liq_tracker.summary(symbol, current) if self.liq_tracker._running else "清算追踪未启动"

        # 方向评分
        decision = self._calc_direction_score(market, chain, smc, liq, structure)

        # 入场/止损/止盈
        atr_1h = market.get("1H", {}).get("atr", 0)
        # 从 SMC order_blocks 提取 OB 价格
        ob_blocks = smc.get("order_blocks", [])
        supply_ob = None  # 做空阻力位 → SHORT止损 LONG止盈
        demand_ob = None  # 做多支撑位 → LONG止损 SHORT止盈
        for ob in ob_blocks:
            if ob.get("type") == "supply" and supply_ob is None:
                supply_ob = ob.get("price")
            if ob.get("type") == "demand" and demand_ob is None:
                demand_ob = ob.get("price")

        if decision["direction"] == "LONG":
            entry_low = current * 0.997
            entry_high = current
            sl = demand_ob if demand_ob and demand_ob < current else (current - atr_1h * 2) if atr_1h > 0 else current * 0.98
            # TP1: 优先阻力位，距离≥ATR×1
            if res_1h and res_1h > current and (res_1h - entry_high) >= atr_1h * 1:
                tp1 = res_1h
            else:
                tp1 = current + atr_1h * 1.5
            # TP2: supply OB 必须在入场上方，否则 ATR 推算
            if supply_ob and supply_ob > current:
                tp2 = supply_ob
            else:
                tp2 = current + atr_1h * 3
        else:
            entry_low = current
            entry_high = current * 1.003
            sl = supply_ob if supply_ob and supply_ob > current else (current + atr_1h * 2) if atr_1h > 0 else current * 1.02
            # TP1: 优先支撑位，距离≥ATR×1
            if sup_1h and sup_1h < current and (entry_low - sup_1h) >= atr_1h * 1:
                tp1 = sup_1h
            else:
                tp1 = current - atr_1h * 1.5
            # TP2: demand OB 必须在入场下方，否则 ATR 推算
            if demand_ob and demand_ob < current:
                tp2 = demand_ob
            else:
                tp2 = current - atr_1h * 3

        lines = [
            f"*{symbol} 盘面分析*",
            f"当前价: ${current:,.2f}",
            "",
            "*多周期信号:*",
        ]
        lines.extend(structures)
        lines.append("")
        if sup_1h or res_1h:
            parts = []
            if sup_1h: parts.append(f"支撑 ${sup_1h:,.2f}")
            if res_1h: parts.append(f"阻力 ${res_1h:,.2f}")
            if vwap_1h: parts.append(f"VWAP ${vwap_1h:,.2f}")
            lines.append(f"*关键位置:* {' | '.join(parts)}")
        lines.extend([
            f"*趋势强度:* {structure.get('trend_strength', '不明')}",
            f"*SMC结构:* {smc.get('summary', '未知')}",
            f"*订单流:* 价差{ms.get('spread',0):.3f}% | 买卖比{ms.get('buy_sell_ratio',0.5):.2f} | "
            f"1%深度${ms.get('depth_1pct',0):,.0f} | {'流动性断层⚠️' if ms.get('liquidity_gap') else '流动性均匀'}",
            f"*链上数据:* 费率{chain['funding_rate']*100:.3f}% | OI {chain.get('oi_val', 0):,.0f}张 | 拥挤度: {chain.get('crowd_signal', '中性')}",
            f"  {chain.get('explanation', '')}",
            f"*清算:* {liq}",
            "",
            f"*综合判定:* {decision['conclusion']} (评分: {decision['score']}/100)",
        ])
        if decision["details"]:
            lines.append(f"  {' | '.join(decision['details'][:4])}")
        if decision["score"] >= 40:
            lines.append(f"*建议:* 入场 ${entry_low:,.0f}-${entry_high:,.0f} | "
                         f"止损 ${sl:,.0f} | 止盈 ${tp1:,.0f} / ${tp2:,.0f}")

        if structure.get("range_bound"):
            lines.append("⚠️ 窄幅横盘，等待放量突破")
        if structure.get("pump_warning"):
            lines.append("\U0001f6a8 急涨预警")
        if structure.get("dump_warning"):
            lines.append("\U0001f6a8 急跌预警")

        return "\n".join(lines)

    def start(self, send):
        threading.Thread(target=self.run_loop, args=(send,), daemon=True).start()

    def stop(self):
        self._running = False
