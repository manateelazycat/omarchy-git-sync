import QtQuick
import qs.Commons
import qs.Ui
import qs.Ui as ShellUi

ShellUi.BarWidget {
    id: root
    moduleName: "andy.git-sync"
    readonly property bool opened: panelLoader.item ? panelLoader.item.opened : false
    readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing : false

    function injectPanel() {
        if (!panelLoader.item) return
        panelLoader.item.bar = root.bar
        panelLoader.item.settings = root.settings
        panelLoader.item.anchorItem = button
        panelLoader.item.hostWidget = root
    }
    function open() { if (panelLoader.item) panelLoader.item.open() }
    function close() { if (panelLoader.item) panelLoader.item.close() }
    function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
    function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }
    function refresh() { if (panelLoader.item) panelLoader.item.sync.check() }

    implicitWidth: button.implicitWidth
    implicitHeight: button.implicitHeight
    onBarChanged: injectPanel()
    onSettingsChanged: injectPanel()

    Loader {
        id: panelLoader
        source: Qt.resolvedUrl("Panel.qml")
        visible: false
        onLoaded: { root.injectPanel(); Qt.callLater(root.injectPanel) }
    }

    BarIconButton {
        id: button
        anchors.fill: parent
        bar: root.bar
        iconComponent: Component {
            GitIcon {
                color: button.active && button.useActiveColor ? button.activeColor : button.foreground
            }
        }
        active: panelLoader.item && (panelLoader.item.sync.busy || panelLoader.item.sync.updates > 0)
        tooltipText: "Git Sync · 插件更新" + (panelLoader.item && panelLoader.item.sync.updates > 0 ? "\n" + panelLoader.item.sync.updates + " 个插件可更新" : "")
        onPressed: function(b) {
            if (b === Qt.MiddleButton) root.refresh()
            else root.togglePanel()
        }
    }
}
