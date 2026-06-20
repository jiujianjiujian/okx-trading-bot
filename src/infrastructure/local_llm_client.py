"""本地 LLM 客户端 — OpenAI 兼容 API (Ollama/OpenClaw/vLLM)

通过本地 OpenAI 兼容端点调用 Qwen 3.5 9B 等模型。
默认使用 Ollama 格式: http://localhost:11434/v1/chat/completions
"""

import json
import time

import httpx

from .config import LOCAL_LLM_URL, LOCAL_LLM_MODEL, PROXY_URL
from .logging_ import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_SLEEP = 1.5


class LocalLLMClient:
    """本地 LLM API 客户端 (OpenAI 兼容)"""

    def __init__(self):
        self._url = LOCAL_LLM_URL
        self._model = LOCAL_LLM_MODEL
        transport_kwargs = {}
        if PROXY_URL:
            transport_kwargs["proxy"] = PROXY_URL
        self._client = httpx.Client(
            timeout=httpx.Timeout(90.0),
            transport=httpx.HTTPTransport(retries=2, **transport_kwargs),
        )

    def close(self):
        self._client.close()

    @property
    def configured(self) -> bool:
        return bool(self._url)

    def chat(
        self,
        messages: list[dict],
        max_tokens: int = 2048,
        temperature: float = 0.3,
        json_mode: bool = False,
    ) -> str:
        """OpenAI 兼容 chat completion"""
        if not self.configured:
            return ""

        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.post(self._url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    logger.warning("本地LLM HTTP %s: %s", resp.status_code, resp.text[:200])
                    if resp.status_code < 500:
                        break
            except Exception as e:
                logger.warning("本地LLM 请求异常 (attempt %s): %s", attempt + 1, str(e))
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_SLEEP)
        return ""

    def chat_json(
        self, messages: list[dict], max_tokens: int | None = None,
    ) -> dict | None:
        """调用本地 LLM 并解析 JSON 返回"""
        text = self.chat(messages, max_tokens=max_tokens or 2048, temperature=0.1, json_mode=True)
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            for marker in ("```json", "```"):
                if marker in text:
                    try:
                        start = text.index(marker) + len(marker)
                        end = text.index("```", start) if "```" in text[start:] else len(text)
                        return json.loads(text[start:end].strip())
                    except (json.JSONDecodeError, ValueError):
                        pass
            logger.warning("本地LLM 返回非 JSON: %s", text[:300])
            return None
