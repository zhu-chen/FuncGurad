"""原生 git 客户端：不依赖 gh，只使用 git 命令。

链路：
    clone --filter=blob:none --no-checkout
    -> fetch origin <sha>
    -> rev-parse "<sha>^1"（target=1）
    -> git archive 导出不含 .git 的快照目录
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from repo_service.config import Settings
from repo_service.urls import CommitRef


class GitError(RuntimeError):
    """git 命令失败或快照导出失败。"""


def _force_rmtree(path: Path) -> None:
    """删除目录；git 的 pack 文件是只读的，Windows 下需先去掉只读位。"""

    def on_error(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=on_error)


def _kill_process_tree(pid: int) -> None:
    """杀掉 git 及其子进程（fetch/index-pack 等会继承管道，必须整棵树杀）。"""
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
    else:
        try:
            os.kill(pid, 9)
        except OSError:
            pass


def run_git(
    args: list[str],
    cwd: Path | None = None,
    timeout: int = 900,
    proxy: str | None = None,
    check: bool = True,
    low_speed: tuple[int, int] | None = None,
    no_lazy_fetch: bool = False,
) -> subprocess.CompletedProcess:
    """执行 git 命令；统一注入代理与禁用交互式凭证提示。

    low_speed=(bytes/s, seconds) 时注入 http.lowSpeedLimit/lowSpeedTime，
    让停滞的连接主动失败，而不是无限挂起。
    no_lazy_fetch=True 时关闭 partial clone 的按需拉取，避免本地查询
    触发意外的网络请求（会表现为长时间卡住）。
    """
    command = ["git"]
    if low_speed:
        limit, seconds = low_speed
        command.extend(
            [
                "-c",
                f"http.lowSpeedLimit={limit}",
                "-c",
                f"http.lowSpeedTime={seconds}",
            ]
        )
    if proxy:
        command.extend(["-c", f"http.proxy={proxy}"])
    command.extend(args)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    if no_lazy_fetch:
        env["GIT_NO_LAZY_FETCH"] = "1"

    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(proc.pid)
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        raise GitError(f"git 超时（{timeout}s）：{' '.join(args)}") from exc

    if check and proc.returncode != 0:
        raise GitError(
            "git 失败（{}）：{}\n{}".format(
                proc.returncode, " ".join(args), (stderr or "").strip()[:2000]
            )
        )
    return subprocess.CompletedProcess(
        command, proc.returncode, stdout or "", stderr or ""
    )


def extract_tar_safely(
    tar_path: Path,
    dest_dir: Path,
    mode: str = "r",
    strip_prefix: str | None = None,
) -> int:
    """把 tar / tar.gz 解压到 dest_dir，返回跳过的链接数。

    只写普通文件与目录；符号链接/硬链接跳过，避免路径逃逸。
    strip_prefix 用于去掉 codeload 归档里的 "<repo>-<sha>/" 顶层目录。
    """
    destination = dest_dir.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    skipped_links = 0
    prefix = f"{strip_prefix}/" if strip_prefix else None

    with tarfile.open(tar_path, mode) as archive:
        for member in archive:
            name = member.name
            if prefix is not None:
                if name == strip_prefix:
                    continue
                if not name.startswith(prefix):
                    raise GitError(f"归档包含意外顶层路径：{name}")
                name = name[len(prefix) :]
            if not name:
                continue

            target = (destination / name).resolve()
            try:
                target.relative_to(destination)
            except ValueError as exc:
                raise GitError(f"tar 包含越界路径：{member.name}") from exc

            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with target.open("wb") as output:
                    shutil.copyfileobj(source, output)
            else:
                skipped_links += 1

    return skipped_links


class GitClient:
    """按仓库维护 blobless 缓存，并按 commit 导出快照。"""

    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def _low_speed(self) -> tuple[int, int]:
        return (
            self.settings.git_low_speed_limit_bytes,
            self.settings.git_low_speed_time_seconds,
        )

    def repo_path(self, owner: str, repo: str) -> Path:
        return self.settings.git_dir / f"{owner}__{repo}"

    def _is_git_repo(self, path: Path) -> bool:
        if not path.exists():
            return False
        proc = run_git(
            ["rev-parse", "--git-dir"],
            cwd=path,
            timeout=30,
            check=False,
            no_lazy_fetch=True,
        )
        return proc.returncode == 0

    def clone_blobless(self, ref: CommitRef) -> Path:
        """每个仓库只克隆一次；失败时清理临时目录，便于下次重试。"""
        if not ref.owner or not ref.repo:
            raise GitError(f"缺少 owner/repo：{ref.raw_url}")

        path = self.repo_path(ref.owner, ref.repo)
        if path.exists():
            if self._is_git_repo(path):
                return path
            raise GitError(f"缓存目录存在但不是 git 仓库：{path}")

        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(path.name + ".cloning")
        if temp_path.exists():
            # 上次被中断的克隆可能留下只读 pack 文件。
            _force_rmtree(temp_path)

        url = f"https://github.com/{ref.owner}/{ref.repo}.git"
        try:
            run_git(
                [
                    "clone",
                    "--filter=blob:none",
                    "--no-checkout",
                    url,
                    str(temp_path),
                ],
                timeout=self.settings.git_timeout_seconds,
                proxy=self.settings.proxy,
                low_speed=self._low_speed,
            )
        except GitError:
            # 半成品克隆会占用大量磁盘（大仓库可达数 GB），失败即清理。
            _force_rmtree(temp_path)
            raise

        if path.exists():
            _force_rmtree(temp_path)
        else:
            temp_path.rename(path)
        return path

    def has_commit(self, repo_path: Path, sha: str) -> bool:
        proc = run_git(
            ["cat-file", "-e", f"{sha}^{{commit}}"],
            cwd=repo_path,
            timeout=60,
            check=False,
            no_lazy_fetch=True,
        )
        return proc.returncode == 0

    def fetch_commit(self, repo_path: Path, sha: str) -> None:
        if self.has_commit(repo_path, sha):
            return

        try:
            run_git(
                ["fetch", "--filter=blob:none", "origin", sha],
                cwd=repo_path,
                timeout=self.settings.git_timeout_seconds,
                proxy=self.settings.proxy,
                low_speed=self._low_speed,
            )
        except GitError:
            # 少数服务器不支持按 SHA 过滤，退化为普通 fetch。
            run_git(
                ["fetch", "origin", sha],
                cwd=repo_path,
                timeout=self.settings.git_timeout_seconds,
                proxy=self.settings.proxy,
                low_speed=self._low_speed,
            )

        if not self.has_commit(repo_path, sha):
            raise GitError(f"fetch 后仍找不到提交 {sha}")

    def resolve_commit(
        self, repo_path: Path, sha: str, target: int
    ) -> str:
        """target=1 取第一父提交，target=0 取提交本身。"""
        revision = f"{sha}^1" if target == 1 else sha
        proc = run_git(
            ["rev-parse", "--verify", f"{revision}^{{commit}}"],
            cwd=repo_path,
            timeout=120,
            no_lazy_fetch=True,
        )
        return proc.stdout.strip()

    def shallow_repo_path(self, owner: str, repo: str) -> Path:
        return self.settings.git_dir / f"{owner}__{repo}__shallow"

    def _ensure_shallow_repo(self, ref: CommitRef) -> Path:
        """网络受限时不克隆全历史，只建一个用于解析父提交的空仓库。"""
        path = self.shallow_repo_path(ref.owner, ref.repo)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.mkdir(parents=True)
            run_git(["init", "-q"], cwd=path, timeout=60)
            run_git(
                [
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{ref.owner}/{ref.repo}.git",
                ],
                cwd=path,
                timeout=60,
            )
        # 上次被超时杀掉的 fetch 会留下 shallow.lock 等锁，导致后续全部 128。
        git_dir = path / ".git"
        if git_dir.is_dir():
            for lock in git_dir.rglob("*.lock"):
                try:
                    lock.unlink()
                except OSError:
                    pass
        return path

    def resolve_commit_shallow(self, ref: CommitRef, sha: str, target: int) -> str:
        """浅取单个提交，从提交对象里读父提交，替代 rev-parse "<sha>^1"。

        只下载该提交本身（depth=1），不拉历史与 blob；
        提交对象内的 parent 字段即第一父提交。
        """
        if not ref.owner or not ref.repo:
            raise GitError(f"缺少 owner/repo：{ref.raw_url}")

        path = self._ensure_shallow_repo(ref)
        if not self.has_commit(path, sha):
            run_git(
                [
                    "fetch",
                    "--depth=1",
                    "--filter=blob:none",
                    "--no-tags",
                    "origin",
                    sha,
                ],
                cwd=path,
                timeout=self.settings.git_timeout_seconds,
                proxy=self.settings.proxy,
                low_speed=self._low_speed,
            )
        if not self.has_commit(path, sha):
            raise GitError(f"浅取后仍找不到提交 {sha}")

        proc = run_git(
            ["rev-parse", "--verify", f"{sha}^{{commit}}"],
            cwd=path,
            timeout=60,
            no_lazy_fetch=True,
        )
        full_sha = proc.stdout.strip()

        if target != 1:
            return full_sha

        # 不能用 rev-list：depth=1 会把该提交标记为 shallow，父提交被隐藏；
        # 直接读提交对象里的 parent 行即可拿到第一父提交。
        proc = run_git(
            ["cat-file", "-p", full_sha],
            cwd=path,
            timeout=60,
            no_lazy_fetch=True,
        )
        parents = [
            line.split()[1]
            for line in proc.stdout.splitlines()
            if line.startswith("parent ")
        ]
        if not parents:
            raise GitError(f"提交 {sha} 没有父提交，无法回溯漏洞版本")
        return parents[0]

    def archive(
        self, repo_path: Path, sha: str, dest_dir: Path, tar_path: Path
    ) -> int:
        """导出快照，返回跳过的链接数。"""
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        run_git(
            ["archive", "--format=tar", f"--output={tar_path}", sha],
            cwd=repo_path,
            timeout=self.settings.git_archive_timeout_seconds,
            proxy=self.settings.proxy,
            low_speed=self._low_speed,
        )
        try:
            return extract_tar_safely(tar_path, dest_dir)
        finally:
            if tar_path.exists():
                tar_path.unlink()

    def archive_via_codeload(
        self, ref: CommitRef, sha: str, dest_dir: Path, tar_path: Path
    ) -> int:
        """直连 codeload 拉取指定提交的 tar.gz 并解压。

        用于 git archive 经代理拉取 blob 停滞或超时的兜底：
        不走代理，只取该提交的完整树，仍保证快照不含 .git。
        """
        if not ref.owner or not ref.repo:
            raise GitError(f"缺少 owner/repo：{ref.raw_url}")

        url = f"{self.settings.codeload_base}/{ref.owner}/{ref.repo}/tar.gz/{sha}"
        tar_path.parent.mkdir(parents=True, exist_ok=True)
        # 显式禁用代理：codeload 直连可用，且代理会成为瓶颈。
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )
        request = urllib.request.Request(
            url, headers={"User-Agent": "FuncGurad-repo-service/1.0"}
        )
        try:
            with opener.open(
                request, timeout=self.settings.codeload_timeout_seconds
            ) as response, tar_path.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            return extract_tar_safely(
                tar_path, dest_dir, mode="r:gz", strip_prefix=f"{ref.repo}-{sha}"
            )
        except Exception as exc:
            raise GitError(f"codeload 兜底失败（{url}）：{exc}") from exc
        finally:
            if tar_path.exists():
                tar_path.unlink()
