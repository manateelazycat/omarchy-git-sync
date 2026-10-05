import QtQuick
import qs.Commons

Rectangle {
    id: root
    required property var plugin
    property bool globalBusy: false
    property bool animationsActive: true
    property bool selected: false
    property bool successFlash: false
    signal checkRequested(string pluginId)
    signal updateRequested(string pluginId)
    signal detailsRequested(string pluginId)

    readonly property color foreground: Color.popups.text
    readonly property bool working: plugin.status === "checking" || plugin.status === "updating" || plugin.status === "preparing"
    readonly property bool retryable: plugin.status === "error" && plugin.repo !== ""
    readonly property string statusLabel: {
        switch (plugin.status) {
        case "available": return "有更新"
        case "sync-needed": return plugin.symlink ? "待独立安装" : "待同步"
        case "current": return root.successFlash ? "✓ 已更新" : "已最新"
        case "checking": return "检查中"
        case "updating": return "更新中"
        case "preparing": return "准备中"
        case "prepared": return "等待安装"
        case "modified": return "本地修改"
        case "ahead": return "本地领先"
        case "diverged": return "分支分叉"
        case "error": return "失败"
        case "local": return "本地插件"
        default: return "待检查"
        }
    }
    readonly property color statusColor: plugin.status === "error" ? Color.urgent
        : plugin.canUpdate || working || root.successFlash ? Color.accent
        : Util.alpha(foreground, 0.55)

    color: Util.alpha(foreground, 0.025)
    radius: Math.max(Style.cornerRadius, Style.space(8))
    border.width: 1
    border.color: selected ? Util.alpha(Color.accent, 0.7)
        : root.successFlash ? Util.alpha(Color.accent, 0.35) : Util.alpha(foreground, 0.1)
    clip: true
    Behavior on border.color { ColorAnimation { duration: 220 } }

    onPluginChanged: Qt.callLater(function() {
        root.successFlash = root.plugin && root.plugin.outcome === "updated"
        if (root.successFlash) successTimer.restart()
    })
    Timer { id: successTimer; interval: 1500; onTriggered: root.successFlash = false }
    Rectangle {
        anchors.fill: parent
        radius: parent.radius
        color: Color.accent
        opacity: root.successFlash && root.animationsActive ? 0.08 : 0
        Behavior on opacity { NumberAnimation { duration: 260 } }
    }

    Column {
        anchors.fill: parent
        anchors.margins: Style.space(16)
        spacing: Style.space(8)

        Item {
            width: parent.width
            height: Style.space(24)
            Text {
                width: parent.width - status.implicitWidth - Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: root.plugin.name
                textFormat: Text.PlainText
                color: root.foreground
                font.family: Style.font.family
                font.pixelSize: Style.font.body
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Text {
                id: status
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.statusLabel
                textFormat: Text.PlainText
                color: root.statusColor
                font.family: Style.font.family
                font.pixelSize: Style.font.caption
                Behavior on color { ColorAnimation { duration: 180 } }
            }
            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.detailsRequested(root.plugin.id)
            }
        }

        Text {
            width: parent.width
            height: Style.space(35)
            text: root.plugin.description || "暂无简介"
            textFormat: Text.PlainText
            color: Util.alpha(root.foreground, 0.65)
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            wrapMode: Text.WordWrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }

        Text {
            width: parent.width
            height: Style.space(20)
            text: root.plugin.commitMessage || (root.working ? "正在读取 Git 上游…" : root.plugin.repo ? "尚未读取最新提交" : "没有 Git 上游")
            textFormat: Text.PlainText
            color: root.foreground
            font.family: Style.font.family
            font.pixelSize: Style.font.caption
            elide: Text.ElideRight
        }

        Column {
            width: parent.width
            spacing: Style.space(3)
            Repeater {
                model: [
                    "上游版本：" + (root.plugin.latestVersion || (root.plugin.repo ? "未知" : "无上游")),
                    "本机版本：" + (root.plugin.version || "未知"),
                    "商店版本：" + (root.plugin.marketVersion || (root.plugin.marketState === "unlisted" ? "未收录" : "未知"))
                ]
                Text {
                    required property string modelData
                    width: parent.width
                    text: modelData
                    textFormat: Text.PlainText
                    color: Util.alpha(root.foreground, 0.4)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    maximumLineCount: 1
                    elide: Text.ElideRight
                }
            }
        }

        Item {
            width: parent.width
            height: Math.max(Style.space(34), footerInfo.implicitHeight, updateButton.implicitHeight)
            Column {
                id: footerInfo
                width: parent.width - updateButton.width - Style.space(10)
                anchors.verticalCenter: parent.verticalCenter
                spacing: Style.space(3)
                Text {
                    width: parent.width
                    text: root.plugin.marketState === "same" ? "市场一致"
                        : root.plugin.marketState === "different" ? "与市场不同"
                        : root.plugin.marketState === "version-same" ? "市场版本号一致"
                        : root.plugin.marketState === "unlisted" ? "市场未收录" : "市场版本待确认"
                    color: root.plugin.marketState === "different" ? Util.alpha(Color.accent, 0.8) : Util.alpha(root.foreground, 0.55)
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    elide: Text.ElideRight
                }
                Text {
                    width: parent.width
                    visible: text !== ""
                    text: (root.plugin.error || "").replace(/\s+/g, " ").trim()
                    textFormat: Text.PlainText
                    color: Color.urgent
                    font.family: Style.font.family
                    font.pixelSize: Style.font.caption
                    maximumLineCount: 1
                    elide: Text.ElideRight
                }
            }
            MouseArea {
                width: parent.width - updateButton.width - Style.space(10)
                height: parent.height
                cursorShape: Qt.PointingHandCursor
                onClicked: root.detailsRequested(root.plugin.id)
            }
            ActionButton {
                id: updateButton
                objectName: "gitSyncCardAction-" + root.plugin.id
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: root.plugin.status === "updating" ? "安装中" : root.plugin.status === "preparing" ? "准备中"
                    : root.plugin.status === "prepared" ? "等待安装" : root.plugin.status === "checking" ? "检查中"
                    : root.retryable ? "重试" : root.plugin.canUpdate ? (root.plugin.symlink ? "独立安装" : root.plugin.status === "sync-needed" ? "同步" : "更新") : "详情"
                primary: root.plugin.canUpdate || root.plugin.status === "updating"
                busy: root.working && root.plugin.status !== "checking" && root.animationsActive
                enabled: !root.working && (!root.globalBusy || (!root.plugin.canUpdate && !root.retryable))
                tooltip: root.retryable ? "重新检查此插件" : ""
                onClicked: {
                    if (root.retryable) root.checkRequested(root.plugin.id)
                    else if (root.plugin.canUpdate) root.updateRequested(root.plugin.id)
                    else root.detailsRequested(root.plugin.id)
                }
            }
        }
    }

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: Style.space(2)
        color: Util.alpha(Color.accent, 0.1)
        visible: root.working
        clip: true
        Rectangle {
            id: pulse
            width: parent.width * 0.32
            height: parent.height
            color: Color.accent
            NumberAnimation on x {
                from: -pulse.width; to: root.width
                duration: 1100; loops: Animation.Infinite
                running: root.working && root.animationsActive
            }
        }
    }
}
