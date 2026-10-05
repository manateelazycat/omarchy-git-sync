import QtQuick
import qs.Commons

Item {
    id: root
    property color color: Color.accent
    property bool running: true
    implicitWidth: Style.space(16)
    implicitHeight: implicitWidth
    onColorChanged: ring.requestPaint()

    Canvas {
        id: ring
        anchors.fill: parent
        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            ctx.strokeStyle = root.color
            ctx.lineWidth = Math.max(1.6, width / 9)
            ctx.lineCap = "round"
            ctx.beginPath()
            ctx.arc(width / 2, height / 2, width / 2 - ctx.lineWidth, 0, Math.PI * 1.5)
            ctx.stroke()
        }
    }
    RotationAnimator on rotation {
        from: 0; to: 360; duration: 900
        loops: Animation.Infinite
        running: root.running && root.visible
    }
}
