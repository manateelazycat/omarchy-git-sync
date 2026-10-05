import QtQuick
import QtQuick.Controls
import Quickshell
import qs.Commons
import qs.Ui
import qs.Ui as ShellUi

ShellUi.Panel {
    id: root
    moduleName: "andy.git-sync"
    ipcTarget: "andy.git-sync"
    manageIpc: false
    property var anchorItem: null
    property var hostWidget: null
    property string detailId: ""
    property alias sync: sync
    readonly property color foreground: Color.popups.text
    readonly property var detailPlugin: {
        var plugins = sync.snapshot.plugins || []
        for (var i = 0; i < plugins.length; i++) if (plugins[i].id === root.detailId) return plugins[i]
        return null
    }

    function open() {
        windowState.remember(true)
        root.controller.show()
        sync.onOpen()
        Qt.callLater(function() { if (root.detailId) keyboard.forceActiveFocus(); else grid.forceActiveFocus() })
    }
    function close() { windowState.remember(false); root.controller.hide() }
    function closeForPopoutSwitch() {}
    function toggle() { root.opened ? root.close() : root.open() }
    function reloadShell() {
        if (sync.busy) return
        Quickshell.execDetached(["omarchy", "restart", "shell"])
    }

    onDetailIdChanged: Qt.callLater(function() {
        if (root.detailId) keyboard.forceActiveFocus()
        else if (root.opened) grid.forceActiveFocus()
    })

    SyncModel { id: sync; active: root.opened }

    WindowState {
        id: windowState
        path: sync.statePath.replace(/state\.json$/, "window.json")
        screenName: root.hostWidget && popup.anchorWindow && popup.screen ? popup.screen.name : ""
        onRestoreRequested: root.open()
    }

    CenteredPanel {
        id: popup
        anchorItem: root.anchorItem
        open: root.opened
        focusTarget: keyboard
        padding: Style.space(20)
        contentWidth: fittedContentWidth(Style.space(1000))
        contentHeight: fittedContentHeight(Style.space(660))
        onCloseRequested: root.close()

        Item {
            id: keyboard
            anchors.fill: parent
            focus: true
            Keys.onPressed: function(event) {
                if (event.key === Qt.Key_Escape) {
                    if (root.detailId) root.detailId = ""
                    else root.close()
                    event.accepted = true
                } else if (event.key === Qt.Key_F5) {
                    sync.check(); event.accepted = true
                } else if (event.modifiers & Qt.ControlModifier && event.key === Qt.Key_U) {
                    sync.updateAll(); event.accepted = true
                } else if (!root.detailId && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && !event.isAutoRepeat) {
                    if (!checkButton.activeFocus && !allButton.activeFocus && !reloadButton.activeFocus && grid.currentItem)
                        root.detailId = grid.currentItem.plugin.id
                }
            }

            Item {
                id: header
                width: parent.width
                readonly property bool compact: width < Style.space(590)
                height: Style.space(compact ? 100 : 62)
                Column {
                    width: header.compact ? parent.width : parent.width - actions.width - Style.space(12)
                    spacing: Style.space(5)
                    Text {
                        text: "Git Sync"
                        color: root.foreground
                        font.family: Style.font.family
                        font.pixelSize: Style.font.heading
                        font.weight: Font.DemiBold
                    }
                    Text {
                        width: parent.width
                        text: sync.snapshot.busy ? (sync.snapshot.action === "update" ? "正在更新" : "正在检查")
                            + "  " + sync.snapshot.progress.done + " / " + sync.snapshot.progress.total
                            : sync.updates ? sync.updates + " 个插件可更新 · 共 " + sync.model.count + " 个"
                            : "共 " + sync.model.count + " 个插件"
                        color: Util.alpha(root.foreground, 0.5)
                        font.family: Style.font.family
                        font.pixelSize: Style.font.caption
                        elide: Text.ElideRight
                    }
                }
                Row {
                    id: actions
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.topMargin: header.compact ? Style.space(48) : 0
                    spacing: Style.space(8)
                    ActionButton {
                        id: checkButton
                        text: "检查更新"
                        tooltip: "检查更新 · F5"
                        busy: sync.busy && sync.snapshot.action !== "update" && root.opened
                        enabled: !sync.busy
                        onClicked: sync.check()
                    }
                    ActionButton {
                        id: allButton
                        text: sync.snapshot.busy && sync.snapshot.action === "update" ? "正在更新" : "全部更新"
                        primary: true
                        enabled: !sync.busy && sync.updates > 0
                        tooltip: "同步所有可更新的插件 · 自动备份 · Ctrl+U"
                        onClicked: sync.updateAll()
                    }
                    ActionButton {
                        id: reloadButton
                        text: "重载 Shell"
                        tooltip: "重新启动整个 Omarchy Shell"
                        enabled: !sync.busy
                        onClicked: root.reloadShell()
                    }
                }
            }

            Rectangle {
                id: overallProgress
                anchors.top: header.bottom
                width: parent.width
                height: Style.space(2)
                color: Util.alpha(root.foreground, 0.07)
                Rectangle {
                    height: parent.height
                    width: sync.snapshot.busy && sync.snapshot.progress.total > 0
                        ? parent.width * sync.snapshot.progress.done / sync.snapshot.progress.total : 0
                    color: Color.accent
                    Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                }
            }

            GridView {
                id: grid
                anchors.top: overallProgress.bottom
                anchors.bottom: footer.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: Style.space(16)
                anchors.bottomMargin: Style.space(12)
                visible: root.detailId === ""
                clip: true
                model: sync.model
                readonly property int columns: Math.max(1, Math.min(3, Math.floor(width / Style.space(295))))
                cellWidth: width / columns
                cellHeight: Style.space(224)
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationEnabled: true
                focus: visible
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: PluginCard {
                    required property int index
                    required property var entry
                    plugin: entry
                    width: grid.cellWidth - Style.space(10)
                    height: grid.cellHeight - Style.space(10)
                    globalBusy: sync.busy
                    animationsActive: root.opened
                    selected: GridView.isCurrentItem && grid.activeFocus
                    onDetailsRequested: function(id) { root.detailId = id }
                    onUpdateRequested: function(id) { sync.update(id) }
                }
                add: Transition { NumberAnimation { properties: "opacity"; from: 0; to: 1; duration: 180 } }
                move: Transition { NumberAnimation { properties: "x,y"; duration: 280; easing.type: Easing.OutCubic } }
                moveDisplaced: Transition { NumberAnimation { properties: "x,y"; duration: 280; easing.type: Easing.OutCubic } }

                Text {
                    anchors.centerIn: parent
                    visible: sync.model.count === 0
                    text: sync.busy ? "正在读取插件…" : "还没有安装用户插件"
                    color: Util.alpha(root.foreground, 0.5)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.body
                }
            }

            Item {
                anchors.top: overallProgress.bottom
                anchors.bottom: footer.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: Style.space(16)
                visible: root.detailPlugin !== null
                ActionButton { id: backButton; text: "← 返回"; onClicked: root.detailId = "" }
                Flickable {
                    anchors.top: backButton.bottom
                    anchors.topMargin: Style.space(18)
                    anchors.bottom: parent.bottom
                    width: parent.width
                    clip: true
                    contentHeight: detailContent.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    Column {
                        id: detailContent
                        width: parent.width - Style.space(12)
                        spacing: Style.space(12)
                        Text {
                            width: parent.width
                            text: root.detailPlugin ? root.detailPlugin.name : ""
                            textFormat: Text.PlainText
                            color: root.foreground
                            font.family: Style.font.family
                            font.pixelSize: Style.font.heading
                            wrapMode: Text.Wrap
                        }
                        Text {
                            width: parent.width
                            text: root.detailPlugin ? root.detailPlugin.description : ""
                            textFormat: Text.PlainText
                            color: Util.alpha(root.foreground, 0.65)
                            font.family: Style.font.family
                            font.pixelSize: Style.font.body
                            wrapMode: Text.Wrap
                        }
                        Repeater {
                            model: root.detailPlugin ? [
                                ["当前安装", "v" + root.detailPlugin.version + " · " + (root.detailPlugin.localCommit || "无法识别本地提交")],
                                ["Git 上游", (root.detailPlugin.latestVersion ? "v" + root.detailPlugin.latestVersion + " · " : "") + (root.detailPlugin.upstreamCommit || "尚未检查")],
                                ["最新提交", root.detailPlugin.commitMessage || "暂无"],
                                ["提交时间", root.detailPlugin.commitDate ? new Date(root.detailPlugin.commitDate).toLocaleString(Qt.locale("zh_CN"), "yyyy-MM-dd HH:mm") : "暂无"],
                                ["官方插件市场", (root.detailPlugin.marketVersion ? "v" + root.detailPlugin.marketVersion + " · " : "") + (root.detailPlugin.marketCommit || (root.detailPlugin.marketState === "unlisted" ? "未收录" : "信息暂不可用"))],
                                ["仓库", root.detailPlugin.repo || "本地插件，没有 Git 上游"],
                                ["分支", root.detailPlugin.branch || "暂无"],
                                ["更新方式", root.detailPlugin.symlink ? "转换为独立安装，本地开发目录保留" : "先备份当前插件，再同步 Git 上游代码"],
                                ["更新结果", root.detailPlugin.error || (root.detailPlugin.outcome === "updated" ? "更新成功" : "—")]
                            ] : []
                            Column {
                                required property var modelData
                                width: parent.width
                                spacing: Style.space(4)
                                Text { text: modelData[0]; color: Util.alpha(root.foreground, 0.4); font.family: Style.font.family; font.pixelSize: Style.font.caption }
                                Text {
                                    width: parent.width
                                    text: modelData[1]
                                    textFormat: Text.PlainText
                                    wrapMode: Text.WrapAnywhere
                                    color: root.foreground
                                    font.family: Style.font.family
                                    font.pixelSize: Style.font.body
                                }
                            }
                        }
                        Row {
                            spacing: Style.space(8)
                            ActionButton {
                                text: "更新此插件"
                                primary: true
                                enabled: !sync.busy && root.detailPlugin && (root.detailPlugin.canUpdate || (root.detailPlugin.status === "error" && root.detailPlugin.repo))
                                onClicked: sync.update(root.detailId)
                            }
                            ActionButton {
                                text: "打开仓库"
                                visible: root.detailPlugin && /^https?:/.test(root.detailPlugin.repo)
                                onClicked: Qt.openUrlExternally(root.detailPlugin.repo)
                            }
                        }
                    }
                }
            }

            Text {
                id: footer
                objectName: "gitSyncFooter"
                anchors.bottom: parent.bottom
                width: parent.width
                height: Style.space(20)
                text: sync.launchError || (sync.busy ? "" :
                    sync.snapshot.message || (sync.snapshot.marketCached ? "市场暂不可用，正在使用缓存" : sync.snapshot.marketError ? "市场信息暂不可用 · Git 更新仍可使用" : "同步 Git 上游 · 自动备份本地文件 · 官方市场作对照"))
                textFormat: Text.PlainText
                color: sync.launchError ? Color.urgent : Util.alpha(root.foreground, 0.4)
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                elide: Text.ElideRight
            }
        }
    }
}
