"""Agent 运行时配置：步数、超时、模型 provider、缓存目录。

模型 key 只从环境变量读取，不写入仓库；没有 key 时由工厂回退到 stub。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from repo_service.config import Settings as RepoServiceSettings


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


@dataclass(frozen=True)
class AgentSettings:
    max_steps: int
    timeout_seconds: int
    provider: str
    base_url: str
    api_key: str
    model: str
    temperature: float
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
        cache_dir = RepoServiceSettings.from_env().cache_dir
        api_key = (
            os.getenv("FUNCGUARD_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip()
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
            max_steps=_env_int("FUNCGUARD_AGENT_MAX_STEPS", 8),
            timeout_seconds=_env_int("FUNCGUARD_AGENT_TIMEOUT", 120),
            provider=os.getenv("FUNCGUARD_LLM_PROVIDER", "auto").strip().lower(),
            base_url=os.getenv(
                "FUNCGUARD_LLM_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            api_key=api_key,
            model=os.getenv("FUNCGUARD_LLM_MODEL", "gpt-4o-mini").strip(),
            temperature=_env_float("FUNCGUARD_LLM_TEMPERATURE", 0.0),
            request_timeout_seconds=_env_int("FUNCGUARD_LLM_TIMEOUT", 60),
            proxy=proxy,
            cache_dir=cache_dir,
            runs_dir=runs_dir,
            max_file_bytes=_env_int("FUNCGUARD_TOOL_MAX_FILE_BYTES", 2_000_000),
            max_results=_env_int("FUNCGUARD_TOOL_MAX_RESULTS", 200),
            max_read_lines=_env_int("FUNCGUARD_TOOL_MAX_READ_LINES", 400),
            max_scan_files=_env_int("FUNCGUARD_TOOL_MAX_SCAN_FILES", 6000),
            max_tool_content_chars=_env_int(
                "FUNCGUARD_AGENT_MAX_TOOL_CONTENT", 8000
            ),
        )

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)
