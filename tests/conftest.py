"""pytest fixtures"""

import os
import sys
import pytest

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def sample_klines():
    """生成 100 根模拟 5 分钟 K 线"""
    import random
    base = 87000.0
    klines = []
    price = base
    for i in range(100):
        open_price = price
        change = random.gauss(0, 50)
        close = open_price + change
        high = max(open_price, close) + abs(random.gauss(0, 20))
        low = min(open_price, close) - abs(random.gauss(0, 20))
        vol = random.uniform(100, 500)
        klines.append({
            "open": str(open_price),
            "high": str(high),
            "low": str(low),
            "close": str(close),
            "volume": str(vol),
        })
        price = close
    return klines


@pytest.fixture
def trending_up_klines():
    """上升趋势 K 线"""
    base = 87000.0
    klines = []
    for i in range(100):
        o = base + i * 20
        c = o + (i % 3) * 10 + 5
        h = max(o, c) + 10
        l = min(o, c) - 10
        v = 200
        klines.append({"open": str(o), "high": str(h), "low": str(l), "close": str(c), "volume": str(v)})
    return klines


@pytest.fixture
def trending_down_klines():
    """下降趋势 K 线"""
    base = 87000.0
    klines = []
    for i in range(100):
        o = base - i * 20
        c = o - (i % 3) * 10 - 5
        h = max(o, c) + 10
        l = min(o, c) - 10
        v = 200
        klines.append({"open": str(o), "high": str(h), "low": str(l), "close": str(c), "volume": str(v)})
    return klines
