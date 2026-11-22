"""commit_url 解析。

支持：
- GitHub 标准提交链接：https://github.com/{owner}/{repo}/commit/{sha}
- qemu 非标准链接：https://git.qemu.org/?p=qemu.git;a=commit;h={sha}
- 其他域名：标记为 manual，交给人工兜底，不直接报错中断批处理
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

GITHUB_HOSTS = {"github.com", "www.github.com"}
QEMU_HOSTS = {"git.qemu.org", "git.qemu-project.org"}

GITHUB_COMMIT_PATH = re.compile(
    r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/commit/(?P<sha>[0-9a-fA-F]{7,40})/?$"
)

QEMU_COMMIT_PARAM = re.compile(r"[?&;]h=(?P<sha>[0-9a-fA-F]{7,40})")


@dataclass(frozen=True)
class CommitRef:
    raw_url: str
    provider: str
    owner: str | None
    repo: str | None
    commit: str | None
    reason: str | None = None

    @property
    def is_github(self) -> bool:
        return self.provider == "github"

    @property
    def repo_key(self) -> str:
        if not self.owner or not self.repo:
            return "unknown"
        return f"{self.owner}__{self.repo}"


def classify_commit_url(url: str) -> CommitRef:
    """解析提交链接；GitHub 之外的链接返回 provider=manual。"""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()

    if host in GITHUB_HOSTS:
        match = GITHUB_COMMIT_PATH.match(parsed.path)
        if not match:
            raise ValueError(f"无法解析 GitHub 提交链接：{url}")
        return CommitRef(
            raw_url=url,
            provider="github",
            owner=match.group("owner"),
            repo=match.group("repo"),
            commit=match.group("sha").lower(),
        )

    if host in QEMU_HOSTS:
        match = QEMU_COMMIT_PARAM.search(url)
        commit = match.group("sha").lower() if match else None
        return CommitRef(
            raw_url=url,
            provider="manual",
            owner="qemu",
            repo="qemu",
            commit=commit,
            reason="非 GitHub 链接，需人工准备对应版本快照",
        )

    return CommitRef(
        raw_url=url,
        provider="manual",
        owner=None,
        repo=None,
        commit=None,
        reason=f"不支持的仓库域名：{host or 'unknown'}",
    )
