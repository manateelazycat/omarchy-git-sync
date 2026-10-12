import "CompatUi" as ThemeUi
import QtQuick
import qs.Commons as Commons
import qs.Ui

Rectangle {
    id: root
    property string text: ""
    property string tooltip: ""
    property bool primary: false
    property bool busy: false
    property color foreground: Commons.Color.popups.text
    signal clicked()

    implicitWidth: label.implicitWidth + Commons.Style.space(busy ? 44 : 24)
    implicitHeight: Commons.Style.space(32)
    radius: Math.max(Commons.Style.cornerRadius, Commons.Style.space(6))
    activeFocusOnTab: enabled
    opacity: enabled || busy ? 1 : 0.4
    color: primary ? Commons.Util.alpha(Commons.Color.accent, mouse.containsMouse ? 0.25 : 0.16)
                   : Commons.Util.alpha(foreground, mouse.pressed ? 0.14 : mouse.containsMouse || activeFocus ? 0.09 : 0.04)
    border.width: activeFocus ? 1 : 0
    border.color: Commons.Color.accent
    Behavior on color { ColorAnimation { duration: 120 } }
    Behavior on opacity { NumberAnimation { duration: 120 } }

    Row {
        anchors.centerIn: parent
        spacing: Commons.Style.space(7)
        Spinner { visible: root.busy; anchors.verticalCenter: parent.verticalCenter; color: root.primary ? Commons.Color.accent : root.foreground }
        Text {
            id: label
            text: root.text
            textFormat: Text.PlainText
            color: root.primary ? Commons.Color.accent : root.foreground
            font.family: Commons.Style.font.family
            font.pixelSize: Commons.Style.font.body
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: root.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (root.enabled && !root.busy) root.clicked()
    }
    Keys.onReturnPressed: if (enabled && !busy) clicked()
    Keys.onEnterPressed: if (enabled && !busy) clicked()
    Keys.onSpacePressed: if (enabled && !busy) clicked()
    ThemeUi.PanelToolTip { visible: mouse.containsMouse && root.tooltip !== ""; text: root.tooltip }
}
