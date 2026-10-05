import QtQuick
import Quickshell
import Quickshell.Io

// Keep an explicitly opened window through plugin reloads in this desktop
// session. A new login or an explicit close must not reopen it automatically.
Item {
    id: root
    visible: false
    property string path: ""
    property string screenName: ""
    property var saved: null
    property bool interacted: false
    property bool restored: false
    readonly property string session: Quickshell.env("HYPRLAND_INSTANCE_SIGNATURE") || Quickshell.env("XDG_SESSION_ID") || ""
    signal restoreRequested()

    function restore() {
        if (interacted || restored || !saved || !screenName || !session) return
        if (saved.open && saved.session === session && saved.screen === screenName) {
            restored = true
            root.restoreRequested()
        }
    }
    function remember(open) {
        interacted = true
        saved = {open: open, session: session, screen: screenName}
        stateFile.setText(JSON.stringify(saved))
    }
    onScreenNameChanged: Qt.callLater(restore)

    FileView {
        id: stateFile
        path: root.path
        printErrors: false
        atomicWrites: true
        onLoaded: {
            try { root.saved = JSON.parse(text()) } catch (error) { root.saved = null }
            Qt.callLater(root.restore)
        }
    }
}
