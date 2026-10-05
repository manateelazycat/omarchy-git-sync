#!/usr/bin/env python3
"""Exercise actual retry buttons in a private window with disposable Git repos.

No installed plugins or user Shell are changed. Both Shell reload commands are
stubbed, so an accidental update is detected without restarting the desktop.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

from test_backend import UpdatesTest
from backend import Manager


def main():
    fixture = UpdatesTest()
    fixture.setUp()
    shell = None
    try:
        repo, installed, old = fixture.plugin("test.retry")
        latest = fixture.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n", "Retry upstream update")
        other_repo, _, _ = fixture.plugin("test.other")
        fixture.commit(other_repo, "import QtQuick\nItem { property int revision: 2 }\n", "Other upstream update")
        fixture.plugin("test.current")
        state_env = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(fixture.root / "state-base"),
            "OMARCHY_GIT_SYNC_STATE_DIR": str(fixture.root / "state-base/omarchy-git-sync"),
        })
        state_env.start()
        fixture.addCleanup(state_env.stop)
        missing_repo = (fixture.root / "missing-repo").as_uri()
        fixture.git(installed, "remote", "set-url", "origin", missing_repo)
        manager = Manager()
        manager.check()
        assert fixture.row(manager, "test.retry")["status"] == "error"
        fixture.git(installed, "remote", "set-url", "origin", repo.as_uri())
        other_before = fixture.row(manager, "test.other").copy()
        fixture.commit(other_repo, "import QtQuick\nItem { property int revision: 3 }\n", "Other unqueried update")
        files_before = {p.name: fixture.fingerprint(p) for p in fixture.plugins.iterdir()}

        config = fixture.root / "qml"
        config.mkdir()
        library = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
        for name in ("Commons", "Ui"):
            (config / name).symlink_to(library / name)
        panel_url = (Path(__file__).resolve().parents[1] / "Panel.qml").as_uri()
        qml = '''import QtQuick
import QtTest 1.3
import Quickshell
import Quickshell.Io
ShellRoot {
    id: root
    property int loadCount: 0
    property bool sawChecking: false
    property bool checkingSpinner: false
    TestResult { id: result }
    QtObject { id: host }
    PanelWindow {
        visible: true; implicitWidth: 1; implicitHeight: 1; color: "transparent"
        mask: Region { width: 0; height: 0 }
        screen: Quickshell.screens[0]
        Item { id: anchor; width: 1; height: 1 }
    }
    Loader {
        id: panel
        source: PANEL_URL
        onLoaded: { loadCount++; item.anchorItem = anchor; item.hostWidget = host }
    }
    function findItem(parent, name) {
        if (!parent) return null
        if (parent.objectName === name) return parent
        var children = parent.children || []
        for (var i = 0; i < children.length; i++) {
            var found = findItem(children[i], name)
            if (found) return found
        }
        return null
    }
    function action(name) {
        var window = result.findChild(panel.item, "gitSyncWindow")
        var items = window ? window.contentItem : []
        for (var i = 0; i < items.length; i++) {
            var found = findItem(items[i], name)
            if (found) return found
        }
        return null
    }
    function cardButton() { return action("gitSyncCardAction-test.retry") }
    Timer {
        interval: 10; repeat: true; running: true
        onTriggered: {
            if (!panel.item) return
            var button = root.cardButton()
            if (button && button.text === "检查中") {
                root.sawChecking = true
                if (button.busy) root.checkingSpinner = true
            }
        }
    }
    IpcHandler {
        target: "test.retry"
        function open(): void { panel.item.open() }
        function showDetail(): void { panel.item.detailId = "test.retry" }
        function status(): string {
            var p = panel.item
            var button = p ? root.cardButton() : null
            return JSON.stringify({ready: !!p, busy: !!p && p.sync.busy,
                snapshot: p ? p.sync.snapshot : {}, loads: root.loadCount,
                buttonText: button ? button.text : "", buttonEnabled: !!button && button.enabled,
                sawChecking: root.sawChecking, checkingSpinner: root.checkingSpinner})
        }
        function retryCard(): string {
            var button = root.cardButton()
            if (!button || !button.enabled || button.text !== "重试") return "not ready"
            button.clicked()
            return "ok"
        }
        function retryDetail(): string {
            var button = root.action("gitSyncDetailAction")
            if (!button || !button.enabled || button.text !== "重新检查") return "not ready"
            button.clicked()
            return "ok"
        }
    }
}'''.replace("PANEL_URL", json.dumps(panel_url))
        (config / "shell.qml").write_text(qml)
        binary = fixture.root / "bin"
        binary.mkdir()
        reload_log = fixture.root / "reload.log"
        for name in ("omarchy", "omarchy-shell"):
            stub = binary / name
            stub.write_text('#!/bin/sh\nprintf "%s %s\\n" "$0" "$*" >> "$OMARCHY_GIT_SYNC_RELOAD_LOG"\n')
            stub.chmod(0o755)
        # Keep checks observable long enough to inspect the actual button state.
        git_stub = binary / "git"
        git_stub.write_text('#!/bin/sh\ncase "$*" in *" fetch "*) sleep 0.2;; esac\nexec /usr/bin/git "$@"\n')
        git_stub.chmod(0o755)
        env = dict(os.environ, QT_QPA_PLATFORM="wayland", QS_DISABLE_FILE_WATCHER="1",
                   OMARCHY_GIT_SYNC_RELOAD_LOG=str(reload_log),
                   PATH=str(binary) + os.pathsep + os.environ["PATH"])
        env.pop("OMARCHY_GIT_SYNC_NO_RESCAN", None)
        shell_log = fixture.root / "shell.log"
        with shell_log.open("w") as output:
            shell = subprocess.Popen(["quickshell", "-n", "-p", str(config), "--no-color"],
                                     env=env, stdout=output, stderr=subprocess.STDOUT)

            def ipc(method):
                proc = subprocess.run(["qs", "ipc", "-n", "-p", str(config), "call", "--", "test.retry", method],
                                      env=env, capture_output=True, text=True, timeout=3)
                return proc.stdout.strip() if proc.returncode == 0 else ""

            def status():
                return json.loads(ipc("status") or "{}")

            def row(snapshot, plugin_id="test.retry"):
                return next((p for p in snapshot.get("plugins", []) if p["id"] == plugin_id), {})

            def until(predicate, message):
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    if shell.poll() is not None:
                        raise RuntimeError("Private Shell exited: " + shell_log.read_text())
                    time.sleep(0.02)
                raise RuntimeError(message + "\n" + json.dumps(status(), ensure_ascii=False) + "\n" + shell_log.read_text())

            until(lambda: status().get("ready") and len(status().get("snapshot", {}).get("plugins", [])) == 3
                  and not status().get("busy"), "Model did not load")
            ipc("open")
            until(lambda: status().get("buttonText") == "重试" and status().get("buttonEnabled"), "Retry card did not load")

            def verify_result():
                state = status()
                selected = row(state["snapshot"])
                assert selected["localCommit"] == old, "Retry installed an update"
                assert selected["upstreamCommit"] == latest, "Retry did not refresh the selected card"
                assert state["snapshot"]["progress"] == {"done": 1, "total": 1}, "Retry checked all cards"
                other = row(state["snapshot"], "test.other")
                for key in ("upstreamCommit", "commitMessage", "checkedAt"):
                    assert other[key] == other_before[key], "Retry also checked another plugin"
                for name, fingerprint in files_before.items():
                    assert fixture.fingerprint(fixture.plugins / name) == fingerprint, "Retry changed installed files"
                assert not reload_log.exists() or not reload_log.read_text().strip(), "Retry requested a Shell reload"
                assert state["loads"] == 1 and shell.poll() is None, "Retry recreated the window"
                assert not state["checkingSpinner"], "Checking button displayed a spinner"

            assert ipc("retryCard") == "ok"
            until(lambda: not status().get("busy", True) and row(status().get("snapshot", {})).get("status") == "available",
                  "Card retry did not finish")
            assert status()["sawChecking"], "Checking button was not observed"
            verify_result()

            fixture.git(installed, "remote", "set-url", "origin", missing_repo)
            Manager().check(["test.retry"])
            fixture.git(installed, "remote", "set-url", "origin", repo.as_uri())
            until(lambda: row(status().get("snapshot", {})).get("status") == "error" and not status().get("busy"),
                  "Second failure did not appear")
            ipc("showDetail")
            assert ipc("retryDetail") == "ok"
            until(lambda: not status().get("busy", True) and row(status().get("snapshot", {})).get("status") == "available",
                  "Details retry did not finish")
            verify_result()
        print("RETRY_PASS: card and details only recheck one plugin; installed files unchanged, no Shell reload, no checking spinner")
        return 0
    finally:
        if shell and shell.poll() is None:
            shell.terminate()
            shell.wait(timeout=5)
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
