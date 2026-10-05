import QtQuick
import QtQuick.Shapes

Item {
    id: root
    property color color: "#444444"
    readonly property real iconScale: Math.min(width / 1025, height / 1024)

    // Preserve the supplied SVG; the bar controls optical size and theme color.
    Shape {
        x: (root.width - 1025 * root.iconScale) / 2
        y: (root.height - 1024 * root.iconScale) / 2
        width: 1025
        height: 1024
        preferredRendererType: Shape.CurveRenderer
        transform: Scale {
            xScale: root.iconScale
            yScale: root.iconScale
        }
        ShapePath {
            fillColor: root.color
            fillRule: ShapePath.WindingFill
            strokeWidth: 0
            PathSvg {
                path: "M1004.728 466.4l-447.104-447.072c-25.728-25.76-67.488-25.76-93.28 0l-103.872 103.872 78.176 78.176c12.544-5.984 26.56-9.376 41.376-9.376 53.024 0 96 42.976 96 96 0 14.816-3.36 28.864-9.376 41.376l127.968 127.968c12.544-5.984 26.56-9.376 41.376-9.376 53.024 0 96 42.976 96 96s-42.976 96-96 96-96-42.976-96-96c0-14.816 3.36-28.864 9.376-41.376l-127.968-127.968c-3.04 1.472-6.176 2.752-9.376 3.872l0 266.976c37.28 13.184 64 48.704 64 90.528 0 53.024-42.976 96-96 96s-96-42.976-96-96c0-41.792 26.72-77.344 64-90.528l0-266.976c-37.28-13.184-64-48.704-64-90.528 0-14.816 3.36-28.864 9.376-41.376l-78.176-78.176-295.904 295.872c-25.76 25.792-25.76 67.52 0 93.28l447.136 447.072c25.728 25.76 67.488 25.76 93.28 0l444.992-444.992c25.76-25.76 25.76-67.552 0-93.28z"
            }
        }
    }
}
