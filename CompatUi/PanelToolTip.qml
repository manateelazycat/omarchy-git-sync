// SPDX-License-Identifier: MIT
// Copyright (c) David Heinemeier Hansson
// Adapted from Omarchy 4.0.4; see OMARCHY-LICENSE.
// Explicit theme imports avoid Qt 6.12 Color name collisions.
import qs.Ui as ShellUi
import "." as LocalUi
import QtQuick
import QtQuick.Controls
import qs.Commons as Commons

// Styled wrapper around Qt Quick Controls ToolTip. Drop-in: declare inside
// the hovered item and bind `visible` to the hover state, e.g.
//   PanelToolTip {
//     visible: mouse.containsMouse
//     text: "Forget network"
//   }
//
// Defaults pull from [tooltip] in shell.toml via Commons.Color.tooltip.*. Override
// the panel* properties per-instance only when you need a tooltip that
// intentionally diverges from the theme.
//
// Property names are prefixed `panel*` to avoid clashing with ToolTip's
// built-in `background`/`font` properties.
ToolTip {
  id: root

  property color panelForeground: Commons.Color.tooltip.text
  property color panelBackground: Commons.Color.tooltip.background
  property color panelBorder: Commons.Color.tooltip.border
  property string fontFamily: Commons.Style.font.family
  property real fontSize: Commons.Style.font.bodySmall

  readonly property var panelBorderSpec: LocalUi.ThemeBorder.localOrSurfaceSpec("tooltip", "border", panelBorder, Commons.Color.tooltip.border, Commons.Style.normalBorderWidth)

  delay: 400
  padding: 0

  background: ShellUi.BorderSurface {
    color: root.panelBackground
    borderSpec: root.panelBorderSpec
    radius: Commons.Style.cornerRadius
  }

  contentItem: Text {
    textFormat: Text.PlainText
    text: root.text
    color: root.panelForeground
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
    leftPadding: LocalUi.ThemeBorder.left(root.panelBorderSpec) + Commons.Style.spacing.controlPaddingX
    rightPadding: LocalUi.ThemeBorder.right(root.panelBorderSpec) + Commons.Style.spacing.controlPaddingX
    topPadding: LocalUi.ThemeBorder.top(root.panelBorderSpec) + Commons.Style.spacing.controlPaddingY
    bottomPadding: LocalUi.ThemeBorder.bottom(root.panelBorderSpec) + Commons.Style.spacing.controlPaddingY
  }
}
