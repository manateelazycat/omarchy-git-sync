#!/usr/bin/env python3
"""Count reloads using Omarchy's actual watcher in a private Quickshell.

All repositories, plugins, IPC calls and watcher signals stay in a disposable
fixture. The user's Shell and installed plugins are not touched.
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
from reload_guard import watcher_pids


def main():
    fixture = UpdatesTest()
    fixture.setUp()
    shell = None
    monitors = json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"]))
    original_focus = next((m["name"] for m in monitors if m.get("focused")), "")

    def focus_monitor(name):
        subprocess.run(["hyprctl", "eval", "hl.dispatch(hl.dsp.focus({monitor=" + json.dumps(name) + "}))"],
                       capture_output=True, check=True, timeout=3)
        focused = next(m["name"] for m in json.loads(subprocess.check_output(["hyprctl", "monitors", "-j"])) if m.get("focused"))
        if focused != name:
            raise RuntimeError("Could not focus monitor " + name)

    try:
        installed = {}
        for plugin_id in ("test.alpha", "test.beta", "test.broken"):
            repo, path, old = fixture.plugin(plugin_id)
            if plugin_id == "test.broken":
                manifest = json.loads((repo / "manifest.json").read_text())
                manifest["entryPoints"]["service"] = "missing.qml"
                (repo / "manifest.json").write_text(json.dumps(manifest))
            latest = fixture.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n")
            installed[plugin_id] = (repo, path, old, latest)
        state_env = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(fixture.root / "state-base"),
            "OMARCHY_GIT_SYNC_STATE_DIR": str(fixture.root / "state-base/omarchy-git-sync"),
        })
        state_env.start()
        fixture.addCleanup(state_env.stop)
        Manager().check()
        config = fixture.root / "qml"
        config.mkdir()
        library = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
        (config / "Commons").symlink_to(library / "Commons")
        (config / "Ui").symlink_to(library / "Ui")
        (config / "Services").symlink_to(library / "services")
        qml = '''import QtQuick
import QtTest 1.3
import Quickshell
import Quickshell.Io
import "Services" as Core
ShellRoot {
    id: root
    property int reloadCount: 0
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
        onLoaded: { item.anchorItem = anchor; item.hostWidget = host }
    }
    function reload(origin) {
        reloadCount++
        console.log("BATCH_RELOAD", origin, reloadCount)
        // Native bar teardown may close its child window before destroying QML.
        var window = panel.item ? result.findChild(panel.item, "gitSyncWindow") : null
        if (window && window.open) window.closed()
        panel.active = false
        Qt.callLater(function() { registry.rescan(); panel.active = true })
    }
    Core.PluginRegistry {
        id: registry
        pluginsDir: PLUGINS_DIR
        firstPartyDir: ""
        shellConfigProvider: function() { return {plugins: []} }
    }
    Timer { id: debounce; interval: 150; onTriggered: root.reload("automatic") }
    Connections {
        target: registry
        function onLocalPluginChanged(id) { debounce.restart() }
    }
    IpcHandler {
        target: "test.batch"
        function ping(): string { return "ok" }
        function count(): string { return String(root.reloadCount) }
        function reload(): string { root.reload("explicit"); return "ok" }
        function open(): void { panel.item.open() }
        function rememberForUpdate(): void { panel.item.sync.beforeUpdate() }
        function panelState(): string {
            var p = panel.item
            var w = p ? result.findChild(p, "gitSyncWindow") : null
            return JSON.stringify({open: !!p && p.opened, mapped: !!w && w.backingWindowVisible,
                busy: !!p && p.sync.busy, screen: w && w.screen ? w.screen.name : ""})
        }
    }
}'''.replace("PLUGINS_DIR", json.dumps(str(fixture.plugins))).replace(
            "PANEL_URL", json.dumps((Path(__file__).resolve().parents[1] / "Panel.qml").as_uri()))
        (config / "shell.qml").write_text(qml)
        binary = fixture.root / "bin"
        binary.mkdir()
        stub = binary / "omarchy-shell"
        stub.write_text('#!/bin/sh\nexec qs ipc -n -p "$OMARCHY_GIT_SYNC_BATCH_TEST_QML" call -- test.batch reload\n')
        stub.chmod(0o755)
        env = dict(os.environ, QS_DISABLE_FILE_WATCHER="1", QT_QPA_PLATFORM="wayland",
                   OMARCHY_GIT_SYNC_BATCH_TEST_QML=str(config),
                   PATH=str(binary) + os.pathsep + os.environ["PATH"])
        env.pop("OMARCHY_GIT_SYNC_NO_RESCAN", None)
        log = fixture.root / "shell.log"
        with log.open("w") as output, patch.dict(os.environ, env):
            os.environ.pop("OMARCHY_GIT_SYNC_NO_RESCAN", None)
            shell = subprocess.Popen(["quickshell", "-n", "-p", str(config), "--no-color"],
                                     env=env, stdout=output, stderr=subprocess.STDOUT)
            def ipc(method):
                result = subprocess.run(["qs", "ipc", "-n", "-p", str(config), "call", "--", "test.batch", method],
                                        capture_output=True, text=True, env=env, timeout=3)
                return result.stdout.strip() if result.returncode == 0 else ""
            def until(predicate, message, timeout=4):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    if shell.poll() is not None:
                        raise RuntimeError("Private Shell exited: " + log.read_text())
                    time.sleep(0.03)
                raise RuntimeError(message + "\n" + log.read_text())
            until(lambda: ipc("ping") == "ok" and watcher_pids(fixture.plugins), "Watcher did not start")
            def panel_state():
                return json.loads(ipc("panelState") or "{}")
            until(lambda: "open" in panel_state() and not panel_state()["busy"], "Panel did not load")
            original_screen = panel_state()["screen"]
            focus_monitor(original_screen)
            ipc("open")
            until(lambda: panel_state().get("mapped"), "Panel did not open")

            def native_window():
                clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
                return next((w for w in clients if w.get("pid") == shell.pid and w.get("title") == "Git Sync"), None)

            def restored():
                state = panel_state()
                window = native_window()
                monitor = next((m for m in monitors if window and m["id"] == window.get("monitor")), None)
                if not state.get("open") or not state.get("mapped") or not monitor or monitor["name"] != original_screen:
                    return False
                width, height = monitor["width"] / monitor["scale"], monitor["height"] / monitor["scale"]
                if monitor["transform"] % 2:
                    width, height = height, width
                expected = [monitor["x"] + (width - window["size"][0]) / 2,
                            monitor["y"] + (height - window["size"][1]) / 2]
                return window["floating"] and all(abs(a - b) <= 1 for a, b in zip(window["at"], expected))

            until(restored, "Initial panel opened on the wrong monitor")
            window_state = Path(os.environ["OMARCHY_GIT_SYNC_STATE_DIR"]) / "window.json"
            other_screen = next((m["name"] for m in monitors if m["name"] != original_screen), "")
            if other_screen:
                def move_panel(screen):
                    selector = json.dumps("address:" + native_window()["address"])
                    code = "local target=hl.get_active_workspace(" + json.dumps(screen) + "); " \
                        + "hl.dispatch(hl.dsp.window.move({workspace=target,window=" + selector + ",follow=false})); " \
                        + "hl.dispatch(hl.dsp.window.center({window=" + selector + "}))"
                    subprocess.run(["hyprctl", "eval", code], capture_output=True, check=True, timeout=3)
                move_panel(other_screen)
                until(lambda: json.loads(window_state.read_text())["screen"] == other_screen,
                      "Moving the window did not save its actual monitor")
                ipc("rememberForUpdate")
                if json.loads(window_state.read_text())["screen"] != other_screen:
                    raise RuntimeError("Update saved the bar monitor instead of the actual window monitor")
                move_panel(original_screen)
                until(lambda: restored() and json.loads(window_state.read_text())["screen"] == original_screen,
                      "Original monitor was not saved after moving back")
            before = watcher_pids(fixture.plugins)
            manager = Manager()
            manager.update(["all"])
            if manager.state["reloadMode"] != "controlled" or manager.state["reloadError"]:
                raise RuntimeError("Batch was not controlled: " + json.dumps(manager.state, ensure_ascii=False))
            until(lambda: watcher_pids(fixture.plugins) and watcher_pids(fixture.plugins) != before,
                  "Omarchy did not restore its watcher")
            time.sleep(0.25)
            if ipc("count") != "1":
                raise RuntimeError("Batch did not reload exactly once: " + log.read_text())
            until(restored, "Batch update did not restore the open panel")
            if not json.loads(window_state.read_text())["open"]:
                raise RuntimeError("Teardown incorrectly saved a user close")
            for plugin_id, (repo, path, old, latest) in installed.items():
                expected = old if plugin_id == "test.broken" else latest
                if fixture.git(path, "rev-parse", "HEAD") != expected:
                    raise RuntimeError("Unexpected installed commit for " + plugin_id)
            # A regular file edit must still trigger the native listener.
            alpha, path, _, _ = installed["test.alpha"]
            (path / "Service.qml").write_text("external edit after the batch")
            until(lambda: ipc("count") == "2", "Restored watcher did not detect a file edit")
            fixture.commit(alpha, "import QtQuick\nItem { property int revision: 3 }\n")
            manager.check(["test.alpha"])
            manager.update(["test.alpha"])
            until(lambda: watcher_pids(fixture.plugins), "Watcher did not return after a single update")
            time.sleep(0.25)
            if ipc("count") != "3":
                raise RuntimeError("Single update did not reload exactly once: " + log.read_text())
            until(restored, "Single update did not restore the open panel")
            # Move focus away before both QML reload and a whole Shell restart.
            if other_screen:
                focus_monitor(other_screen)
                ipc("reload")
                until(restored, "Plugin reload followed the focused monitor")
            shell.terminate()
            shell.wait(timeout=5)
            if other_screen:
                focus_monitor(other_screen)
            shell = subprocess.Popen(["quickshell", "-n", "-p", str(config), "--no-color"],
                                     env=env, stdout=output, stderr=subprocess.STDOUT)
            until(restored, "Shell restart did not restore the open panel")
            clients = json.loads(subprocess.check_output(["hyprctl", "clients", "-j"]))
            own_window = next(w for w in clients if w.get("pid") == shell.pid and w.get("title") == "Git Sync")
            selector = json.dumps("address:" + own_window["address"])
            subprocess.run(["hyprctl", "eval", "hl.dispatch(hl.dsp.window.close({window=" + selector + "}))"],
                           capture_output=True, check=True, timeout=3)
            until(lambda: not panel_state().get("open") and not panel_state().get("mapped"),
                  "Window manager close did not hide the panel")
            if json.loads(window_state.read_text())["open"]:
                raise RuntimeError("Explicit close was not saved")
            ipc("reload")
            until(lambda: "open" in panel_state(), "Panel did not reload after closing")
            time.sleep(0.25)
            if panel_state()["open"] or panel_state()["mapped"]:
                raise RuntimeError("Reload reopened an explicitly closed panel")
            print("BATCH_RELOAD_PASS: batch=1, single=1, partial failure preserved, watcher restored; "
                  "actual monitor and center restored after switching focus; moved window saved before update; explicit close stays closed")
        return 0
    finally:
        if shell and shell.poll() is None:
            shell.terminate()
            shell.wait(timeout=5)
        if original_focus:
            focus_monitor(original_focus)
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
