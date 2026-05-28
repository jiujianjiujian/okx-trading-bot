"""
AI 对话模块 — 支持 Anthropic Claude 和 DeepSeek 两种后端

优先级: Anthropic > DeepSeek
DeepSeek 使用 OpenAI 兼容接口，国内直连，无需代理
"""

import contextlib
import sys
import requests

# Windows GBK 终端兼容
if sys.platform == "win32":
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class ClaudeChat:
    """AI 对话客户端，自动选择可用后端"""

    def __init__(self, anthropic_key: str = "", deepseek_key: str = "",
                 deepseek_base: str = "https://api.deepseek.com/v1",
                 model: str = "", max_tokens: int = 1024, proxy: str = ""):
        self.anthropic_key = anthropic_key
        self.deepseek_key = deepseek_key
        self.deepseek_base = deepseek_base
        self.max_tokens = max_tokens
        self.proxy = proxy
        self._backend = None  # "anthropic" or "deepseek"
        self.model = model

        # 自动选择后端
        if anthropic_key:
            self._backend = "anthropic"
            if not model:
                self.model = "claude-sonnet-4-20250514"
        elif deepseek_key:
            self._backend = "deepseek"
            if not model:
                self.model = "deepseek-chat"

    # ================================================================
    # 公共接口
    # ================================================================

    def chat(self, messages: list, system_prompt: str | None = None) -> str:
        """发送消息给 AI 并获取回复"""
        if self._backend == "anthropic":
            return self._chat_anthropic(messages, system_prompt)
        elif self._backend == "deepseek":
            return self._chat_deepseek(messages, system_prompt)
        else:
            return "AI 服务未配置，请在 .env 中设置 ANTHROPIC_API_KEY 或 DEEPSEEK_API_KEY"

    def get_system_prompt(self) -> str:
        return (
            "你是一个嵌入在加密货币交易机器人中的AI助手。"
            "你可以自由回答用户的各种问题，包括编程、交易策略、市场分析等。"
            "对于交易操作（查余额、持仓、下单等），请引导用户使用斜杠命令，"
            "例如 /balance、/positions、/market 等。"
            "请用中文回答，保持专业但友好的语气。"
            "用户通过QQ与你交流，请保持回复简洁（通常不超过500字）。"
        )

    def health_check(self) -> bool:
        return self._backend is not None

    # ================================================================
    # Anthropic 后端
    # ================================================================

    def _chat_anthropic(self, messages: list, system_prompt: str | None = None) -> str:
        try:
            from anthropic import (
                Anthropic,
                APIError,
                APIConnectionError,
                RateLimitError,
                AuthenticationError,
                APITimeoutError,
            )
        except ImportError:
            return "anthropic SDK 未安装，请执行: pip install anthropic"

        try:
            kwargs = {"api_key": self.anthropic_key}
            if self.proxy:
                import httpx
                kwargs["http_client"] = httpx.Client(proxy=self.proxy)

            client = Anthropic(**kwargs)
            req = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": messages,
                "temperature": 0.7,
            }
            if system_prompt:
                req["system"] = system_prompt

            response = client.messages.create(**req)
            return response.content[0].text

        except AuthenticationError:
            return "Anthropic API 密钥认证失败，请检查 ANTHROPIC_API_KEY"
        except RateLimitError:
            return "请求过于频繁，请稍后再试"
        except APITimeoutError:
            return "AI 响应超时，请稍后重试"
        except APIConnectionError:
            return "无法连接到 Anthropic API，请检查网络和代理设置"
        except APIError as e:
            return f"Anthropic API 错误: {e}"
        except Exception as e:
            return f"未知错误: {e}"

    # ================================================================
    # DeepSeek 后端 (OpenAI 兼容)
    # ================================================================

    def _chat_deepseek(self, messages: list, system_prompt: str | None = None) -> str:
        headers = {
            "Authorization": f"Bearer {self.deepseek_key}",
            "Content-Type": "application/json",
        }

        # DeepSeek 用 messages 数组传 system prompt
        api_messages = []
        if system_prompt:
            api_messages.append({"role": "system", "content": system_prompt})
        api_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": self.max_tokens,
            "temperature": 0.7,
        }

        proxies = {"https": self.proxy} if self.proxy else None

        try:
            r = requests.post(
                f"{self.deepseek_base}/chat/completions",
                headers=headers,
                json=payload,
                proxies=proxies,
                timeout=60,
            )
            if r.status_code == 401:
                return "DeepSeek API 密钥无效，请检查 DEEPSEEK_API_KEY"
            if r.status_code == 429:
                return "请求过于频繁，请稍后再试"
            if r.status_code != 200:
                return f"DeepSeek API 错误 (HTTP {r.status_code}): {r.text[:200]}"

            data = r.json()
            return data["choices"][0]["message"]["content"]

        except requests.exceptions.Timeout:
            return "AI 响应超时，请稍后重试"
        except requests.exceptions.ConnectionError:
            return "无法连接到 DeepSeek API，请检查网络"
        except Exception as e:
            import traceback
            print(f"[DeepSeek] 请求异常:\n{traceback.format_exc()}", flush=True)
            return f"AI 请求失败: {e}"
