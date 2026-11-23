"""Agent 运行时配置：步数、超时、模型 provider、缓存目录。

模型 key 只从环境变量读取，不写入仓库；没有 key 时由工厂回退到 stub。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from repo_service.config import Settings as RepoServiceSettings
from repo_service.env import load_env_file

ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ZHIPU_MODEL = "glm-5.3-flash"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
API_KEY_ENV_NAMES = (
    "FUNCGUARD_LLM_API_KEY",
    "ZAI_API_KEY",
    "ZHIPUAI_API_KEY",
    "OPENAI_API_KEY",
)


def _first_env(names: tuple[str, ...]) -> tuple[str, str | None]:
    """按顺序取第一个非空环境变量，返回 (值, 变量名)。"""
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value, name
    return "", None


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class AgentSettings:
    max_steps: int
    timeout_seconds: int
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_tokens: int
    stream: bool
    reasoning_effort: str
    retries: int
    request_timeout_seconds: int
    proxy: str
    cache_dir: Path
    runs_dir: Path
    max_file_bytes: int
    max_results: int
    max_read_lines: int
    max_scan_files: int
    max_tool_content_chars: int

    @classmethod
    def from_env(cls) -> "AgentSettings":
        load_env_file()
        cache_dir = RepoServiceSettings.from_env().cache_dir
        api_key, key_source = _first_env(API_KEY_ENV_NAMES)

        # 默认走智谱 OpenAI 兼容接口；只有显式提供 OPENAI_API_KEY 时才默认
        # 指向 OpenAI，避免把 OpenAI key 误发到其他域名。
        default_base = (
            OPENAI_BASE_URL if key_source == "OPENAI_API_KEY" else ZHIPU_BASE_URL
        )
        base_url = (
            os.getenv("FUNCGUARD_LLM_BASE_URL") or default_base
        ).rstrip("/")
        default_model = (
            DEFAULT_ZHIPU_MODEL if "bigmodel.cn" in base_url else DEFAULT_OPENAI_MODEL
        )

        # 代理不设默认值：只有显式配置时才注入，
        # 避免在他人机器上强制指向不存在的本机代理。
        proxy = (
            os.getenv("FUNCGUARD_LLM_PROXY")
            or os.getenv("FUNCGUARD_GIT_PROXY")
            or ""
        ).strip()
        runs_dir = Path(
            os.getenv("FUNCGUARD_AGENT_RUNS_DIR", str(cache_dir / "runs"))
        ).resolve()
        return cls(
            max_steps=_env_int("FUNCGUARD_AGENT_MAX_STEPS", 10),
            timeout_seconds=_env_int("FUNCGUARD_AGENT_TIMEOUT", 600),
            provider=os.getenv("FUNCGUARD_LLM_PROVIDER", "auto").strip().lower(),
            base_url=base_url,
            api_key=api_key,
            model=(os.getenv("FUNCGUARD_LLM_MODEL") or default_model).strip(),
            temperature=_env_float("FUNCGUARD_LLM_TEMPERATURE", 0.0),
            max_tokens=_env_int("FUNCGUARD_LLM_MAX_TOKENS", 16384),
            stream=_env_bool("FUNCGUARD_LLM_STREAM", True),
            reasoning_effort=os.getenv(
                "FUNCGUARD_LLM_REASONING_EFFORT", "low"
            ).strip().lower(),
            retries=_env_int("FUNCGUARD_LLM_RETRIES", 2),
            request_timeout_seconds=_env_int("FUNCGUARD_LLM_TIMEOUT", 180),
            proxy=proxy,
            cache_dir=cache_dir,
            runs_dir=runs_dir,
            max_file_bytes=_env_int("FUNCGUARD_TOOL_MAX_FILE_BYTES", 2_000_000),
            max_results=_env_int("FUNCGUARD_TOOL_MAX_RESULTS", 200),
            max_read_lines=_env_int("FUNCGUARD_TOOL_MAX_READ_LINES", 400),
            max_scan_files=_env_int("FUNCGUARD_TOOL_MAX_SCAN_FILES", 6000),
            max_tool_content_chars=_env_int(
                "FUNCGUARD_AGENT_MAX_TOOL_CONTENT", 16000
            ),
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)
