import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Manager, SyncError, git as backend_git, market_entries, run as backend_run, validate_repo, REPO_AUTH_ERROR


class UpdatesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.plugins = self.root / "plugins"
        self.plugins.mkdir()
        self.market_path = self.root / "market.json"
        self.sources = []
        self.env = patch.dict(os.environ, {
            "OMARCHY_GIT_SYNC_PLUGINS_DIR": str(self.plugins),
            "OMARCHY_GIT_SYNC_STATE_DIR": str(self.root / "state"),
            "OMARCHY_GIT_SYNC_CACHE_DIR": str(self.root / "cache"),
            "OMARCHY_GIT_SYNC_MARKET_URL": self.market_path.as_uri(),
            "OMARCHY_GIT_SYNC_NO_RESCAN": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.temp.cleanup)

    def git(self, directory, *args):
        result = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True,
                                env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def commit(self, repo, content, message="New upstream code"):
        (repo / "Service.qml").write_text(content)
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-qm", message)
        return self.git(repo, "rev-parse", "HEAD")

    def plugin(self, plugin_id="test.clock", snapshot=False):
        repo = self.root / (plugin_id + "-upstream")
        repo.mkdir()
        self.git(repo, "init", "-b", "main")
        self.git(repo, "config", "user.email", "test@example.invalid")
        self.git(repo, "config", "user.name", "Test")
        manifest = dict(schemaVersion=1, id=plugin_id, name=plugin_id, description="Test plugin", version="1.0.0", kinds=["service"], entryPoints={"service": "Service.qml"})
        (repo / "manifest.json").write_text(json.dumps(manifest))
        old = self.commit(repo, "import QtQuick\nItem {}\n", "Original code")
        installed = self.plugins / plugin_id
        if snapshot:
            shutil.copytree(repo, installed, ignore=shutil.ignore_patterns(".git"))
        else:
            self.git(self.plugins, "clone", repo.as_uri(), str(installed))
        source = dict(repo=repo.as_uri(), listingValidatedCommit=old, plugins={plugin_id: {"manifestPath": "manifest.json"}})
        self.sources.append(source)
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        return repo, installed, old

    def row(self, manager, plugin_id="test.clock"):
        return next(row for row in manager.state["plugins"] if row["id"] == plugin_id)

    def linked_plugin(self, snapshot=False, relative=False):
        repo, installed, old = self.plugin(snapshot=snapshot)
        developer = self.root / "developer"
        installed.rename(developer)
        target = os.path.relpath(developer, installed.parent) if relative else developer
        installed.symlink_to(target, target_is_directory=True)
        return repo, developer, installed, old

    def fingerprint(self, directory):
        return {path.relative_to(directory).as_posix():
                "link:" + str(path.readlink()) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.rglob("*") if path.is_symlink() or path.is_file()}

    def test_credential_origin_never_reaches_command_arguments_or_state(self):
        repo, installed, old = self.plugin()
        url = "https://FAKE_ACCESS_TOKEN@example.invalid/plugin.git"
        self.git(installed, "remote", "set-url", "origin", url)
        commands = []
        def record(args, **kwargs):
            commands.append(args)
            return backend_run(args, **kwargs)
        manager = Manager()
        with patch("backend.run", side_effect=record):
            manager.scan()
            manager.check()
            manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["error"], REPO_AUTH_ERROR)
        self.assertFalse(row["canUpdate"])
        self.assertEqual(row["repo"], "")
        self.assertNotIn("FAKE_ACCESS_TOKEN", manager.state_path.read_text())
        self.assertFalse(any("FAKE_ACCESS_TOKEN" in str(args) for args in commands))
        self.assertEqual(self.git(installed, "remote", "get-url", "origin"), url)
        self.assertFalse((manager.cache_dir / "repos" / "test.clock.git").exists())

    def test_sensitive_market_url_is_blocked_and_not_cached(self):
        self.plugin(snapshot=True)
        self.sources[0]["repo"] = "https://user:FAKE_ACCESS_TOKEN@example.invalid/plugin.git"
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["error"], REPO_AUTH_ERROR)
        self.assertNotIn("FAKE_ACCESS_TOKEN", manager.state_path.read_text())
        self.assertNotIn("FAKE_ACCESS_TOKEN", (manager.cache_dir / "market.json").read_text())
        manager.check()
        self.assertEqual(self.row(manager)["error"], REPO_AUTH_ERROR)

    def test_legacy_sensitive_cache_and_state_are_removed_under_lock(self):
        self.plugin()
        manager = Manager()
        manager.check()
        cache = manager.cache_dir / "repos" / "test.clock.git"
        url = "https://FAKE_ACCESS_TOKEN@example.invalid/plugin.git"
        self.git(cache, "remote", "set-url", "origin", url)
        data = json.loads(manager.state_path.read_text())
        data["plugins"][0].update(repo=url, error="failure " + url)
        manager.state_path.write_text(json.dumps(data))
        (manager.cache_dir / "market.json").write_text(json.dumps({"sources": [{"repo": url}]}))
        recovered = Manager()
        with recovered.lock():
            self.assertFalse(cache.exists())
            self.assertFalse((manager.cache_dir / "market.json").exists())
            self.assertNotIn("FAKE_ACCESS_TOKEN", manager.state_path.read_text())
        recovered.check()
        self.assertEqual(self.row(recovered)["status"], "current")

    def test_git_url_rewrite_is_checked_before_transport(self):
        repo = self.root / "bare.git"
        self.git(self.root, "init", "--bare", str(repo))
        self.git(repo, "remote", "add", "origin", "https://example.invalid/plugin.git")
        self.git(repo, "config", "url.https://FAKE_ACCESS_TOKEN@example.invalid/.insteadOf", "https://example.invalid/")
        commands = []
        def record(args, **kwargs):
            commands.append(args)
            return backend_run(args, **kwargs)
        with patch("backend.run", side_effect=record), self.assertRaisesRegex(SyncError, REPO_AUTH_ERROR):
            backend_git(repo, "fetch", "origin", bare=True)
        with patch("backend.run", side_effect=record), self.assertRaisesRegex(SyncError, REPO_AUTH_ERROR):
            backend_git(repo, "--work-tree", str(repo), "fetch", "origin", bare=True)
        self.assertFalse(any("fetch" in args for args in commands))
        self.assertFalse(any("FAKE_ACCESS_TOKEN" in str(args) for args in commands))

    def test_repo_auth_validation_keeps_ssh_and_credential_helper_urls(self):
        for url in ("https://example.invalid/repo.git", "git@example.invalid:repo.git",
                    "ssh://git@example.invalid/repo.git", "file:///tmp/repo.git", "/tmp/repo.git"):
            self.assertEqual(validate_repo(url), url)
        for url in ("https://token@example.invalid/repo.git", "https://user:token@example.invalid/repo.git",
                    "ssh://git:token@example.invalid/repo.git", "https://example.invalid/repo.git?token=secret"):
            with self.assertRaisesRegex(SyncError, REPO_AUTH_ERROR):
                validate_repo(url)
            with self.assertRaisesRegex(SyncError, REPO_AUTH_ERROR):
                backend_git(self.root, "remote", "add", "origin", url)

    def test_same_version_new_commit_is_update_and_market_comparison_is_separate(self):
        repo, installed, old = self.plugin()
        latest = self.commit(repo, "import QtQuick\nItem { property int xValue: 2 }\n")
        manager = Manager()
        manager.check()
        row = self.row(manager)
        self.assertEqual(row["status"], "available")
        self.assertEqual(row["marketState"], "same")
        self.assertEqual(row["version"], row["latestVersion"])
        self.assertEqual(row["upstreamCommit"], latest)
        manager.update([row["id"]])
        row = self.row(manager)
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(row["status"], "current")
        self.assertEqual(row["marketState"], "different")
        self.assertTrue((Path(row["backup"]) / "manifest.json").is_file())

    def test_dirty_old_git_installation_syncs_head_files_and_tracking_reference_after_backup(self):
        repo, installed, old = self.plugin()
        (repo / "New.qml").write_text("import QtQuick\nItem {}\n")
        latest = self.commit(repo, "import QtQuick\nItem { property int xValue: 2 }\n")
        (installed / "Service.qml").write_text("my local customization")
        self.git(installed, "add", "Service.qml")
        (installed / "New.qml").write_text("local copy of the new module")
        (installed / "settings.json").write_text('{"volume": 50}')
        manager = Manager()
        manager.check()
        row = self.row(manager)
        self.assertEqual(row["status"], "available")
        self.assertTrue(row["canUpdate"])
        self.assertEqual(row["marketState"], "different")
        manager.scan()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update([row["id"]])
        row = self.row(manager)
        self.assertEqual((installed / "Service.qml").read_text(), (repo / "Service.qml").read_text())
        self.assertEqual((installed / "New.qml").read_text(), (repo / "New.qml").read_text())
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.git(installed, "rev-parse", "origin/main"), latest)
        self.assertEqual(self.git(installed, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual((installed / "settings.json").read_text(), '{"volume": 50}')
        backup = Path(row["backup"])
        self.assertEqual((backup / "Service.qml").read_text(), "my local customization")
        self.assertEqual((backup / "New.qml").read_text(), "local copy of the new module")
        self.assertEqual(self.git(backup, "rev-parse", "HEAD"), old)
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")

    def test_new_files_copied_over_old_git_record_still_offer_an_update(self):
        repo, installed, _ = self.plugin()
        latest = self.commit(repo, "import QtQuick\nItem { property int upstreamValue: 7 }\n")
        shutil.copy2(repo / "Service.qml", installed / "Service.qml")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.git(installed, "status", "--porcelain"), "")

    def test_current_git_commit_with_changed_files_can_be_synced(self):
        repo, installed, current = self.plugin()
        (installed / "Service.qml").write_text("modified current version")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "sync-needed")
        self.assertTrue(self.row(manager)["canUpdate"])
        manager.scan()
        self.assertEqual(self.row(manager)["status"], "sync-needed")
        self.assertEqual(self.row(manager)["marketVersion"], "1.0.0")
        manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["status"], "current")
        self.assertEqual(row["marketState"], "same")
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), current)
        self.assertEqual((installed / "Service.qml").read_text(), (repo / "Service.qml").read_text())
        self.assertEqual((Path(row["backup"]) / "Service.qml").read_text(), "modified current version")

    def test_snapshot_updates_from_git_and_preserves_settings(self):
        repo, installed, old = self.plugin(snapshot=True)
        (installed / "settings.json").write_text('{"volume": 50}')
        latest = self.commit(repo, "import QtQuick\nItem { property int xValue: 3 }\n")
        manager = Manager()
        manager.check()
        row = self.row(manager)
        self.assertEqual(row["localCommit"], old)
        self.assertEqual(row["status"], "available")
        manager.update([row["id"]])
        row = self.row(manager)
        self.assertEqual(row["localCommit"], latest)
        self.assertEqual((installed / "settings.json").read_text(), '{"volume": 50}')
        manager.check()
        row = self.row(manager)
        self.assertEqual(row["status"], "current")

    def test_unidentified_snapshot_syncs_after_backing_up_local_code(self):
        repo, installed, _ = self.plugin(snapshot=True)
        latest = self.commit(repo, "import QtQuick\nItem { property int xValue: 3 }\n")
        (installed / "Service.qml").write_text("local code")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "sync-needed")
        self.assertTrue(self.row(manager)["canUpdate"])
        self.assertEqual(self.row(manager)["marketState"], "version-same")
        manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual((Path(row["backup"]) / "Service.qml").read_text(), "local code")
        self.assertEqual((installed / "Service.qml").read_text(), (repo / "Service.qml").read_text())
        self.assertEqual(row["localCommit"], latest)
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")

    def test_partial_installation_can_be_synced_without_matching_a_full_tree(self):
        repo, installed, _ = self.plugin(snapshot=True)
        (repo / "tests").mkdir()
        (repo / "tests/model.py").write_text("# Upstream test\n")
        latest = self.commit(repo, "import QtQuick\nItem {}\n")
        self.git(repo, "branch", "-M", "release")
        # Rebuild history with the omitted test already present in the first
        # commit, so no full upstream tree can match the partial installation.
        self.git(repo, "checkout", "--orphan", "main")
        self.git(repo, "commit", "-qm", "Initial release with tests")
        latest = self.git(repo, "rev-parse", "HEAD")
        self.sources[0]["listingValidatedCommit"] = latest
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "sync-needed")
        manager.update(["test.clock"])
        self.assertTrue((installed / "tests/model.py").exists())
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")

    def test_invalid_new_manifest_does_not_replace_installed_code(self):
        repo, installed, old = self.plugin()
        manifest = json.loads((repo / "manifest.json").read_text())
        manifest["entryPoints"] = {"service": "missing.qml"}
        (repo / "manifest.json").write_text(json.dumps(manifest))
        self.commit(repo, "import QtQuick\nItem { property int xValue: 3 }\n")
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), old)
        self.assertEqual(self.row(manager)["outcome"], "failed")

    def test_check_keeps_published_order_and_live_results_until_all_checks_finish(self):
        alpha, _, _ = self.plugin("test.alpha")
        self.plugin("test.beta")
        zulu, installed_zulu, _ = self.plugin("test.zulu")
        latest_zulu = self.commit(zulu, "import QtQuick\nItem { property int revision: 2 }\n")
        manager = Manager()
        manager.check()
        previous_order = [row["id"] for row in manager.state["plugins"]]
        self.assertEqual(previous_order, ["test.zulu", "test.alpha", "test.beta"])

        # The next check reverses which plugin needs an update, and one fails.
        self.git(installed_zulu, "fetch", "origin")
        self.git(installed_zulu, "reset", "--hard", latest_zulu)
        self.commit(alpha, "import QtQuick\nItem { property int revision: 3 }\n")
        published = []
        save = manager.save
        inspect = manager.inspect

        def record_save():
            save()
            published.append(json.loads(manager.state_path.read_text()))

        def inspect_with_failure(row):
            if row["id"] == "test.beta":
                raise SyncError("Repository unavailable")
            return inspect(row)

        with patch.object(manager, "save", side_effect=record_save), \
                patch.object(manager, "inspect", side_effect=inspect_with_failure):
            manager.check()

        checking = [snapshot for snapshot in published if snapshot["busy"]]
        self.assertEqual([snapshot["progress"]["done"] for snapshot in checking], [0, 1, 2, 3])
        for snapshot in checking:
            self.assertEqual([row["id"] for row in snapshot["plugins"]], previous_order)
        statuses = {row["id"]: row["status"] for row in checking[-1]["plugins"]}
        self.assertEqual(statuses, {"test.alpha": "available", "test.beta": "error", "test.zulu": "current"})
        self.assertFalse(published[-1]["busy"])
        self.assertEqual([row["id"] for row in published[-1]["plugins"]],
                         ["test.alpha", "test.beta", "test.zulu"])

    def test_partial_check_keeps_existing_order_and_appends_new_plugins_until_finished(self):
        self.plugin("test.current")
        zulu, _, _ = self.plugin("test.zulu")
        self.commit(zulu, "import QtQuick\nItem { property int revision: 2 }\n")
        manager = Manager()
        manager.check()
        self.plugin("test.alpha")
        published = []
        save = manager.save

        def record_save():
            save()
            published.append(json.loads(manager.state_path.read_text()))

        with patch.object(manager, "save", side_effect=record_save):
            manager.check(["test.zulu"])

        for snapshot in published[:-1]:
            self.assertEqual([row["id"] for row in snapshot["plugins"]],
                             ["test.zulu", "test.current", "test.alpha"])
            self.assertEqual(snapshot["progress"]["total"], 1)
        self.assertEqual([row["id"] for row in published[-1]["plugins"]],
                         ["test.zulu", "test.alpha", "test.current"])

    def test_bulk_continues_after_a_failure_and_latest_sort_last(self):
        broken, installed_broken, old = self.plugin("test.broken")
        working, installed_working, _ = self.plugin("test.working")
        self.plugin("test.current")
        manifest = json.loads((broken / "manifest.json").read_text())
        manifest["entryPoints"] = {"service": "missing.qml"}
        (broken / "manifest.json").write_text(json.dumps(manifest))
        self.commit(broken, "broken")
        latest = self.commit(working, "import QtQuick\nItem { property int xValue: 4 }\n")
        manager = Manager()
        manager.check()
        self.assertEqual(manager.state["plugins"][-1]["id"], "test.current")
        manager.update(["all"])
        self.assertEqual(self.git(installed_working, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.git(installed_broken, "rev-parse", "HEAD"), old)
        self.assertEqual(manager.state["progress"], {"done": 2, "total": 2})
        self.assertFalse(manager.state["busy"])

    def test_batch_prepares_every_backup_and_git_tree_before_first_install(self):
        plugins = {}
        for plugin_id in ("test.alpha", "test.beta"):
            repo, installed, old = self.plugin(plugin_id)
            latest = self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
            plugins[plugin_id] = (installed, old, latest)
        manager = Manager()
        manager.check()
        replace = manager.replace_snapshot
        calls = []
        def inspect_batch(stage, target):
            if not calls:
                journal = json.loads(manager.journal_path.read_text())
                self.assertEqual(len(journal["items"]), 2)
                for item in journal["items"]:
                    installed, old, latest = plugins[item["id"]]
                    self.assertTrue(item["ready"])
                    self.assertEqual(self.git(installed, "rev-parse", "HEAD"), old)
                    self.assertEqual(self.git(Path(item["stage"]), "rev-parse", "HEAD"), latest)
                    self.assertEqual(self.git(Path(item["row"]["backup"]), "rev-parse", "HEAD"), old)
            calls.append(target.name)
            replace(stage, target)
        with patch.object(manager, "replace_snapshot", side_effect=inspect_batch):
            manager.update(["all"])
        self.assertEqual(calls, ["test.alpha", "test.beta"])
        self.assertFalse(manager.journal_path.exists())
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_all_preparation_failures_and_no_changes_never_suspend_or_reload(self):
        repo, installed, old = self.plugin()
        manager = Manager()
        manager.check()
        with patch("backend.ReloadGuard") as guard:
            manager.update(["test.clock"])
            guard.assert_not_called()
        manifest = json.loads((repo / "manifest.json").read_text())
        manifest["entryPoints"]["service"] = "missing.qml"
        (repo / "manifest.json").write_text(json.dumps(manifest))
        self.commit(repo, "invalid new release")
        with patch("backend.ReloadGuard") as guard:
            manager.update(["test.clock"])
            guard.assert_not_called()
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), old)
        self.assertFalse(manager.journal_path.exists())

    def test_running_backend_does_not_write_bytecode_into_watched_plugin(self):
        _, installed, _ = self.plugin()
        source = Path(__file__).resolve().parents[1]
        for name in ("backend.py", "reload_guard.py"):
            shutil.copy2(source / name, installed / name)
        before = self.fingerprint(installed)
        child = subprocess.run([sys.executable, str(installed / "backend.py"), "scan"],
                               capture_output=True, timeout=5)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(self.fingerprint(installed), before)

    def test_changed_installed_files_after_preparation_are_kept(self):
        alpha, installed_alpha, old = self.plugin("test.alpha")
        beta, installed_beta, _ = self.plugin("test.beta")
        self.commit(alpha, "import QtQuick\nItem { property int revision: 2 }\n")
        latest_beta = self.commit(beta, "import QtQuick\nItem { property int revision: 3 }\n")
        manager = Manager()
        manager.check()
        prepare = manager.prepare_one
        def prepare_and_edit(row):
            entry = prepare(row)
            if row["id"] == "test.beta":
                (installed_alpha / "Service.qml").write_text("changed while the batch was preparing")
            return entry
        with patch.object(manager, "prepare_one", side_effect=prepare_and_edit):
            manager.update(["all"])
        self.assertEqual(self.git(installed_alpha, "rev-parse", "HEAD"), old)
        self.assertEqual((installed_alpha / "Service.qml").read_text(), "changed while the batch was preparing")
        self.assertEqual(self.row(manager, "test.alpha")["outcome"], "failed")
        self.assertEqual(self.git(installed_beta, "rev-parse", "HEAD"), latest_beta)

    def test_interruption_before_install_keeps_all_originals_and_cleans_stages(self):
        installed = []
        for plugin_id in ("test.alpha", "test.beta"):
            repo, path, old = self.plugin(plugin_id)
            self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
            installed.append((path, old))
        manager = Manager()
        manager.check()
        with patch.object(manager, "commit_one", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.update(["all"])
        for path, old in installed:
            self.assertEqual(self.git(path, "rev-parse", "HEAD"), old)
        self.assertFalse(manager.state["busy"])
        self.assertFalse(manager.journal_path.exists())
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_interruption_after_exchange_records_the_installed_result(self):
        repo, installed, _ = self.plugin()
        latest = self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
        manager = Manager()
        manager.check()
        replace = manager.replace_snapshot
        def exchange_then_interrupt(stage, target):
            replace(stage, target)
            raise KeyboardInterrupt()
        with patch.object(manager, "replace_snapshot", side_effect=exchange_then_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertFalse(manager.state["busy"])
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_killed_worker_recovers_completed_exchange_and_uninstalled_plugins(self):
        installed = {}
        for plugin_id in ("test.alpha", "test.beta"):
            repo, path, old = self.plugin(plugin_id)
            latest = self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
            installed[plugin_id] = (path, old, latest)
        Manager().check()
        code = '''import os, signal
from backend import Manager
manager = Manager()
replace = manager.replace_snapshot
def stop_after_exchange(stage, target):
    replace(stage, target)
    os.kill(os.getpid(), signal.SIGKILL)
manager.replace_snapshot = stop_after_exchange
with manager.lock():
    manager.update(["all"])
'''
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, timeout=10)
        self.assertEqual(child.returncode, -9, child.stderr)
        recovered = Manager()
        self.assertTrue(recovered.journal_path.exists())
        with recovered.lock():
            pass
        alpha, old_alpha, latest_alpha = installed["test.alpha"]
        beta, old_beta, latest_beta = installed["test.beta"]
        self.assertEqual(self.git(alpha, "rev-parse", "HEAD"), latest_alpha)
        self.assertEqual(self.row(recovered, "test.alpha")["outcome"], "updated")
        self.assertEqual(self.git(beta, "rev-parse", "HEAD"), old_beta)
        self.assertEqual(self.row(recovered, "test.beta")["outcome"], "failed")
        self.assertFalse(recovered.state["busy"])
        self.assertFalse(recovered.journal_path.exists())
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_killed_fallback_install_restores_the_original_directory(self):
        repo, installed, old = self.plugin()
        self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
        Manager().check()
        code = '''import os, signal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from backend import Manager
manager = Manager()
replace = os.replace
def stop_before_install(source, target):
    if Path(target).name == "test.clock" and Path(source).name.startswith(".git-sync-"):
        os.kill(os.getpid(), signal.SIGKILL)
    replace(source, target)
with manager.lock(), patch("backend.ctypes.CDLL", return_value=SimpleNamespace()), patch("backend.os.replace", side_effect=stop_before_install):
    manager.update(["test.clock"])
'''
        child = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
                               capture_output=True, timeout=10)
        self.assertEqual(child.returncode, -9, child.stderr)
        self.assertFalse(installed.exists())
        recovered = Manager()
        with recovered.lock():
            pass
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), old)
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_diverged_local_commits_are_not_reset(self):
        repo, installed, _ = self.plugin()
        self.git(installed, "config", "user.email", "test@example.invalid")
        self.git(installed, "config", "user.name", "Test")
        local = self.commit(installed, "import QtQuick\nItem { property int localValue: 9 }\n")
        self.commit(repo, "import QtQuick\nItem { property int upstreamValue: 3 }\n")
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), local)
        self.assertEqual(self.row(manager)["outcome"], "failed")

    def test_tracking_branch_is_used_instead_of_default_head(self):
        repo, installed, old = self.plugin()
        self.git(repo, "checkout", "-b", "stable")
        stable = self.commit(repo, "import QtQuick\nItem { property int stableValue: 1 }\n")
        self.git(installed, "fetch", "origin")
        self.git(installed, "checkout", "-b", "stable", "origin/stable")
        stable_new = self.commit(repo, "import QtQuick\nItem { property int stableValue: 2 }\n")
        self.git(repo, "checkout", "main")
        self.commit(repo, "import QtQuick\nItem { property int mainValue: 7 }\n")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["upstreamCommit"], stable_new)
        self.assertEqual(self.row(manager)["branch"], "stable")
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), stable_new)
        self.assertEqual(self.git(installed, "rev-parse", "origin/stable"), stable_new)
        self.assertEqual(self.git(installed, "symbolic-ref", "--short", "HEAD"), "stable")

    def test_offline_market_uses_cache_without_blocking_git(self):
        repo, installed, _ = self.plugin()
        manager = Manager()
        manager.check()
        self.market_path.unlink()
        self.commit(repo, "import QtQuick\nItem { property int xValue: 5 }\n")
        manager.check()
        self.assertTrue(manager.state["marketCached"])
        self.assertEqual(self.row(manager)["status"], "available")

    def test_new_upstream_file_replaces_local_collision_with_original_in_backup(self):
        repo, installed, old = self.plugin(snapshot=True)
        (installed / "settings.json").write_text("local settings")
        (repo / "settings.json").write_text("upstream settings")
        self.commit(repo, "import QtQuick\nItem { property int xValue: 6 }\n")
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual((installed / "settings.json").read_text(), "upstream settings")
        row = self.row(manager)
        self.assertEqual((Path(row["backup"]) / "settings.json").read_text(), "local settings")
        self.assertEqual(row["outcome"], "updated")

    def test_failed_staged_git_reset_leaves_live_files_and_git_record_intact(self):
        repo, installed, old = self.plugin()
        self.commit(repo, "import QtQuick\nItem { property int xValue: 6 }\n")
        (installed / "Service.qml").write_text("local code before failed sync")
        manager = Manager()
        manager.check()
        def fail_reset(path, *args, **kwargs):
            if "reset" in args:
                raise SyncError("Injected reset failure")
            return backend_git(path, *args, **kwargs)
        with patch("backend.git", side_effect=fail_reset):
            manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["outcome"], "failed")
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), old)
        self.assertEqual(self.git(installed, "rev-parse", "origin/main"), old)
        self.assertEqual((installed / "Service.qml").read_text(), "local code before failed sync")
        self.assertEqual((Path(row["backup"]) / "Service.qml").read_text(), "local code before failed sync")
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_git_backup_precedes_atomic_replacement_and_preserves_running_plugin(self):
        repo, installed, old = self.plugin()
        latest = self.commit(repo, "import QtQuick\nItem { property int xValue: 10 }\n")
        (installed / "Service.qml").write_text("live local code")
        manager = Manager()
        manager.check()
        original_replace = manager.replace_snapshot
        def check_backup(stage, target):
            row = self.row(manager)
            backup = Path(row["backup"])
            self.assertEqual((backup / "Service.qml").read_text(), "live local code")
            self.assertEqual(self.git(backup, "rev-parse", "HEAD"), old)
            self.assertEqual((target / "Service.qml").read_text(), "live local code")
            self.assertEqual(self.git(target, "rev-parse", "HEAD"), old)
            self.assertEqual(self.git(stage, "rev-parse", "HEAD"), latest)
            self.assertEqual(self.git(stage, "status", "--porcelain"), "")
            original_replace(stage, target)
        with patch.object(manager, "replace_snapshot", side_effect=check_backup):
            manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")

    def test_new_local_commit_during_sync_is_not_overwritten(self):
        repo, installed, _ = self.plugin()
        self.commit(repo, "import QtQuick\nItem { property int upstreamValue: 10 }\n")
        self.git(installed, "config", "user.email", "test@example.invalid")
        self.git(installed, "config", "user.name", "Test")
        manager = Manager()
        manager.check()
        original_stage = manager.stage_git
        local = []
        def commit_during_stage(*args):
            original_stage(*args)
            local.append(self.commit(installed, "import QtQuick\nItem { property int localValue: 10 }\n"))
        with patch.object(manager, "stage_git", side_effect=commit_during_stage):
            manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "failed")
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), local[0])
        self.assertIn("localValue", (installed / "Service.qml").read_text())

    def test_shallow_git_installation_can_sync_from_a_shallow_cache(self):
        repo, installed, old = self.plugin()
        shutil.rmtree(installed)
        self.git(self.plugins, "clone", "--depth=1", repo.as_uri(), str(installed))
        self.assertTrue((installed / ".git/shallow").exists())
        for revision in range(42):
            latest = self.commit(repo, f"import QtQuick\nItem {{ property int revision: {revision} }}\n")
        (installed / "Service.qml").write_text("old installation with copied files")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.git(installed, "status", "--porcelain"), "")

    def test_upstream_file_replaces_local_extra_directory_after_backup(self):
        repo, installed, _ = self.plugin(snapshot=True)
        (installed / "module").mkdir()
        (installed / "module/settings.json").write_text("local config inside colliding directory")
        (repo / "module").write_text("new upstream file")
        self.commit(repo, "import QtQuick\nItem {}\n")
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["outcome"], "updated")
        self.assertEqual((installed / "module").read_text(), "new upstream file")
        self.assertEqual((Path(row["backup"]) / "module/settings.json").read_text(), "local config inside colliding directory")

    def test_upstream_directory_replaces_local_extra_file_after_backup(self):
        repo, installed, _ = self.plugin(snapshot=True)
        (installed / "module").write_text("local file before upstream added directory")
        (repo / "module").mkdir()
        (repo / "module/code.qml").write_text("import QtQuick\nItem {}\n")
        self.commit(repo, "import QtQuick\nItem {}\n")
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["outcome"], "updated")
        self.assertTrue((installed / "module/code.qml").is_file())
        self.assertEqual((Path(row["backup"]) / "module").read_text(), "local file before upstream added directory")

    def test_cross_process_lock_rejects_concurrent_update(self):
        manager = Manager()
        with manager.lock():
            with self.assertRaises(SyncError):
                with Manager().lock():
                    self.fail("lock must not be acquired")

    def test_check_recovers_stale_cache_locks_without_changing_installations(self):
        installed = {}
        for plugin_id in ("test.alpha", "test.beta", "test.gamma", "test.delta"):
            repo, path, _ = self.plugin(plugin_id)
            installed[plugin_id] = (repo, path)
        manager = Manager()
        manager.check()
        before = {}
        for plugin_id, (repo, path) in installed.items():
            self.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
            (path / ".git/index.lock").touch()  # Installed repositories are outside recovery's scope.
            before[plugin_id] = self.fingerprint(path)
            (manager.cache_dir / "repos" / (plugin_id + ".git/shallow.lock")).touch()
        manager.check()
        for plugin_id, (_, path) in installed.items():
            row = self.row(manager, plugin_id)
            self.assertEqual(row["status"], "available", row["error"])
            self.assertEqual(row["error"], "")
            self.assertEqual(self.fingerprint(path), before[plugin_id])
            self.assertFalse(list((manager.cache_dir / "repos" / (plugin_id + ".git")).rglob("*.lock")))

    def test_check_preserves_locks_held_by_a_running_git_transaction(self):
        self.plugin()
        manager = Manager()
        manager.check()
        repo = manager.cache_dir / "repos/test.clock.git"
        commit = backend_git(repo, "rev-parse", "FETCH_HEAD", bare=True)
        process = subprocess.Popen(["git", "--git-dir", str(repo), "update-ref", "--stdin"],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.stdin.write("start\nupdate refs/heads/held " + commit + "\nprepare\n")
            process.stdin.flush()
            self.assertEqual(process.stdout.readline().strip(), "start: ok")
            self.assertEqual(process.stdout.readline().strip(), "prepare: ok")
            lock = repo / "refs/heads/held.lock"
            self.assertTrue(lock.exists())
            manager.check()
            self.assertEqual(self.row(manager)["status"], "error")
            self.assertIn("仍有任务在运行", self.row(manager)["error"])
            self.assertTrue(lock.exists())
            process.communicate("commit\n", timeout=3)
            self.assertEqual(process.returncode, 0)
            manager.check()
            self.assertEqual(self.row(manager)["status"], "current")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)

    def test_timeout_stops_git_children_and_releases_transaction_locks(self):
        self.plugin()
        manager = Manager()
        manager.check()
        repo = manager.cache_dir / "repos/test.clock.git"
        commit = backend_git(repo, "rev-parse", "FETCH_HEAD", bare=True)
        pid_file = self.root / "child.pid"
        code = '''import subprocess, sys, time
from pathlib import Path
child = subprocess.Popen(["git", "--git-dir", sys.argv[1], "update-ref", "--stdin"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
child.stdin.write("start\\nupdate refs/heads/held " + sys.argv[2] + "\\nprepare\\n")
child.stdin.flush()
assert child.stdout.readline().strip() == "start: ok"
assert child.stdout.readline().strip() == "prepare: ok"
Path(sys.argv[3]).write_text(str(child.pid))
time.sleep(30)
'''
        with self.assertRaisesRegex(SyncError, "连接超时"):
            backend_run([sys.executable, "-c", code, str(repo), commit, str(pid_file)], timeout=1)
        self.assertTrue(pid_file.exists(), "Git child did not reach the locked transaction")
        self.assertFalse((repo / "refs/heads/held.lock").exists())
        child = Path("/proc") / pid_file.read_text().strip()
        if child.exists():
            self.assertEqual((child / "stat").read_text().rsplit(") ", 1)[1].split()[0], "Z")
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")

    def test_timeout_kills_a_child_that_ignores_termination(self):
        pid_file = self.root / "stubborn.pid"
        code = '''import signal, subprocess, sys, time
child_code = "import os, signal, sys, time; from pathlib import Path; signal.signal(signal.SIGTERM, signal.SIG_IGN); Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
subprocess.Popen([sys.executable, "-c", child_code, sys.argv[1]])
time.sleep(30)
'''
        started = time.monotonic()
        try:
            with self.assertRaisesRegex(SyncError, "连接超时"):
                backend_run([sys.executable, "-c", code, str(pid_file)], timeout=0.5)
            self.assertLess(time.monotonic() - started, 5)
            self.assertTrue(pid_file.exists())
            child = Path("/proc") / pid_file.read_text().strip()
            if child.exists():
                self.assertEqual((child / "stat").read_text().rsplit(") ", 1)[1].split()[0], "Z")
        finally:
            if pid_file.exists():
                with contextlib.suppress(ProcessLookupError):
                    os.kill(int(pid_file.read_text()), signal.SIGKILL)

    def test_unpushed_local_commits_do_not_offer_an_update(self):
        repo, installed, _ = self.plugin()
        self.git(installed, "config", "user.email", "test@example.invalid")
        self.git(installed, "config", "user.name", "Test")
        self.commit(installed, "import QtQuick\nItem { property int custom: 1 }\n")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "ahead")
        self.assertFalse(self.row(manager)["canUpdate"])

    def test_upstream_file_deletion_is_detected_for_snapshot(self):
        repo, installed, _ = self.plugin(snapshot=True)
        (repo / "old.qml").write_text("old module")
        old = self.commit(repo, "import QtQuick\nItem {}\n")
        (installed / "old.qml").write_text("old module")
        self.sources[0]["listingValidatedCommit"] = old
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        (repo / "old.qml").unlink()
        latest = self.commit(repo, "import QtQuick\nItem {}\n")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update(["test.clock"])
        self.assertFalse((installed / "old.qml").exists())
        self.assertEqual(self.row(manager)["localCommit"], latest)

    def test_nested_marketplace_manifest_updates_only_the_plugin_subtree(self):
        repo, installed, _ = self.plugin(snapshot=True)
        subdir = repo / "widgets/clock"
        subdir.mkdir(parents=True)
        for name in ("manifest.json", "Service.qml"):
            shutil.move(str(repo / name), str(subdir / name))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "Move into a suite")
        old = self.git(repo, "rev-parse", "HEAD")
        self.sources[0]["listingValidatedCommit"] = old
        self.sources[0]["plugins"]["test.clock"]["manifestPath"] = "widgets/clock/manifest.json"
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        (subdir / "Service.qml").write_text("import QtQuick\nItem { property int suiteValue: 2 }\n")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "Update nested plugin")
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertIn("suiteValue", (installed / "Service.qml").read_text())
        self.assertFalse((installed / "widgets").exists())

    def test_snapshot_backup_is_created_before_atomic_replacement(self):
        repo, installed, old = self.plugin(snapshot=True)
        original = (installed / "Service.qml").read_text()
        self.commit(repo, "import QtQuick\nItem { property int xValue: 9 }\n")
        manager = Manager()
        manager.check()
        original_replace = manager.replace_snapshot
        def check_backup(stage, target):
            backups = list((manager.state_dir / "backups").iterdir())
            self.assertEqual(len(backups), 1)
            self.assertEqual((backups[0] / "Service.qml").read_text(), original)
            original_replace(stage, target)
        with patch.object(manager, "replace_snapshot", side_effect=check_backup):
            manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")

    def test_local_plugin_without_git_is_listed_as_local(self):
        repo, installed, _ = self.plugin(snapshot=True)
        self.market_path.write_text('{"sources": []}')
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "local")
        self.assertEqual(self.row(manager)["marketState"], "unlisted")

    def test_link_at_current_remote_commit_becomes_independent_without_changing_developer_files(self):
        repo, developer, installed, current = self.linked_plugin()
        (developer / "Service.qml").write_text("my uncommitted developer code")
        (developer / "settings.json").write_text('{"preference": 3}')
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        row = self.row(manager)
        self.assertTrue(installed.is_symlink())
        self.assertEqual(row["status"], "sync-needed")
        self.assertTrue(row["canUpdate"])
        manager.update(["test.clock"])
        row = self.row(manager)
        self.assertEqual(row["outcome"], "updated")
        self.assertFalse(installed.is_symlink())
        self.assertFalse(row["symlink"])
        self.assertTrue(row["git"])
        self.assertEqual(self.fingerprint(developer), before)
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), current)
        self.assertEqual(self.git(installed, "rev-parse", "origin/main"), current)
        self.assertEqual(self.git(installed, "remote", "get-url", "origin"), repo.as_uri())
        self.assertFalse((installed / ".git/objects/info/alternates").exists())
        self.assertNotEqual((installed / ".git/index").stat().st_ino, (developer / ".git/index").stat().st_ino)
        self.assertEqual((installed / "Service.qml").read_text(), (repo / "Service.qml").read_text())
        self.assertEqual((installed / "settings.json").read_text(), '{"preference": 3}')
        self.assertEqual((Path(row["backup"]) / "Service.qml").read_text(), "my uncommitted developer code")
        metadata = json.loads(Path(row["linkBackup"]).read_text())
        self.assertEqual(metadata["linkTarget"], str(developer))
        (developer / "Service.qml").write_text("a later edit in the developer folder")
        self.assertEqual((installed / "Service.qml").read_text(), (repo / "Service.qml").read_text())
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")

    def test_conversion_installs_published_code_and_keeps_unpushed_developer_commits(self):
        repo, developer, installed, published = self.linked_plugin()
        self.git(developer, "config", "user.email", "test@example.invalid")
        self.git(developer, "config", "user.name", "Test")
        unpublished = self.commit(developer, "import QtQuick\nItem { property int unpublished: 1 }\n")
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "sync-needed")
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), published)
        self.assertEqual(self.git(developer, "rev-parse", "HEAD"), unpublished)
        self.assertEqual(self.fingerprint(developer), before)
        latest = self.commit(repo, "import QtQuick\nItem { property int publishedLater: 2 }\n")
        manager.check()
        self.assertEqual(self.row(manager)["status"], "available")
        manager.update(["test.clock"])
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), latest)
        self.assertEqual(self.fingerprint(developer), before)

    def test_relative_link_conversion_fallback_removes_only_the_link(self):
        repo, developer, installed, current = self.linked_plugin(relative=True)
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        with patch("backend.ctypes.CDLL", return_value=SimpleNamespace()):
            manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertFalse(installed.is_symlink())
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), current)
        self.assertEqual(self.fingerprint(developer), before)
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_failed_link_conversion_restores_the_original_link(self):
        repo, developer, installed, _ = self.linked_plugin()
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        original_replace = os.replace
        def fail_install(source, target):
            source = Path(source)
            if Path(target) == installed and source.name.startswith(".git-sync-") and not source.is_symlink():
                raise OSError("Injected install failure")
            original_replace(source, target)
        with patch("backend.ctypes.CDLL", return_value=SimpleNamespace()), patch("backend.os.replace", side_effect=fail_install):
            manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "failed")
        self.assertTrue(installed.is_symlink())
        self.assertEqual(installed.resolve(), developer)
        self.assertEqual(self.fingerprint(developer), before)
        self.assertFalse(list(self.plugins.glob(".git-sync-*")))

    def test_invalid_upstream_leaves_development_link_and_source_intact(self):
        repo, developer, installed, _ = self.linked_plugin()
        manifest = json.loads((repo / "manifest.json").read_text())
        manifest["entryPoints"] = {"service": "missing.qml"}
        (repo / "manifest.json").write_text(json.dumps(manifest))
        self.commit(repo, "import QtQuick\nItem {}\n")
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "failed")
        self.assertTrue(installed.is_symlink())
        self.assertEqual(self.fingerprint(developer), before)

    def test_link_without_git_metadata_uses_marketplace_repo_for_independent_installation(self):
        repo, developer, installed, current = self.linked_plugin(snapshot=True)
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertTrue((installed / ".git").is_dir())
        self.assertFalse(installed.is_symlink())
        self.assertEqual(self.git(installed, "rev-parse", "HEAD"), current)
        self.assertEqual(self.git(installed, "config", "branch.main.merge"), "refs/heads/main")
        self.assertEqual(self.fingerprint(developer), before)

    def test_link_with_no_remote_source_remains_local(self):
        repo, developer, installed, _ = self.linked_plugin(snapshot=True)
        self.market_path.write_text('{"sources": []}')
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        self.assertEqual(self.row(manager)["status"], "local")
        self.assertFalse(self.row(manager)["canUpdate"])
        self.assertTrue(installed.is_symlink())
        self.assertEqual(self.fingerprint(developer), before)

    def test_linked_suite_plugin_becomes_independent_subtree(self):
        repo, developer, installed, _ = self.linked_plugin(snapshot=True)
        subdir = repo / "widgets/clock"
        subdir.mkdir(parents=True)
        for name in ("manifest.json", "Service.qml"):
            shutil.move(str(repo / name), str(subdir / name))
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "Move to suite")
        self.sources[0]["listingValidatedCommit"] = self.git(repo, "rev-parse", "HEAD")
        self.sources[0]["plugins"]["test.clock"]["manifestPath"] = "widgets/clock/manifest.json"
        self.market_path.write_text(json.dumps({"sources": self.sources}))
        before = self.fingerprint(developer)
        manager = Manager()
        manager.check()
        manager.update(["test.clock"])
        self.assertEqual(self.row(manager)["outcome"], "updated")
        self.assertFalse(installed.is_symlink())
        self.assertTrue((installed / "Service.qml").is_file())
        self.assertFalse((installed / "widgets").exists())
        self.assertEqual(self.fingerprint(developer), before)
        manager.check()
        self.assertEqual(self.row(manager)["status"], "current")


if __name__ == "__main__":
    unittest.main()
