"""
HTTP 兼容层 — 使用 httpx 替代 requests，复用连接池 + 自动重试
"""

import time
import httpx
from config import PROXY_URL

# 全局单例，复用 SSL 连接
_http = httpx.Client(
    proxy=PROXY_URL or None,
    timeout=30,
    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
)


def get(url, headers=None, proxies=None, timeout=None, **kwargs):
    last_err = None
    for attempt in range(3):
        try:
            resp = _http.get(url, headers=headers, timeout=timeout or 15)
            return _Response(resp)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1 + attempt * 2)
    raise last_err


def post(url, headers=None, data=None, json=None, proxies=None, timeout=None, **kwargs):
    last_err = None
    for attempt in range(3):
        try:
            if json is not None:
                resp = _http.post(url, headers=headers, json=json, timeout=timeout or 15)
            else:
                resp = _http.post(url, headers=headers, content=data, timeout=timeout or 15)
            return _Response(resp)
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(1 + attempt * 2)
    raise last_err


class _Response:
    def __init__(self, httpx_resp):
        self._resp = httpx_resp
        self.status_code = httpx_resp.status_code
        self.text = httpx_resp.text

    def json(self):
        return self._resp.json()


class ConnectionError(Exception):
    pass


class Timeout(Exception):
    pass
