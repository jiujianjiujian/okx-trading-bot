"""DeepSeek AI 客户端 — 统一 LLM 调用封装"""

import json
import time
from typing import Optional

import httpx

from .config import (
    DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL,
    DEEPSEEK_MAX_TOKENS, PROXY_URL,
)
from .logging_ import get_logger

logger = get_logger(__name__)

# 请求重试配置
MAX_RETRIES = 3
RETRY_SLEEP = 2.0


class DeepSeekClient:
    """DeepSeek API 调用封装"""

    def __init__(self):
        limits = httpx.Limits(max_keepalive_connections=3, max_connections=5)
        transport_kwargs = {}
        if PROXY_URL:
            transport_kwargs["proxy"] = PROXY_URL
        self._client = httpx.Client(
            base_url=DEEPSEEK_BASE_URL,
            timeout=httpx.Timeout(60.0),
            limits=limits,
            transport=httpx.HTTPTransport(retries=2, **transport_kwargs),
        )

    def close(self):
        self._client.close()

    @property
    def configured(self) -> bool:
        return bool(DEEPSEEK_API_KEY)

    def chat(
        self,
        messages: list[dict],
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """
        调用 DeepSeek Chat Completion

        Args:
            messages: [{"role": "system"|"user"|"assistant", "content": "..."}]
            model: 模型名，默认 DEEPSEEK_MODEL
            max_tokens: 最大输出 token
            temperature: 温度 (0-2)，默认 0.3 偏确定性
            json_mode: 是否要求返回 JSON 格式

        Returns:
            AI 回复文本
        """
        if not self.configured:
            return ""

        model = model or DEEPSEEK_MODEL
        max_tokens = max_tokens or DEEPSEEK_MAX_TOKENS

        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        }

        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.post("/chat/completions", json=payload, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                elif resp.status_code == 429:  # rate limit
                    wait = RETRY_SLEEP * (attempt + 1)
                    logger.warning("DeepSeek 限流, 等待 %.0fs", wait)
                    time.sleep(wait)
                else:
                    logger.warning("DeepSeek HTTP %s: %s", resp.status_code, resp.text[:200])
            except Exception as e:
                logger.warning("DeepSeek 请求异常 (attempt %s): %s", attempt + 1, str(e))
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_SLEEP)

        return ""

    def chat_json(
        self, messages: list[dict], max_tokens: int | None = None,
    ) -> Optional[dict]:
        """调用 DeepSeek 并解析 JSON 返回"""
        text = self.chat(messages, max_tokens=max_tokens, temperature=0.1, json_mode=True)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试提取 JSON 块
            for marker in ("```json", "```"):
                if marker in text:
                    start = text.index(marker) + len(marker)
                    end = text.index("```", start) if "```" in text[start:] else len(text)
                    try:
                        return json.loads(text[start:end].strip())
                    except json.JSONDecodeError:
                        pass
            logger.warning("DeepSeek 返回非 JSON: %s", text[:300])
            return None
