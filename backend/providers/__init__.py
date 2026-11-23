"""provider 工厂：按配置选择真实模型或 stub，并在无 key 时明确回退。"""

from __future__ import annotations

from backend.config import AgentSettings
from backend.providers.base import Provider, ProviderError
from backend.providers.openai_compat import OpenAICompatibleProvider
from backend.providers.stub import StubProvider


def create_provider(settings: AgentSettings) -> tuple[Provider, str | None]:
    """返回 (provider, fallback_reason)；fallback_reason 非空表示用了 stub 兜底。"""
    mode = settings.provider
    if mode == "stub":
        return StubProvider(), None

    if settings.has_api_key:
        provider = OpenAICompatibleProvider(
            base_url=settings.base_url,
            api_key=settings.api_key,
            model=settings.model,
            temperature=settings.temperature,
            proxy=settings.proxy,
            max_tokens=settings.max_tokens,
            stream=settings.stream,
            reasoning_effort=settings.reasoning_effort,
            retries=settings.retries,
        )
        return provider, None

    if mode in ("openai", "zhipu", "glm"):
        raise ProviderError(
            f"FUNCGUARD_LLM_PROVIDER={mode} 但未设置 API key；请在 .env.local 或环境变量中配置 "
            "FUNCGUARD_LLM_API_KEY（也支持 ZAI_API_KEY / ZHIPUAI_API_KEY / OPENAI_API_KEY）"
        )

    return StubProvider(), (
        "未检测到模型 API key（FUNCGUARD_LLM_API_KEY / ZAI_API_KEY / "
        "ZHIPUAI_API_KEY / OPENAI_API_KEY），已回退到确定性 stub provider。"
    )
