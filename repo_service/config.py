"""P2 快照链路的集中配置。

所有缓存路径、并发数、超时与文件大小限制只在这里定义；
其他模块通过 Settings 读取，避免同一路径散落在多处。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    cache_dir: Path
    git_dir: Path
    snapshots_dir: Path
    meta_dir: Path
    snapshot_meta_dir: Path
    sample_meta_dir: Path
    tmp_dir: Path
    manifest_path: Path
    default_dataset: Path
    proxy: str
    max_workers: int
    git_timeout_seconds: int
    git_low_speed_limit_bytes: int
    git_low_speed_time_seconds: int
    git_archive_timeout_seconds: int
    codeload_timeout_seconds: int
    archive_fallback: str
    codeload_base: str
    max_source_file_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        cache_dir = Path(
            os.getenv("FUNCGUARD_CACHE_DIR", str(REPO_ROOT / "cache"))
        ).resolve()
        # 代理不设默认值：只有显式配置 FUNCGUARD_GIT_PROXY 时才注入，
        # 避免在他人机器上强制指向不存在的本机代理。
        proxy = os.getenv("FUNCGUARD_GIT_PROXY", "").strip()
        return cls(
            cache_dir=cache_dir,
            git_dir=cache_dir / "git",
            snapshots_dir=cache_dir / "snapshots",
            meta_dir=cache_dir / "meta",
            snapshot_meta_dir=cache_dir / "meta" / "snapshots",
            sample_meta_dir=cache_dir / "meta" / "samples",
            tmp_dir=cache_dir / "tmp",
            manifest_path=cache_dir / "meta" / "manifest.jsonl",
            default_dataset=cache_dir / "dataset" / "test.jsonl",
            proxy=proxy,
            max_workers=_env_int("FUNCGUARD_MAX_WORKERS", 2),
            git_timeout_seconds=_env_int("FUNCGUARD_GIT_TIMEOUT", 900),
            # 低于该速率持续若干秒即判定网络停滞，让 git 主动放弃而不是挂死。
            git_low_speed_limit_bytes=_env_int(
                "FUNCGUARD_GIT_LOW_SPEED_LIMIT", 1000
            ),
            git_low_speed_time_seconds=_env_int(
                "FUNCGUARD_GIT_LOW_SPEED_TIME", 30
            ),
            git_archive_timeout_seconds=_env_int(
                "FUNCGUARD_GIT_ARCHIVE_TIMEOUT", 180
            ),
            codeload_timeout_seconds=_env_int("FUNCGUARD_CODELOAD_TIMEOUT", 1800),
            # git archive 经代理拉取 blob 停滞时，用 codeload 直连兜底；
            # 设为 none 可强制只用 git 链路。
            archive_fallback=os.getenv(
                "FUNCGUARD_ARCHIVE_FALLBACK", "codeload"
            ).strip().lower(),
            codeload_base=os.getenv(
                "FUNCGUARD_CODELOAD_BASE", "https://codeload.github.com"
            ).rstrip("/"),
            max_source_file_bytes=_env_int(
                "FUNCGUARD_MAX_SOURCE_FILE_BYTES", 2_000_000
            ),
        )

    def ensure_dirs(self) -> None:
        for directory in (
            self.git_dir,
            self.snapshots_dir,
            self.snapshot_meta_dir,
            self.sample_meta_dir,
            self.tmp_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


def display_path(path: Path) -> str:
    """尽量返回相对仓库根的路径，便于跨机器复现。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path.resolve())
