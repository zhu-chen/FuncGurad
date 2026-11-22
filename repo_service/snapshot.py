"""快照管理器：按 repo+commit 去重，导出只读快照并写元数据。"""

from __future__ import annotations

import json
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bench.dataset import Sample
from repo_service.config import Settings, display_path
from repo_service.git_client import GitClient, GitError
from repo_service.urls import CommitRef, classify_commit_url


@dataclass
class SnapshotOutcome:
    status: str
    ref: CommitRef | None
    requested_commit: str | None = None
    resolved_commit: str | None = None
    version_role: str | None = None
    snapshot_dir: Path | None = None
    method: str | None = None
    reason: str | None = None
    note: str | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0


class SnapshotManager:
    """同一个仓库的 clone/fetch/archive 串行执行，不同仓库可并发。"""

    def __init__(self, settings: Settings, git: GitClient | None = None):
        self.settings = settings
        self.git = git or GitClient(settings)
        self._repo_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        # 本次运行中已判定 git 链路不可用的仓库；后续样本直接走兜底，
        # 避免同一个仓库反复等待克隆/导出超时。
        self._git_unavailable: set[str] = set()

    def _lock_for(self, repo_key: str) -> threading.Lock:
        with self._locks_guard:
            return self._repo_locks.setdefault(repo_key, threading.Lock())

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

    @staticmethod
    def _read_method(marker_path: Path) -> str:
        """复用已完成快照时读回其生成方式。"""
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return "git-archive"
        return payload.get("method") or "git-archive"

    @staticmethod
    def _read_note(marker_path: Path) -> str | None:
        try:
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return payload.get("note")

    def ensure_snapshot(self, sample: Sample) -> SnapshotOutcome:
        started = time.time()
        try:
            ref = classify_commit_url(sample.commit_url)
        except ValueError as exc:
            return SnapshotOutcome(
                status="failed",
                ref=None,
                error=str(exc),
                elapsed_seconds=round(time.time() - started, 2),
            )

        version_role = "vulnerable" if sample.target == 1 else "fixed"
        if not ref.is_github:
            return SnapshotOutcome(
                status="manual_pending",
                ref=ref,
                requested_commit=ref.commit,
                version_role=version_role,
                reason=ref.reason,
                elapsed_seconds=round(time.time() - started, 2),
            )

        try:
            with self._lock_for(ref.repo_key):
                assert ref.commit is not None
                method = "git-archive"
                note = None
                repo_path: Path | None = None

                # 主链路：blobless clone -> 按 SHA fetch -> rev-parse 取父提交。
                if ref.repo_key in self._git_unavailable:
                    resolved = self.git.resolve_commit_shallow(
                        ref, ref.commit, sample.target
                    )
                    method = "manual"
                    note = "shallow-resolve: 本仓库 git 链路此前已失败，直接走兜底"
                else:
                    try:
                        repo_path = self.git.clone_blobless(ref)
                        self.git.fetch_commit(repo_path, ref.commit)
                        resolved = self.git.resolve_commit(
                            repo_path, ref.commit, sample.target
                        )
                    except GitError as exc:
                        # 网络受限时 blobless 克隆/取 blob 会停滞；用单提交浅取
                        # 解析父提交，再用 codeload 取该提交的树。
                        if self.settings.archive_fallback != "codeload":
                            raise
                        self._git_unavailable.add(ref.repo_key)
                        first_error = str(exc).splitlines()[0][:300]
                        resolved = self.git.resolve_commit_shallow(
                            ref, ref.commit, sample.target
                        )
                        method = "manual"
                        note = f"shallow-resolve: {first_error}"

                snapshot_dir = self.settings.snapshots_dir / f"{ref.repo_key}__{resolved}"
                marker_path = (
                    self.settings.snapshot_meta_dir / f"{ref.repo_key}__{resolved}.json"
                )

                if marker_path.exists() and snapshot_dir.exists():
                    method = self._read_method(marker_path)
                    note = self._read_note(marker_path)
                else:
                    if snapshot_dir.exists():
                        shutil.rmtree(snapshot_dir)
                    snapshot_dir.mkdir(parents=True, exist_ok=True)
                    skipped_links = 0
                    archived = False

                    if method == "git-archive" and repo_path is not None:
                        tar_path = (
                            self.settings.tmp_dir / f"{ref.repo_key}__{resolved}.tar"
                        )
                        try:
                            skipped_links = self.git.archive(
                                repo_path, resolved, snapshot_dir, tar_path
                            )
                            archived = True
                        except GitError as exc:
                            if self.settings.archive_fallback != "codeload":
                                raise
                            self._git_unavailable.add(ref.repo_key)
                            note = "codeload-fallback: " + str(exc).splitlines()[0][:300]
                            shutil.rmtree(snapshot_dir, ignore_errors=True)
                            snapshot_dir.mkdir(parents=True, exist_ok=True)

                    if not archived:
                        method = "manual"
                        skipped_links = self.git.archive_via_codeload(
                            ref,
                            resolved,
                            snapshot_dir,
                            self.settings.tmp_dir
                            / f"{ref.repo_key}__{resolved}.tar.gz",
                        )

                    if (snapshot_dir / ".git").exists():
                        raise GitError(f"快照目录包含 .git：{snapshot_dir}")
                    if not any(snapshot_dir.iterdir()):
                        raise GitError(f"快照目录为空：{snapshot_dir}")
                    self._write_json(
                        marker_path,
                        {
                            "repo": ref.repo_key,
                            "requested_commit": ref.commit,
                            "resolved_commit": resolved,
                            "snapshot_dir": display_path(snapshot_dir),
                            "method": method,
                            "note": note,
                            "skipped_links": skipped_links,
                            "created_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )

                created_at = datetime.now(timezone.utc).isoformat()
                self._write_json(
                    self.settings.sample_meta_dir / f"{sample.idx}.json",
                    {
                        "idx": sample.idx,
                        "commit_url": sample.commit_url,
                        "requested_commit": ref.commit,
                        "resolved_commit": resolved,
                        "version_role": version_role,
                        "snapshot_dir": display_path(snapshot_dir),
                        "method": method,
                        "has_git_dir": False,
                        "created_at": created_at,
                    },
                )

                return SnapshotOutcome(
                    status="snapshot_ok",
                    ref=ref,
                    requested_commit=ref.commit,
                    resolved_commit=resolved,
                    version_role=version_role,
                    snapshot_dir=snapshot_dir,
                    method=method,
                    note=note,
                    elapsed_seconds=round(time.time() - started, 2),
                )
        except Exception as exc:  # 单条失败不打断批处理
            return SnapshotOutcome(
                status="failed",
                ref=ref,
                requested_commit=ref.commit,
                version_role=version_role,
                error=str(exc),
                elapsed_seconds=round(time.time() - started, 2),
            )
