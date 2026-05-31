"""httpx 单例 HTTP 客户端 — 统一代理 + 超时 + 重试"""

import httpx

from .config import PROXY_URL

_HTTP_CLIENT: httpx.Client | None = None


def get_http_client() -> httpx.Client:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        limits = httpx.Limits(max_keepalive_connections=10, max_connections=20)
        transport_kwargs = {}
        if PROXY_URL:
            transport_kwargs["proxy"] = PROXY_URL

        _HTTP_CLIENT = httpx.Client(
            timeout=httpx.Timeout(20.0),
            limits=limits,
            transport=httpx.HTTPTransport(retries=3, **transport_kwargs),
        )
    return _HTTP_CLIENT


def reset_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        _HTTP_CLIENT.close()
        _HTTP_CLIENT = None
