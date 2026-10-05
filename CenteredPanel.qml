import QtQuick
import QtQuick.Window
import Quickshell
import Quickshell.Hyprland
import Quickshell.Io
import qs.Commons
import qs.Ui

// Fixed-size toplevels float and open in the monitor center in Omarchy. The
// window manager owns focus and the normal Super+W close action.
FloatingWindow {
    id: root
    objectName: "gitSyncWindow"
    property Item anchorItem: null
    property bool open: false
    property Item focusTarget: null
    property int margin: Style.gapsOut
    property int padding: Style.spacing.popupPadding
    property int contentWidth: Style.space(1000)
    property int contentHeight: Style.space(660)
    property var borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
    property bool placed: false
    property bool placementRequested: false
    property int placementAttempts: 0
    property string placementToken: Math.random().toString(16).slice(2)
    property string targetScreenName: ""
    property var nativeWindow: null
    readonly property string placementTitle: "Git Sync [" + placementToken + "]"
    readonly property string actualScreenName: nativeWindow && nativeWindow.monitor ? nativeWindow.monitor.name : ""
    readonly property var anchorWindow: anchorItem ? anchorItem.QsWindow.window : null
    readonly property real contentRightInset: card.contentRightInset
    readonly property real verticalContentInset: padding * 2 + Border.top(borderSpec) + Border.bottom(borderSpec)
    default property alias contentItem: contentHolder.children
    signal closeRequested()
    signal placementFinished()

    function fittedContentWidth(desired) {
        return Math.round(Math.min(desired, Math.max(1, (screen ? screen.width : desired) - margin * 2)))
    }
    function fittedContentHeight(desired) {
        return Math.round(Math.min(desired + verticalContentInset,
            Math.max(1, (screen ? screen.height : desired + verticalContentInset) - margin * 2)))
    }
    function activate() {
        if (!open || !backingWindowVisible || !placed) return
        Qt.callLater(function() {
            if (!root.open) return
            if (card.Window.window) card.Window.window.requestActivate()
            if (root.focusTarget) root.focusTarget.forceActiveFocus()
        })
    }

    function placeWindow() {
        if (!root.open || !root.backingWindowVisible || root.placed) return
        if (++root.placementAttempts > 40) {
            console.warn("Git Sync window placement timed out for " + root.targetScreenName)
            root.placed = true
            root.activate()
            return
        }
        if (!root.nativeWindow) {
            var windows = Hyprland.toplevels.values
            for (var i = 0; i < windows.length; i++) {
                if (windows[i].title === root.placementTitle && windows[i].lastIpcObject.pid === Quickshell.processId) {
                    root.nativeWindow = windows[i]
                    break
                }
            }
            if (!root.nativeWindow) { Hyprland.refreshToplevels(); return }
        }
        if (placementProcess.running) return
        if (root.placementRequested) {
            if (root.actualScreenName !== root.targetScreenName) { Hyprland.refreshToplevels(); return }
            root.placed = true
            root.placementFinished()
            root.activate()
            return
        }
        var address = root.nativeWindow.address
        if (!address) return
        var selector = JSON.stringify("address:" + (address.indexOf("0x") === 0 ? address : "0x" + address))
        var monitor = JSON.stringify(root.targetScreenName)
        // A Wayland toplevel's requested screen is only a hint. Place this exact
        // native window on the saved output's visible workspace, then center it.
        placementProcess.command = ["hyprctl", "eval",
            "local target = hl.get_active_workspace(" + monitor + "); "
            + "if target then hl.dispatch(hl.dsp.window.move({workspace=target, window=" + selector + ", follow=false})); "
            + "hl.dispatch(hl.dsp.window.center({window=" + selector + "})) end"]
        root.placementRequested = true
        placementProcess.running = true
    }

    screen: anchorWindow ? anchorWindow.screen : (Quickshell.screens[0] || null)
    title: root.placed ? "Git Sync" : root.placementTitle
    visible: open
    color: "transparent"
    implicitWidth: contentWidth
    implicitHeight: contentHeight
    minimumSize: Qt.size(contentWidth, contentHeight)
    maximumSize: minimumSize
    onBackingWindowVisibleChanged: if (backingWindowVisible) Qt.callLater(root.placeWindow)
    onOpenChanged: {
        if (open) {
            targetScreenName = anchorWindow && anchorWindow.screen ? anchorWindow.screen.name : screen ? screen.name : ""
            placementToken = Math.random().toString(16).slice(2)
            placed = false
            placementRequested = false
            placementAttempts = 0
            nativeWindow = null
            Qt.callLater(root.placeWindow)
        }
    }
    // Unloading the bar can close its native child windows before QML destroys
    // the panel. Defer the close action so teardown never records a user close.
    onClosed: closeTimer.start()
    Component.onDestruction: closeTimer.stop()

    Timer {
        interval: 75
        repeat: true
        running: root.open && root.backingWindowVisible && !root.placed
        onTriggered: root.placeWindow()
    }
    Process {
        id: placementProcess
        onExited: function(exitCode) {
            if (exitCode !== 0) console.warn("Git Sync could not place its window on " + root.targetScreenName)
            Hyprland.refreshToplevels()
            Qt.callLater(root.placeWindow)
        }
    }

    Timer {
        id: closeTimer
        interval: 0
        onTriggered: root.closeRequested()
    }

    BorderSurface {
        id: card
        objectName: "gitSyncCard"
        anchors.fill: parent
        color: Color.popups.background
        borderSpec: root.borderSpec
        padding: root.padding
        radius: Style.cornerRadius

        Item {
            id: contentHolder
            anchors.fill: parent
            anchors.topMargin: card.contentTopInset
            anchors.rightMargin: card.contentRightInset
            anchors.bottomMargin: card.contentBottomInset
            anchors.leftMargin: card.contentLeftInset
        }
    }
}
