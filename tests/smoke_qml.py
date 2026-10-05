#!/usr/bin/env python3
"""Exercise the real QML model against disposable Git repositories.

Requires a running Wayland session with Omarchy's QML libraries. It does not
open a panel or restart the user's Shell; the reload command uses a stub.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from test_backend import UpdatesTest
from backend import Manager


def main():
    fixture = UpdatesTest()
    fixture.setUp()
    try:
        for name in ("test.alpha", "test.beta"):
            repo, installed, _ = fixture.plugin(name)
            fixture.commit(repo, "import QtQuick\nItem { property int revision: 2 }\n", "Animation fixture update")
            (installed / "Service.qml").write_text("old Git record with locally replaced code")
        _, partial, _ = fixture.plugin("test.partial", snapshot=True)
        (partial / "Service.qml").write_text("snapshot without an identifiable upstream commit")
        _, developer, linked, _ = fixture.linked_plugin()
        (developer / "Service.qml").write_text("uncommitted developer code stays local")
        developer_before = fixture.fingerprint(developer)
        fixture.plugin("test.current")
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(fixture.root / "state-base")
        env["OMARCHY_GIT_SYNC_STATE_DIR"] = str(Path(env["XDG_STATE_HOME"]) / "omarchy-git-sync")
        env["QT_QPA_PLATFORM"] = "wayland"
        os.environ["OMARCHY_GIT_SYNC_STATE_DIR"] = env["OMARCHY_GIT_SYNC_STATE_DIR"]
        Manager().check()
        library = Path(os.environ.get("OMARCHY_PATH", "/usr/share/omarchy")) / "shell"
        config = fixture.root / "qml"
        config.mkdir()
        for name in ("Commons", "Ui"):
            (config / name).symlink_to(library / name)
        binary_dir = fixture.root / "bin"
        binary_dir.mkdir()
        log = fixture.root / "reload.log"
        stub = binary_dir / "omarchy"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$OMARCHY_GIT_SYNC_RELOAD_LOG"\n')
        stub.chmod(0o755)
        env["OMARCHY_GIT_SYNC_RELOAD_LOG"] = str(log)
        env["PATH"] = str(binary_dir) + os.pathsep + env["PATH"]
        panel_url = (Path(__file__).resolve().parents[1] / "Panel.qml").as_uri()
        qml = r'''import QtQuick
import Quickshell
ShellRoot {
    property int phase: 0
    property int ticks: 0
    property bool failed: false
    function require(ok, message) {
        if (!ok) { console.error("QML_SMOKE_FAIL " + message); failed = true; Qt.quit() }
        return ok
    }
    function row(id) {
        var rows = panel.item.sync.snapshot.plugins || []
        return rows.filter(function(p) { return p.id === id })[0]
    }
    Loader { id: panel; source: PANEL_URL }
    Timer {
        interval: 100; repeat: true; running: true
        onTriggered: {
            ticks++
            if (ticks > 180) { require(false, "timeout phase " + phase); return }
            if (!panel.item || failed || panel.item.sync.busy) return
            var sync = panel.item.sync
            if (phase === 0 && sync.model.count === 5 && ticks > 8) {
                if (!require(sync.updates === 4, "update and sync count")) return
                if (!require(row("test.alpha").status === "available", "dirty Git installation remains updatable")) return
                if (!require(row("test.partial").status === "sync-needed", "unidentified snapshot can be synced")) return
                if (!require(row("test.clock").symlink && row("test.clock").canUpdate, "development link offers independent installation")) return
                sync.check(); phase = 1
            } else if (phase === 1) {
                if (!require(row("test.alpha").marketState === "different", "modified files differ from market")) return
                sync.update("test.alpha")
                panel.item.reloadShell() // Must do nothing while a worker is pending.
                phase = 2
            } else if (phase === 2) {
                if (!require(row("test.alpha").outcome === "updated", "single update")) return
                if (!require(row("test.alpha").marketState === "different", "market comparison")) return
                if (!require(sync.model.get(0).entry.id === "test.beta", "model move")) return
                sync.updateAll(); phase = 3
            } else if (phase === 3) {
                if (!require(sync.updates === 0, "bulk update")) return
                if (!require(row("test.beta").outcome === "updated", "bulk result")) return
                if (!require(row("test.partial").outcome === "updated", "bulk sync result")) return
                if (!require(row("test.clock").outcome === "updated" && !row("test.clock").symlink, "bulk link conversion result")) return
                panel.item.detailId = "test.beta"
                if (!require(panel.item.detailPlugin.commitMessage === "Animation fixture update", "details")) return
                console.log("QML_SMOKE_BEFORE_RELOAD")
                phase = 4
            } else if (phase === 4) {
                panel.item.reloadShell()
                phase = 5
            } else if (phase === 5) {
                console.log("QML_SMOKE_PASS")
                Qt.quit()
            }
        }
    }
}'''.replace("PANEL_URL", json.dumps(panel_url))
        (config / "shell.qml").write_text(qml)
        proc = subprocess.run(["quickshell", "-p", str(config), "--no-color"], env=env,
                              capture_output=True, text=True, timeout=25)
        output = proc.stdout + proc.stderr
        errors = [line for line in output.splitlines() if "QML_SMOKE" in line or "WARN scene" in line or "ERROR" in line]
        print("\n".join(errors))
        if proc.returncode or "QML_SMOKE_PASS" not in output or "QML_SMOKE_FAIL" in output or "WARN scene" in output:
            print(output, file=sys.stderr)
            return 1
        if not log.exists() or log.read_text().strip() != "restart shell":
            print("Reload command was not dispatched", file=sys.stderr)
            return 1
        if linked.is_symlink() or fixture.fingerprint(developer) != developer_before:
            print("Development link conversion was not independent", file=sys.stderr)
            return 1
        print("QML model, single / bulk updates, details and reload dispatch passed.")
        return 0
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    sys.exit(main())
