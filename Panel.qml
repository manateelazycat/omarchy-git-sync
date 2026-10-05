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
    readonly property bool showingDetails: root.detailId !== ""
    property alias sync: sync
    readonly property color foreground: Color.popups.text
    readonly property var detailPlugin: {
        var plugins = sync.snapshot.plugins || []
        for (var i = 0; i < plugins.length; i++) if (plugins[i].id === root.detailId) return plugins[i]
        return null
    }

    function open() {
        windowState.remember(true, windowState.screenName)
        root.controller.show()
        sync.onOpen()
        Qt.callLater(function() { if (root.detailId) keyboard.forceActiveFocus(); else grid.forceActiveFocus() })
    }
    function close() { windowState.remember(false); root.controller.hide() }
    function closeForPopoutSwitch() {}
    function toggle() { root.opened ? root.close() : root.open() }
    function reloadShell() {
        if (sync.busy) return
        if (root.opened) windowState.remember(true)
        Quickshell.execDetached(["omarchy", "restart", "shell"])
    }

    onDetailIdChanged: Qt.callLater(function() {
        if (root.detailId) keyboard.forceActiveFocus()
        else if (root.opened) grid.forceActiveFocus()
    })

    SyncModel {
        id: sync
        active: root.opened
        onBeforeUpdate: if (root.opened) windowState.remember(true, popup.actualScreenName || windowState.screenName)
    }

    WindowState {
        id: windowState
        path: sync.statePath.replace(/state\.json$/, "window.json")
        screenName: root.hostWidget && popup.anchorWindow && popup.anchorWindow.screen ? popup.anchorWindow.screen.name : ""
        windowScreenName: popup.placed && popup.actualScreenName ? popup.actualScreenName : screenName
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
        onPlacementFinished: if (root.opened) windowState.remember(true, popup.actualScreenName)
        onActualScreenNameChanged: if (root.opened && popup.placed && popup.actualScreenName) windowState.remember(true, popup.actualScreenName)

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
                objectName: "gitSyncHeader"
                width: parent.width
                readonly property bool compact: width < Style.space(590)
                height: Style.space(root.showingDetails ? 44 : compact ? 100 : 62)
                Column {
                    objectName: "gitSyncHomeInfo"
                    visible: !root.showingDetails
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
                    objectName: "gitSyncHomeActions"
                    visible: !root.showingDetails
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.topMargin: header.compact ? Style.space(48) : 0
                    spacing: Style.space(8)
                    ActionButton {
                        id: checkButton
                        text: "检查更新"
                        tooltip: "检查更新 · F5"
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
                ActionButton {
                    id: backButton
                    objectName: "gitSyncBackButton"
                    visible: root.showingDetails
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    text: "← 返回"
                    onClicked: root.detailId = ""
                }
                Text {
                    objectName: "gitSyncDetailTitle"
                    visible: root.showingDetails
                    anchors.centerIn: parent
                    width: Math.max(0, parent.width - 2 * (Math.max(backButton.width, detailAction.width) + Style.space(16)))
                    text: root.detailPlugin ? root.detailPlugin.name : "插件详情"
                    textFormat: Text.PlainText
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    color: root.foreground
                    font.family: Style.font.family
                    font.pixelSize: Style.font.heading
                    font.weight: Font.DemiBold
                }
                ActionButton {
                    id: detailAction
                    objectName: "gitSyncDetailAction"
                    visible: root.showingDetails
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    readonly property bool retryable: !!root.detailPlugin && root.detailPlugin.status === "error" && !!root.detailPlugin.repo
                    readonly property bool installing: !!root.detailPlugin && (root.detailPlugin.status === "preparing" || root.detailPlugin.status === "updating")
                    text: root.detailPlugin && root.detailPlugin.status === "checking" ? "检查中"
                        : installing ? (root.detailPlugin.status === "preparing" ? "准备中" : "安装中")
                        : retryable ? "重新检查" : "更新此插件"
                    primary: !retryable
                    busy: installing && root.opened
                    enabled: !sync.busy && root.detailPlugin && (root.detailPlugin.canUpdate || retryable)
                    onClicked: {
                        if (retryable) sync.check(root.detailId)
                        else sync.update(root.detailId)
                    }
                }
            }

            Rectangle {
                id: overallProgress
                anchors.top: header.bottom
                width: parent.width
                visible: !root.showingDetails
                height: visible ? Style.space(2) : 0
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
                objectName: "gitSyncGrid"
                anchors.top: overallProgress.bottom
                anchors.bottom: footer.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: Style.space(16)
                anchors.bottomMargin: Style.space(12)
                visible: !root.showingDetails
                clip: true
                model: sync.model
                readonly property int columns: Math.max(1, Math.min(3, Math.floor(width / Style.space(295))))
                cellWidth: width / columns
                cellHeight: Style.space(224)
                boundsBehavior: Flickable.StopAtBounds
                keyNavigationEnabled: true
                focus: visible
                ScrollBar.vertical: ScrollBar {
                    objectName: "gitSyncGridScrollBar"
                    parent: keyboard
                    anchors.top: grid.top
                    anchors.bottom: grid.bottom
                    anchors.right: parent.right
                    anchors.rightMargin: 3 - popup.contentRightInset
                    horizontalPadding: 0
                    visible: grid.visible
                    policy: ScrollBar.AsNeeded
                }
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
                    onCheckRequested: function(id) { sync.check(id) }
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

            Flickable {
                id: detailViewport
                objectName: "gitSyncDetailViewport"
                anchors.top: overallProgress.bottom
                anchors.bottom: parent.bottom
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: Style.space(12)
                visible: root.showingDetails
                clip: true
                contentWidth: width
                contentHeight: Math.max(height, detailContent.implicitHeight + Style.space(24) * 2)
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {
                    objectName: "gitSyncDetailScrollBar"
                    parent: keyboard
                    anchors.top: detailViewport.top
                    anchors.bottom: detailViewport.bottom
                    anchors.right: parent.right
                    anchors.rightMargin: 3 - popup.contentRightInset
                    horizontalPadding: 0
                    visible: detailViewport.visible
                    policy: ScrollBar.AsNeeded
                }

                Column {
                    id: detailContent
                    objectName: "gitSyncDetailContent"
                    width: Math.min(detailViewport.width - Style.space(32), Style.space(820))
                    x: (detailViewport.width - width) / 2
                    y: Math.max(Style.space(24), (detailViewport.height - implicitHeight) / 2)
                    spacing: Style.space(16)
                    Repeater {
                        model: root.detailPlugin ? [
                            ["简介", root.detailPlugin.description || "暂无简介"],
                            ["当前安装", "v" + root.detailPlugin.version + " · " + (root.detailPlugin.localCommit || "无法识别本地提交")],
                            ["Git 上游", (root.detailPlugin.latestVersion ? "v" + root.detailPlugin.latestVersion + " · " : "") + (root.detailPlugin.upstreamCommit || "尚未检查")],
                            ["最新提交", root.detailPlugin.commitMessage || "暂无"],
                            ["提交时间", root.detailPlugin.commitDate ? new Date(root.detailPlugin.commitDate).toLocaleString(Qt.locale("zh_CN"), "yyyy-MM-dd HH:mm") : "暂无"],
                            ["官方插件市场", (root.detailPlugin.marketVersion ? "v" + root.detailPlugin.marketVersion + " · " : "") + (root.detailPlugin.marketCommit || (root.detailPlugin.marketState === "unlisted" ? "未收录" : "信息暂不可用"))],
                            ["仓库", root.detailPlugin.repo || "本地插件，没有 Git 上游"],
                            ["分支", root.detailPlugin.branch || "暂无"],
                            ["更新方式", root.detailPlugin.symlink ? "转换为独立安装，本地开发目录保留" : "先备份当前插件，再同步 Git 上游代码"],
                            ["更新结果", sync.launchError || root.detailPlugin.error
                                || (root.detailPlugin.status === "checking" ? "正在检查"
                                    : root.detailPlugin.status === "preparing" ? "正在准备"
                                    : root.detailPlugin.status === "prepared" ? "等待安装"
                                    : root.detailPlugin.status === "updating" ? "正在安装"
                                    : root.detailPlugin.outcome === "updated" ? "更新成功" : "—")]
                        ] : []
                        Row {
                            required property var modelData
                            width: detailContent.width
                            spacing: Style.space(28)
                            Text {
                                id: fieldLabel
                                width: Math.min(Style.space(120), parent.width * 0.28)
                                text: modelData[0]
                                horizontalAlignment: Text.AlignRight
                                color: Util.alpha(root.foreground, 0.45)
                                font.family: Style.font.family
                                font.pixelSize: Style.font.body
                            }
                            Column {
                                width: parent.width - fieldLabel.width - parent.spacing
                                spacing: Style.space(6)
                                Text {
                                    width: parent.width
                                    text: modelData[1]
                                    textFormat: Text.PlainText
                                    wrapMode: Text.Wrap
                                    color: root.foreground
                                    font.family: Style.font.family
                                    font.pixelSize: Style.font.body
                                }
                                ActionButton {
                                    text: "打开仓库"
                                    visible: modelData[0] === "仓库" && root.detailPlugin && /^https?:/.test(root.detailPlugin.repo)
                                    onClicked: Qt.openUrlExternally(root.detailPlugin.repo)
                                }
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
                visible: !root.showingDetails
                height: visible ? Style.space(20) : 0
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
