#!/usr/bin/env python3
"""Git-backed plugin updates. Durable state survives Omarchy's QML reloads."""
from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

# Importing our helper must not create files in Omarchy's watched plugin tree.
sys.dont_write_bytecode = True
from reload_guard import ReloadGuard

PLUGIN_ID = "andy.git-sync"
MARKET_URL = "https://raw.githubusercontent.com/omacom/omarchy-plugin-marketplace/main/registry.json"
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


class SyncError(Exception):
    pass


def read_json(path, fallback=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return fallback


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def stop_command(process):
    # Let Git release its locks, then stop any transport/pack children as well.
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
    finally:
        # Children may have closed their pipes without exiting yet.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def run(args, timeout=75, binary=False):
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes -oConnectTimeout=15")
    try:
        with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, start_new_session=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except BaseException as exc:
                stop_command(process)
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise SyncError("连接超时，请重试") from exc
                raise
    except OSError as exc:
        raise SyncError(str(exc)) from exc
    if process.returncode:
        message = stderr.decode(errors="replace").strip()
        raise SyncError(message[-900:] or "命令执行失败")
    return stdout if binary else stdout.decode(errors="replace").strip()


def git(path, *args, bare=False, **kwargs):
    prefix = ["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never", "-c", "gc.auto=0"]
    prefix += ["--git-dir", str(path)] if bare else ["-C", str(path)]
    return run(prefix + list(args), **kwargs)


def is_ancestor(path, older, newer, cache):
    env = dict(os.environ, GIT_ALTERNATE_OBJECT_DIRECTORIES=str(cache / "objects"))
    result = subprocess.run(["git", "-C", str(path), "merge-base", "--is-ancestor", older, newer],
                            capture_output=True, timeout=15, env=env)
    # A missing parent in shallow history cannot establish ancestry.
    return result.returncode == 0 if result.returncode in (0, 1) else None


def safe_id(value):
    return isinstance(value, str) and bool(ID_RE.fullmatch(value)) and ".." not in value


def remove_install_path(path):
    # Never follow a development link when removing a superseded installation.
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def cache_in_use(repo):
    """Include orphaned Git children that outlived an interrupted backend."""
    repo = repo.resolve()
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            args = [os.fsdecode(arg) for arg in (process / "cmdline").read_bytes().split(b"\0") if arg]
            if not args or not (Path(args[0]).name == "git" or Path(args[0]).name.startswith("git-")):
                continue
            cwd = (process / "cwd").resolve()
            def within(value):
                value = value.split("=", 1)[-1] if value.startswith("--") else value
                path = (cwd / value).resolve()
                return path == repo or repo in path.parents
            if within(str(cwd)) or any(within(arg) for arg in args[1:]):
                return True
            for entry in (process / "environ").read_bytes().split(b"\0"):
                key, _, value = entry.partition(b"=")
                if key in (b"GIT_DIR", b"GIT_COMMON_DIR") and value and within(os.fsdecode(value)):
                    return True
        except PermissionError:
            # An inaccessible Git process cannot be ruled out as the owner.
            return True
        except (OSError, RuntimeError):
            continue
    return False


def market_entries(data):
    """The official marketplace's source list includes pinned, nested manifests."""
    result = {}
    for source in data.get("sources", []):
        entries = source.get("plugins", {})
        catalog = source.get("catalog", {})
        if catalog.get("id") and not entries:
            entries = {catalog["id"]: {"manifestPath": "manifest.json", **catalog}}
        for plugin_id, entry in entries.items():
            if not safe_id(plugin_id):
                continue
            manifest_path = entry.get("manifestPath", "manifest.json")
            parts = PurePosixPath(manifest_path)
            if parts.is_absolute() or ".." in parts.parts:
                continue
            result[plugin_id] = {
                "repo": source.get("repo", ""),
                "marketCommit": source.get("listingValidatedCommit", ""),
                "marketVersion": entry.get("version", ""),
                "manifestPath": manifest_path,
                "history": [r.get("commit", "") for r in source.get("listingValidationHistory", [])],
            }
    return result


class Manager:
    def __init__(self):
        home = Path.home()
        config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        state = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        cache = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
        self.plugins_dir = Path(os.environ.get("OMARCHY_GIT_SYNC_PLUGINS_DIR", config / "omarchy/plugins"))
        self.state_dir = Path(os.environ.get("OMARCHY_GIT_SYNC_STATE_DIR", state / "omarchy-git-sync"))
        self.cache_dir = Path(os.environ.get("OMARCHY_GIT_SYNC_CACHE_DIR", cache / "omarchy-git-sync"))
        self.state_path = self.state_dir / "state.json"
        self.state = read_json(self.state_path, {}) or {}
        self.state.setdefault("plugins", [])
        self.state.setdefault("checkedAt", 0)
        self.state.setdefault("revision", 0)
        self.mutex = threading.RLock()
        self.market = {}
        self.journal_path = self.state_dir / "update-journal.json"
        self.journal = None

    @contextlib.contextmanager
    def lock(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.state_dir / "worker.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError("已有任务正在运行")
            self.recover_update()
            yield

    def save(self):
        with self.mutex:
            # Publish live check results without moving cards until the check finishes.
            if not (self.state.get("busy") and self.state.get("action") == "check"):
                rank = {"available": 0, "sync-needed": 0, "updating": 0, "preparing": 0, "prepared": 0,
                        "checking": 1, "error": 2,
                        "modified": 3, "diverged": 3, "unchecked": 4, "local": 5, "ahead": 5, "current": 6}
                self.state["plugins"].sort(key=lambda p: (rank.get(p["status"], 4), p["name"].casefold(), p["id"]))
            self.state["revision"] += 1
            self.state["savedAt"] = time.time()
            atomic_json(self.state_path, self.state)

    def load_market(self, network=True):
        cache_path = self.cache_dir / "market.json"
        data = read_json(cache_path)
        self.state["marketError"] = ""
        self.state["marketCached"] = False
        if network:
            try:
                url = os.environ.get("OMARCHY_GIT_SYNC_MARKET_URL", MARKET_URL)
                request = urllib.request.Request(url, headers={"User-Agent": "Omarchy-Git-Sync/1.0"})
                with urllib.request.urlopen(request, timeout=25) as response:
                    payload = response.read(24 * 1024 * 1024 + 1)
                if len(payload) > 24 * 1024 * 1024:
                    raise SyncError("市场目录过大")
                fresh = json.loads(payload)
                if not isinstance(fresh, dict) or not isinstance(fresh.get("sources"), list):
                    raise SyncError("市场目录格式无效")
                atomic_json(cache_path, fresh)
                data = fresh
            except (OSError, ValueError, SyncError) as exc:
                self.state["marketError"] = "市场信息暂不可用：" + str(exc)
                self.state["marketCached"] = data is not None
        self.market = market_entries(data or {})
        self.state["marketAvailable"] = data is not None

    def discover(self):
        previous = {p["id"]: p for p in self.state["plugins"]}
        rows = []
        if not self.plugins_dir.exists():
            self.state["plugins"] = rows
            return
        for path in sorted(self.plugins_dir.iterdir()):
            if path.name.startswith(".") or not path.is_dir():
                continue
            manifest = read_json(path / "manifest.json", {}) or {}
            plugin_id = manifest.get("id", "")
            if not safe_id(plugin_id) or plugin_id != path.name:
                continue
            row = previous.get(plugin_id, {}).copy()
            row.update(id=plugin_id, name=manifest.get("name", plugin_id),
                       description=manifest.get("description", ""), version=manifest.get("version", ""),
                       path=str(path), git=(path / ".git").exists(), symlink=path.is_symlink())
            for key in ("repo", "localCommit", "upstreamCommit", "commitMessage", "commitDate",
                        "latestVersion", "marketCommit", "marketVersion", "error", "outcome", "branch"):
                row.setdefault(key, "")
            row.setdefault("status", "unchecked")
            row.setdefault("marketState", "unknown")
            row.setdefault("canUpdate", False)
            rows.append(row)
        self.state["plugins"] = rows

    def configure_row(self, row):
        path = Path(row["path"])
        listing = self.market.get(row["id"], {})
        market_version = row["marketVersion"] if listing.get("marketCommit") == row["marketCommit"] else ""
        row["manifestPath"] = listing.get("manifestPath", "manifest.json")
        row["marketCommit"] = listing.get("marketCommit", "")
        row["marketVersion"] = listing.get("marketVersion", "") or market_version
        row["marketState"] = "unknown" if listing or not self.state.get("marketAvailable") else "unlisted"
        row["fetchRef"] = "HEAD"
        row["branch"] = "默认分支"
        if row["git"]:
            try:
                row["localCommit"] = git(path, "rev-parse", "HEAD")
                try:
                    branch = git(path, "symbolic-ref", "--short", "HEAD")
                    remote = git(path, "config", "--get", f"branch.{branch}.remote")
                    ref = git(path, "config", "--get", f"branch.{branch}.merge")
                    if remote == ".":
                        remote = "origin"
                    row["fetchRef"] = ref
                    row["branch"] = ref.removeprefix("refs/heads/")
                except SyncError:
                    remote = "origin"
                row["repo"] = git(path, "remote", "get-url", remote)
                row["dirty"] = bool(git(path, "status", "--porcelain", "--untracked-files=no"))
            except SyncError:
                row["repo"] = ""
                row["dirty"] = False
        else:
            row["repo"] = listing.get("repo", "")
            row["dirty"] = False
        if not row["repo"]:
            row.update(status="local", canUpdate=False)
            return False
        if row["repo"].startswith("-") or "\n" in row["repo"]:
            raise SyncError("无效的 Git 仓库地址")
        return True

    def fetch(self, row):
        repo = self.cache_dir / "repos" / (row["id"] + ".git")
        repo.parent.mkdir(parents=True, exist_ok=True)
        locks = list(repo.rglob("*.lock"))
        if locks:
            if cache_in_use(repo):
                raise SyncError("此插件的 Git 检查缓存仍有任务在运行，请稍后重试")
            # This is our disposable bare cache, never an installed/development
            # repository. The worker lock excludes other Git Sync tasks.
            for lock in locks:
                lock.unlink(missing_ok=True)
        if not (repo / "HEAD").exists():
            run(["git", "init", "--bare", str(repo)])
            git(repo, "remote", "add", "origin", row["repo"], bare=True)
        else:
            git(repo, "remote", "set-url", "origin", row["repo"], bare=True)
        git(repo, "fetch", "--quiet", "--depth=40", "--no-tags", "origin", row["fetchRef"], bare=True)
        row["upstreamCommit"] = git(repo, "rev-parse", "FETCH_HEAD", bare=True)
        meta = git(repo, "show", "-s", "--format=%s%n%cI", row["upstreamCommit"], bare=True).splitlines()
        row["commitMessage"] = meta[0] if meta else ""
        row["commitDate"] = meta[1] if len(meta) > 1 else ""
        return repo

    def manifest_at(self, repo, commit, manifest_path):
        manifest = json.loads(git(repo, "show", f"{commit}:{manifest_path}", bare=True))
        if not isinstance(manifest, dict):
            raise SyncError("Git 上游 manifest 格式无效")
        return manifest

    def select_subdir(self, repo, row):
        for manifest_path in dict.fromkeys(["manifest.json", row["manifestPath"]]):
            try:
                manifest = self.manifest_at(repo, row["upstreamCommit"], manifest_path)
                if manifest.get("id") == row["id"]:
                    row["sourceDir"] = str(PurePosixPath(manifest_path).parent)
                    row["latestVersion"] = manifest.get("version", "")
                    return
            except (SyncError, ValueError):
                pass
        raise SyncError("上游未找到此插件的 manifest")

    def tree(self, repo, commit, subdir="."):
        tree_ref = commit if subdir in ("", ".") else commit + ":" + subdir
        entries = git(repo, "ls-tree", "-r", "-z", tree_ref, bare=True, binary=True)
        files = {}
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            meta, filename = entry.split(b"\t", 1)
            mode, kind, digest = meta.split()
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise SyncError("上游包含不支持的链接或子模块")
            files[filename.decode()] = digest.decode()
        return files

    def matches(self, path, tree, hashes=None):
        hashes = {} if hashes is None else hashes
        for filename, digest in tree.items():
            local = path / filename
            if local.is_symlink() or not local.is_file():
                return False
            if filename not in hashes:
                data = local.read_bytes()
                algorithm = hashlib.sha256 if len(digest) == 64 else hashlib.sha1
                hashes[filename] = algorithm(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if hashes[filename] != digest:
                return False
        return True

    def ensure_commit(self, repo, commit):
        if not SHA_RE.fullmatch(commit):
            raise SyncError("无效的市场提交")
        try:
            git(repo, "cat-file", "-e", commit + "^{commit}", bare=True)
        except SyncError:
            git(repo, "fetch", "--quiet", "--depth=1", "origin", commit, bare=True)

    def inspect(self, row):
        row.update(error="", outcome="", canUpdate=False)
        if not self.configure_row(row):
            return row
        repo = self.fetch(row)
        self.select_subdir(repo, row)
        market_commit = row["marketCommit"]
        if market_commit:
            try:
                self.ensure_commit(repo, market_commit)
                row["marketVersion"] = self.manifest_at(repo, market_commit, row["manifestPath"]).get("version", "")
            except (SyncError, ValueError):
                pass  # A fork may not contain the marketplace's pinned commit.
        if not row["git"] and not row["symlink"]:
            candidates = [row["localCommit"], row["upstreamCommit"], market_commit]
            candidates += git(repo, "rev-list", "--max-count=40", row["upstreamCommit"], bare=True).splitlines()
            hashes = {}
            row["localCommit"] = ""
            trees = {}
            known_files = set()
            for candidate in dict.fromkeys(candidates):
                if not candidate:
                    continue
                try:
                    tree = self.tree(repo, candidate, row["sourceDir"])
                    trees[candidate] = tree
                    known_files.update(tree)
                except SyncError:
                    continue
            for candidate, tree in trees.items():
                try:
                    path = Path(row["path"])
                    obsolete = any((path / name).exists() for name in known_files - tree.keys())
                    if not obsolete and self.matches(path, tree, hashes):
                        row["localCommit"] = candidate
                        break
                except OSError:
                    continue
            if not row["localCommit"]:
                row["dirty"] = True
        self.set_market_state(row)
        if row["symlink"]:
            row.update(status="sync-needed", canUpdate=True)
        elif row["git"]:
            self.set_git_status(row, repo)
        elif not row["localCommit"]:
            row.update(status="sync-needed", canUpdate=True)
        elif row["localCommit"] == row["upstreamCommit"]:
            row["status"] = "current"
        else:
            row.update(status="available", canUpdate=True)
        row["checkedAt"] = time.time()
        return row

    def set_market_state(self, row):
        market_commit = row["marketCommit"]
        if market_commit and row["localCommit"]:
            row["marketState"] = "same" if row["localCommit"] == market_commit and not row["dirty"] else "different"
        elif row["marketVersion"]:
            row["marketState"] = "version-same" if row["version"] == row["marketVersion"] else "different"

    def set_git_status(self, row, repo):
        row.update(error="", canUpdate=False)
        if row["localCommit"] == row["upstreamCommit"]:
            row.update(status="sync-needed" if row["dirty"] else "current", canUpdate=row["dirty"])
        elif is_ancestor(Path(row["path"]), row["upstreamCommit"], row["localCommit"], repo) is True:
            row.update(status="ahead", error="本地包含尚未推送的提交")
        elif is_ancestor(Path(row["path"]), row["localCommit"], row["upstreamCommit"], repo) is False:
            row.update(status="diverged", error="本地与上游分叉，合并后再更新")
        else:
            row.update(status="available", canUpdate=True)

    def scan(self):
        self.load_market(network=False)
        self.discover()
        for row in self.state["plugins"]:
            old_status = row["status"]
            old_commit = row["localCommit"]
            if self.configure_row(row):
                if row["symlink"]:
                    row.update(status="sync-needed", canUpdate=True, error="")
                    self.set_market_state(row)
                    continue
                if old_status in ("checking", "updating", "preparing", "prepared") or (row["git"] and old_commit != row["localCommit"]):
                    row.update(status="unchecked", canUpdate=False)
                self.set_market_state(row)
                repo = self.cache_dir / "repos" / (row["id"] + ".git")
                if row["git"] and row["upstreamCommit"] and repo.exists():
                    self.set_git_status(row, repo)
        self.state.update(busy=False, action="", pid=0, progress={"done": 0, "total": 0})
        self.save()

    def check(self, ids=None):
        previous_order = {row["id"]: index for index, row in enumerate(self.state["plugins"])}
        self.discover()
        # Discovery uses directory order; keep existing cards in their displayed order.
        self.state["plugins"].sort(key=lambda row: previous_order.get(row["id"], len(previous_order)))
        rows = [r for r in self.state["plugins"] if not ids or r["id"] in ids]
        self.state.update(busy=True, action="check", pid=os.getpid(),
                          progress={"done": 0, "total": len(rows)}, message="正在检查更新…")
        for row in rows:
            row.update(status="checking", error="", outcome="")
        self.save()
        self.load_market()
        def inspect_copy(row):
            copy = row.copy()
            try:
                return self.inspect(copy)
            except (SyncError, OSError, ValueError) as exc:
                copy.update(status="error", error=str(exc), canUpdate=False)
                return copy
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(inspect_copy, row): row for row in rows}
            for future in concurrent.futures.as_completed(futures):
                futures[future].update(future.result())
                self.state["progress"]["done"] += 1
                self.save()
        self.state.update(busy=False, action="", pid=0, checkedAt=time.time(), message="")
        self.save()

    def export(self, repo, row, destination):
        ref = row["upstreamCommit"]
        if row.get("sourceDir", ".") not in ("", "."):
            ref += ":" + row["sourceDir"]
        archive = git(repo, "archive", "--format=tar", ref, bare=True, binary=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            for member in tar.getmembers():
                p = PurePosixPath(member.name)
                if p.is_absolute() or ".." in p.parts or ".git" in p.parts or not (member.isfile() or member.isdir()):
                    raise SyncError("上游文件路径无效")
            tar.extractall(destination, filter="data")

    def preserve_extras(self, source, dest, old_tree, new_tree):
        for path in source.rglob("*"):
            relative = path.relative_to(source)
            rel = relative.as_posix()
            if ".git" in relative.parts or rel in old_tree or rel in new_tree:
                continue
            # Upstream owns its file paths, including paths that replace a
            # local directory. The complete original remains in the backup.
            if any(parent.as_posix() in new_tree for parent in relative.parents):
                continue
            target = dest / rel
            if target.is_dir():
                continue
            if path.is_symlink():
                raise SyncError("本地额外文件含链接，无法自动更新")
            if path.is_dir():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)

    def snapshot_files(self, repo, row):
        if row["localCommit"]:
            return self.tree(repo, row["localCommit"], row.get("sourceDir", "."))
        candidates = [row["upstreamCommit"], row["marketCommit"]]
        candidates += git(repo, "rev-list", "--max-count=40", row["upstreamCommit"], bare=True).splitlines()
        files = {}
        for commit in dict.fromkeys(candidates):
            if not commit:
                continue
            with contextlib.suppress(SyncError):
                files.update(self.tree(repo, commit, row.get("sourceDir", ".")))
        return files

    def stage_git(self, stage, backup, repo, row):
        metadata = backup / ".git"
        if not metadata.is_dir() or metadata.is_symlink():
            raise SyncError("此插件使用外部 Git 工作区，请先改为独立安装")
        shutil.copytree(metadata, stage / ".git", symlinks=True)
        # Work only in the staged copy; the running plugin and its Git state
        # stay intact until the validated directory is exchanged atomically.
        def staged_git(*args):
            return git(stage, "--work-tree", str(stage), *args)
        if staged_git("rev-parse", "HEAD") != row["localCommit"]:
            raise SyncError("插件在备份期间产生了新的提交，请重新检查")
        staged_git("fetch", "--quiet", "--no-tags", "--update-shallow", str(repo), row["upstreamCommit"])
        staged_git("reset", "--hard", row["upstreamCommit"])
        try:
            branch = staged_git("symbolic-ref", "--short", "HEAD")
            remote = staged_git("config", "--get", f"branch.{branch}.remote")
            ref = staged_git("config", "--get", f"branch.{branch}.merge")
        except SyncError:
            return  # Detached checkouts have no tracking reference to advance.
        if remote != "." and ref.startswith("refs/heads/"):
            staged_git("update-ref", "refs/remotes/" + remote + "/" + ref.removeprefix("refs/heads/"), row["upstreamCommit"])

    def independent_git(self, stage, repo, row):
        # Suite plugins keep the exported subtree and its commit in state.
        if row.get("sourceDir", ".") not in ("", "."):
            return
        ref = row["fetchRef"]
        if not ref.startswith("refs/heads/"):
            refs = git(repo, "ls-remote", "--symref", "origin", "HEAD", bare=True)
            ref = next((line.split()[1] for line in refs.splitlines()
                        if line.startswith("ref: refs/heads/")), "")
        if not ref.startswith("refs/heads/"):
            raise SyncError("无法确认 Git 上游默认分支")
        branch = ref.removeprefix("refs/heads/")
        git(stage, "init", "--quiet", "--template=", "--initial-branch", branch)
        git(stage, "remote", "add", "origin", row["repo"])
        git(stage, "fetch", "--quiet", "--no-tags", "--update-shallow", str(repo), row["upstreamCommit"])
        git(stage, "reset", "--hard", row["upstreamCommit"])
        git(stage, "update-ref", "refs/remotes/origin/" + branch, row["upstreamCommit"])
        git(stage, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/" + branch)
        git(stage, "config", f"branch.{branch}.remote", "origin")
        git(stage, "config", f"branch.{branch}.merge", ref)
        row.update(branch=branch, fetchRef=ref)

    def validate(self, stage, plugin_id):
        manifest = read_json(stage / "manifest.json", {}) or {}
        if manifest.get("id") != plugin_id or manifest.get("schemaVersion") != 1:
            raise SyncError("新版本插件标识或 manifest 无效")
        required = {"bar": "bar", "bar-widget": "barWidget", "panel": "panel", "overlay": "overlay",
                    "service": "service", "menu": "menu"}
        kinds, entries = manifest.get("kinds"), manifest.get("entryPoints")
        if not isinstance(kinds, list) or not kinds or not isinstance(entries, dict) or not manifest.get("version"):
            raise SyncError("新版本 manifest 缺少必要字段")
        for kind in kinds:
            if kind in required and required[kind] not in entries:
                raise SyncError("新版本缺少入口：" + kind)
        for entry in entries.values():
            if not isinstance(entry, str) or not entry or PurePosixPath(entry).is_absolute() or ".." in entry or not (stage / entry).is_file():
                raise SyncError("新版本入口文件无效")
        if shutil.which("omarchy-plugin-validate"):
            run(["omarchy-plugin-validate", str(stage)], timeout=15)

    def replace_snapshot(self, stage, target):
        # Linux rename exchange keeps the live plugin path present throughout.
        libc = ctypes.CDLL(None, use_errno=True)
        exchange = getattr(libc, "renameat2", None)
        if exchange is not None:
            result = exchange(-100, os.fsencode(stage), -100, os.fsencode(target), 2)
            if result == 0:
                return
        old = stage.with_name(stage.name + "-old")
        os.replace(target, old)
        try:
            os.replace(stage, target)
        except BaseException:
            os.replace(old, target)
            raise
        remove_install_path(old)

    def file_fingerprint(self, root):
        files = {}
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if ".git" in relative.parts:
                continue
            if path.is_symlink():
                files[relative.as_posix()] = "link:" + os.readlink(path)
            elif path.is_file():
                with path.open("rb") as stream:
                    files[relative.as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        return files

    def write_journal(self):
        if self.journal is not None:
            atomic_json(self.journal_path, self.journal)

    def recover_update(self):
        journal = read_json(self.journal_path)
        if not journal:
            return
        previous = {row["id"]: row for row in self.state["plugins"]}
        for item in journal.get("items", []):
            plugin_id = item.get("id", "")
            stage = Path(item.get("stage", ""))
            target = self.plugins_dir / plugin_id
            if not safe_id(plugin_id) or stage.parent != self.plugins_dir or not stage.name.startswith(".git-sync-"):
                raise SyncError("更新恢复记录无效，未改动插件")
            old = stage.with_name(stage.name + "-old")
            if not target.exists() and not target.is_symlink() and (old.exists() or old.is_symlink()):
                os.replace(old, target)
            row = previous.get(plugin_id, item["row"]).copy()
            # An exchange may have succeeded just before the worker died and
            # before the result was saved. Recognize that exact installed tree.
            installed = self.installed_matches(item, target)
            if installed:
                row.update(item["row"])
                self.mark_installed(row, target)
            elif row.get("outcome") != "failed":
                row.update(status="error", canUpdate=False, outcome="failed",
                           error="上次更新已中断，原安装保留，请重试")
            previous[plugin_id] = row
            remove_install_path(stage)
            remove_install_path(old)
        self.state["plugins"] = list(previous.values())
        self.state.update(busy=False, action="", phase="", activeId="", pid=0,
                          message="已恢复上次中断的更新，请检查插件状态")
        self.save()
        self.journal_path.unlink()

    def installed_matches(self, item, target):
        if not item.get("ready") or target.is_symlink() or not self.matches(target, item["tree"]):
            return False
        if item["row"]["git"]:
            try:
                return git(target, "rev-parse", "HEAD", timeout=5) == item["row"]["upstreamCommit"]
            except SyncError:
                return False
        return True

    def prepare_one(self, row):
        self.inspect(row)
        if row["status"] == "current":
            row["outcome"] = "unchanged"
            return None
        if not row["canUpdate"]:
            raise SyncError(row.get("error") or "此插件没有可更新的 Git 来源")
        row["status"] = "preparing"
        path = Path(row["path"])
        linked = row["symlink"]
        original_link = os.readlink(path) if linked else ""
        repo = self.cache_dir / "repos" / (row["id"] + ".git")
        commit = row["upstreamCommit"]
        stage = Path(tempfile.mkdtemp(prefix=".git-sync-", dir=self.plugins_dir))
        item = {"id": row["id"], "stage": str(stage), "row": row.copy(), "ready": False}
        if self.journal is not None:
            self.journal["items"].append(item)
            self.write_journal()
        try:
            self.export(repo, row, stage)
            old_tree = self.snapshot_files(repo, row) if not row["git"] else None
            new_tree = self.tree(repo, commit, row.get("sourceDir", "."))
            if row["git"]:
                tracked = git(path, "ls-files", "-z", binary=True).split(b"\0")
                old_tree = {p.decode(): "" for p in tracked if p}
            self.preserve_extras(path, stage, old_tree, new_tree)
            self.validate(stage, row["id"])
            if row["git"] and not linked:
                if git(path, "rev-parse", "HEAD") != row["localCommit"]:
                    raise SyncError("插件在更新期间产生了新的提交，请重新检查")
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = self.state_dir / "backups" / (row["id"] + "-" + stamp)
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(path, backup, symlinks=True)
            row["backup"] = str(backup)
            if linked:
                link_backup = backup.with_name(backup.name + ".link.json")
                atomic_json(link_backup, {"pluginId": row["id"], "installPath": str(path),
                                         "linkTarget": original_link, "sourcePath": str(path.resolve()),
                                         "backup": str(backup), "upstreamCommit": commit})
                row["linkBackup"] = str(link_backup)
            self.save()
            self.preserve_extras(backup, stage, old_tree, new_tree)
            if linked:
                self.independent_git(stage, repo, row)
                if not path.is_symlink() or os.readlink(path) != original_link:
                    raise SyncError("插件的安装位置发生变化，请重新检查")
            elif row["git"]:
                self.stage_git(stage, backup, repo, row)
                if git(path, "rev-parse", "HEAD") != row["localCommit"]:
                    raise SyncError("插件在更新期间产生了新的提交，请重新检查")
            if not self.matches(stage, new_tree):
                raise SyncError("暂存文件与 Git 上游不一致，未应用更新")
            self.validate(stage, row["id"])
            row.update(status="prepared", outcome="")
            item.update(row=row.copy(), ready=True, tree=new_tree)
            self.write_journal()
            return dict(row=row, stage=stage, path=path, linked=linked,
                        original_link=original_link, fingerprint=self.file_fingerprint(backup), item=item)
        except BaseException:
            remove_install_path(stage)
            raise

    def commit_one(self, prepared, guard):
        row, path = prepared["row"], prepared["path"]
        guard.ensure_active()
        if prepared["linked"]:
            if not path.is_symlink() or os.readlink(path) != prepared["original_link"]:
                raise SyncError("插件的安装位置发生变化，请重新检查")
        else:
            if path.is_symlink():
                raise SyncError("插件的安装位置发生变化，请重新检查")
            if row["git"] and git(path, "rev-parse", "HEAD", timeout=5) != row["localCommit"]:
                raise SyncError("插件在更新期间产生了新的提交，请重新检查")
            if self.file_fingerprint(path) != prepared["fingerprint"]:
                raise SyncError("插件在准备期间发生变化，原安装保留，请重新检查")
        guard.ensure_active()
        try:
            self.replace_snapshot(prepared["stage"], path)
        except BaseException:
            if self.installed_matches(prepared["item"], path):
                guard.changed = True
                self.mark_installed(row, path)
                prepared["item"].update(row=row.copy(), installed=True)
                self.write_journal()
            raise
        guard.changed = True
        self.mark_installed(row, path)
        prepared["item"].update(row=row.copy(), installed=True)
        self.write_journal()

    def mark_installed(self, row, path):
        row.update(version=row["latestVersion"], localCommit=row["upstreamCommit"],
                   git=(path / ".git").exists(), symlink=False, status="current",
                   canUpdate=False, dirty=False, outcome="updated", error="")
        self.set_market_state(row)

    def update(self, ids):
        self.load_market(network=False)
        self.discover()
        if ids == ["all"]:
            targets = [r for r in self.state["plugins"] if r.get("canUpdate")]
        else:
            missing = set(ids) - {r["id"] for r in self.state["plugins"]}
            if missing:
                raise SyncError("插件未安装：" + ", ".join(sorted(missing)))
            targets = [r for r in self.state["plugins"] if r["id"] in ids]
        self.state.update(busy=True, action="update", phase="prepare", pid=os.getpid(),
                          progress={"done": 0, "total": len(targets)}, message="正在准备更新…",
                          reloadMode="none", reloadError="")
        for row in self.state["plugins"]:
            row["outcome"] = ""
        self.save()
        updated = failed = 0
        prepared = []
        self.journal = {"items": []}
        self.write_journal()
        def fail(row, exc):
            row.update(status="error", error=str(exc), outcome="failed", canUpdate=False)
        try:
            for index, row in enumerate(targets):
                row.update(status="preparing", error="")
                self.state.update(activeId=row["id"], message=f"正在准备更新 {index + 1}/{len(targets)}…")
                self.save()
                try:
                    entry = self.prepare_one(row)
                    if entry:
                        prepared.append(entry)
                except (SyncError, OSError, ValueError) as exc:
                    fail(row, exc)
                    failed += 1
                self.state["progress"]["done"] += 1
                self.save()

            if prepared:
                with ReloadGuard(self.plugins_dir, enabled=not os.environ.get("OMARCHY_GIT_SYNC_NO_RESCAN")) as guard:
                    self.state.update(phase="install", reloadMode=guard.mode,
                                      progress={"done": len(targets) - len(prepared), "total": len(targets)},
                                      message="正在集中安装…")
                    for entry in prepared:
                        row = entry["row"]
                        row.update(status="updating", error="")
                        self.state["activeId"] = row["id"]
                        self.save()
                        try:
                            self.commit_one(entry, guard)
                            updated += 1
                        except (SyncError, OSError, ValueError, RuntimeError) as exc:
                            if row.get("outcome") == "updated":
                                updated += 1
                            else:
                                fail(row, exc)
                                failed += 1
                        self.state["progress"]["done"] += 1
                        self.save()
                    for entry in prepared:
                        remove_install_path(entry["stage"])
                    if guard.changed and guard.enabled:
                        self.state.update(phase="finish", activeId="", message=f"已更新 {updated} 个插件，正在统一重载…")
                        self.save()
                        time.sleep(0.5)  # Let the completion animation appear before components unload.
                if guard.changed and guard.enabled and not guard.reloaded:
                    self.state["reloadError"] = "插件已安装，自动重载未成功，请点击重载 Shell"
        except BaseException:
            for entry in prepared:
                if entry["row"].get("outcome") != "updated":
                    fail(entry["row"], "更新已中断，原安装保留，请重试")
            self.state.update(busy=False, action="", phase="", activeId="", pid=0, message="更新已中断")
            self.save()
            raise
        finally:
            for entry in prepared:
                remove_install_path(entry["stage"])
            # Leave the journal if cleanup failed; the next locked command recovers it.
            self.journal_path.unlink(missing_ok=True)
            self.journal = None
        self.state.update(busy=False, action="", phase="", activeId="", pid=0,
                          message=f"已更新 {updated} 个插件" + (f"，{failed} 个失败" if failed else ""))
        if self.state["reloadError"]:
            self.state["message"] += " · " + self.state["reloadError"]
        elif self.state["reloadMode"] == "coalesced":
            self.state["message"] += " · 当前 Shell 无法暂停自动重载，已集中安装"
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["scan", "check", "update", "status"])
    parser.add_argument("ids", nargs="*")
    args = parser.parse_args()
    manager = Manager()
    if args.action == "status":
        print(json.dumps(manager.state, ensure_ascii=False))
        return 0
    try:
        with manager.lock():
            if args.action == "scan":
                manager.scan()
            elif args.action == "check":
                manager.check(args.ids)
            elif not args.ids:
                raise SyncError("指定插件 ID 或 all")
            else:
                manager.update(args.ids)
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except BaseException as exc:
        if manager.state.get("pid") == os.getpid():
            manager.state.update(busy=False, action="", pid=0, message="任务中断：" + str(exc))
            manager.save()
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
