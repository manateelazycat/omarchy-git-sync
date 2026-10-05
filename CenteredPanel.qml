import QtQuick
import QtQuick.Window
import Quickshell
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
    readonly property var anchorWindow: anchorItem ? anchorItem.QsWindow.window : null
    readonly property real verticalContentInset: padding * 2 + Border.top(borderSpec) + Border.bottom(borderSpec)
    default property alias contentItem: contentHolder.children
    signal closeRequested()

    function fittedContentWidth(desired) {
        return Math.round(Math.min(desired, Math.max(1, (screen ? screen.width : desired) - margin * 2)))
    }
    function fittedContentHeight(desired) {
        return Math.round(Math.min(desired + verticalContentInset,
            Math.max(1, (screen ? screen.height : desired + verticalContentInset) - margin * 2)))
    }
    function activate() {
        if (!open || !backingWindowVisible) return
        Qt.callLater(function() {
            if (!root.open) return
            if (card.Window.window) card.Window.window.requestActivate()
            if (root.focusTarget) root.focusTarget.forceActiveFocus()
        })
    }

    screen: anchorWindow ? anchorWindow.screen : (Quickshell.screens[0] || null)
    title: "Git Sync"
    visible: open
    color: "transparent"
    implicitWidth: contentWidth
    implicitHeight: contentHeight
    minimumSize: Qt.size(contentWidth, contentHeight)
    maximumSize: minimumSize
    onBackingWindowVisibleChanged: activate()
    onOpenChanged: activate()
    // Unloading the bar can close its native child windows before QML destroys
    // the panel. Defer the close action so teardown never records a user close.
    onClosed: closeTimer.start()
    Component.onDestruction: closeTimer.stop()

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
