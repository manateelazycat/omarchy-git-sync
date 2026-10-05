import QtQuick
import Quickshell
import Quickshell.Io

Item {
    id: root
    visible: false
    property bool active: false
    property var snapshot: ({plugins: [], busy: false, progress: {done: 0, total: 0}})
    property bool pending: false
    property string launchError: ""
    property int revision: -1
    property var signatures: ({})
    property alias model: rows
    readonly property bool busy: pending || snapshot.busy === true
    readonly property int updates: (snapshot.plugins || []).filter(function(p) { return p.canUpdate === true }).length
    readonly property string statePath: (Quickshell.env("XDG_STATE_HOME") || (Quickshell.env("HOME") + "/.local/state")) + "/omarchy-git-sync/state.json"
    readonly property string backendPath: decodeURIComponent(String(Qt.resolvedUrl("backend.py")).replace(/^file:\/\//, ""))

    function load(text) {
        try {
            var next = JSON.parse(text)
            if (next.revision === root.revision) return
            root.revision = next.revision
            root.snapshot = next
            root.pending = false
            pendingTimer.stop()
            var plugins = next.plugins || []
            var wanted = {}
            var nextSignatures = {}
            for (var i = 0; i < plugins.length; i++) wanted[plugins[i].id] = true
            for (var j = rows.count - 1; j >= 0; j--)
                if (!wanted[rows.get(j).entry.id]) rows.remove(j)
            for (var k = 0; k < plugins.length; k++) {
                var plugin = plugins[k]
                var found = -1
                for (var n = k; n < rows.count; n++) {
                    if (rows.get(n).entry.id === plugin.id) { found = n; break }
                }
                if (found < 0) rows.insert(k, {entry: plugin})
                else if (found !== k) rows.move(found, k, 1)
                var signature = JSON.stringify(plugin)
                if (root.signatures[plugin.id] !== signature) rows.set(k, {entry: plugin})
                nextSignatures[plugin.id] = signature
            }
            root.signatures = nextSignatures
        } catch (error) {
            root.launchError = "无法读取插件状态：" + error
        }
    }

    function launch(action, ids) {
        if (root.busy) return
        root.launchError = ""
        root.pending = true
        Quickshell.execDetached(["python3", root.backendPath, action].concat(ids || []))
        pendingTimer.restart()
    }

    function check() { root.launch("check", []) }
    function update(id) { root.launch("update", [id]) }
    function updateAll() { if (root.updates > 0) root.launch("update", ["all"]) }
    function onOpen() {
        stateFile.reload()
        autoCheckTimer.restart()
    }

    ListModel { id: rows; dynamicRoles: true }

    FileView {
        id: stateFile
        path: root.statePath
        watchChanges: true
        printErrors: false
        onLoaded: root.load(text())
        onFileChanged: reload()
    }

    Timer {
        id: autoCheckTimer
        interval: 600
        onTriggered: if (root.active && !root.busy && Date.now() / 1000 - (root.snapshot.checkedAt || 0) > 300) root.check()
    }

    Timer {
        interval: 600
        running: root.active || root.busy
        repeat: true
        onTriggered: stateFile.reload()
    }

    Timer {
        id: pendingTimer
        interval: 3500
        onTriggered: {
            root.pending = false
            stateFile.reload()
            if (!root.snapshot.busy) root.launchError = "任务未启动，请重试"
        }
    }

    Component.onCompleted: {
        // A detached worker holds the cross-process lock; rescans are harmless
        // while another monitor or a previous QML instance is updating.
        Quickshell.execDetached(["python3", root.backendPath, "scan"])
    }
}
