"""
LLM 客户端封装 —— 任何 OpenAI 兼容接口都能用。

之所以不直接绑死某一家：作业要求「用标准 LLM function calling」，
而 OpenAI 的 tools 格式事实上已是行业通用标准，DeepSeek / Qwen /
Moonshot / 本地 Ollama 都兼容它。

配置全部走环境变量（见 .env.example）：
    LLM_API_KEY         必填
    LLM_BASE_URL        默认 https://api.openai.com/v1
    LLM_MODEL           默认 gpt-4o-mini
    LLM_THINKING        DeepSeek 专用：disabled（默认）/ enabled
    LLM_REASONING_EFFORT  thinking=enabled 时生效：low / high / max

关于 DeepSeek 的两个坑（都实测确认过，见 README「DeepSeek 注意事项」）：
1. 模型名。2026 年起官方模型是 `deepseek-flash` / `deepseek-v4-pro`，
   老名字 `deepseek-chat` 已从文档下线。
2. 思考模式默认开启，而**携带 tools 时，后续每一轮都必须把上一轮的
   `reasoning_content` 原样回传**，否则 API 直接返回 400。
   本文件负责把 thinking 开关显式化，loop.py 负责回传 reasoning_content。
"""

from __future__ import annotations

import os
from typing import Any

from openai import OpenAI

# .env 里常见的中文占位词，命中即认为用户还没真正填 key
_PLACEHOLDERS = ("在这里填", "你的", "your-key", "your_key", "填你的", "xxx", "占位")


def _validate_api_key(key: str | None) -> str:
    """在发出任何网络请求之前，把配置问题翻译成人能看懂的一句话。

    为什么要这一层：HTTP 头只能装 ASCII，若 key 里混了中文（比如直接留着
    .env 里的中文占位符），openai SDK 会在构造请求头时抛 UnicodeEncodeError，
    堆栈几十行，完全看不出是「key 没填」。
    """
    if not key or not key.strip():
        raise RuntimeError(
            "缺少 LLM_API_KEY。请打开项目根目录的 .env 文件，把 "
            "LLM_API_KEY 改成你的真实密钥；或先运行 `python demo_offline.py` "
            "体验无需密钥的离线演示。"
        )

    key = key.strip()

    if any(p in key.lower() for p in _PLACEHOLDERS):
        raise RuntimeError(
            f"LLM_API_KEY 还是占位符（当前值：{key}）。请打开项目根目录的 .env "
            "文件，把它替换成你的真实密钥后重新运行。"
        )

    try:
        key.encode("ascii")
    except UnicodeEncodeError:
        raise RuntimeError(
            "LLM_API_KEY 含有非 ASCII 字符（可能是中文），密钥必须是一串英文字母"
            "和数字。请检查 .env 文件里 LLM_API_KEY 那一行是否误填了中文。"
        ) from None

    return key


def _is_deepseek(base_url: str) -> bool:
    return "deepseek" in (base_url or "").lower()


def extract_reasoning(message: Any) -> str | None:
    """取出 DeepSeek 思考模式返回的 reasoning_content。

    openai SDK 的 ChatCompletionMessage 配了 extra="allow"，
    未知字段会落在 model_extra 里，也能通过属性访问到；两处都查一下更稳。
    """
    value = getattr(message, "reasoning_content", None)
    if value:
        return value
    extra = getattr(message, "model_extra", None) or {}
    return extra.get("reasoning_content")


class LLMClient:
    """对 OpenAI 兼容 chat.completions 接口的极薄封装。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None, thinking: str | None = None) -> None:
        self.api_key = _validate_api_key(api_key or os.getenv("LLM_API_KEY"))
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.thinking = (thinking or os.getenv("LLM_THINKING") or "disabled").strip().lower()
        self.reasoning_effort = (os.getenv("LLM_REASONING_EFFORT") or "high").strip().lower()

        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        # thinking 是 DeepSeek 私有参数。发给 OpenAI / Qwen 等会被判为
        # 未知字段直接 400，所以只在 DeepSeek 端点上注入。
        self._extra_body: dict[str, Any] | None = None
        if _is_deepseek(self.base_url):
            self._extra_body = {"thinking": {"type": self.thinking}}

    @property
    def thinking_enabled(self) -> bool:
        return self._extra_body is not None and self.thinking == "enabled"

    def chat(self, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None = None,
             temperature: float = 0.2) -> Any:
        """调用一次 chat.completions，返回原始 message 对象。"""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": tools or None,
            "temperature": temperature,
        }
        if self._extra_body is not None:
            kwargs["extra_body"] = self._extra_body
            if self.thinking_enabled:
                kwargs["reasoning_effort"] = self.reasoning_effort

        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message
