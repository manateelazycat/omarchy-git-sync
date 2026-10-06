# Omarchy Git Sync

简体中文 | [English](README.md)

![Omarchy Git Sync 预览](preview.png)

简洁的 Omarchy 插件更新面板，从 Git 上游更新插件，并对照官方插件市场版本。

## 功能

- 网格卡片显示插件名称、简介、最新提交和市场一致性。
- 检查更新、单独更新、全部更新和重启 Shell。
- 动画进度条；检查时保持卡片顺序，全部检查完成后统一排序，有更新的在前。
- 关闭窗口后更新任务继续运行；更新引发重载或重启 Shell 后，窗口回到原来的显示器，切换焦点不影响恢复位置。

## 安装

需要支持自定义 Shell 插件的 Omarchy Quattro、Git 和 Python 3.12+，无需额外 Python 包。

```sh
git clone https://github.com/manateelazycat/omarchy-git-sync.git
cd omarchy-git-sync
./install.sh
```

安装到 `~/.config/omarchy/plugins/andy.git-sync`，图标加入状态栏右侧。安装器会备份已有文件和配置，关闭此窗口的系统焦点边框，并重启一次 Shell。

## 使用

点击状态栏图标打开居中窗口，也可运行：

```sh
omarchy-shell shell summon andy.git-sync
```

- **Super+W**、**Esc** 或再次点击图标关闭窗口。点击外部空白不关闭；详情页按 Esc 先返回列表。
- **F5** 或鼠标中键点击图标检查更新；**Ctrl+U** 更新全部。
- 打开窗口时，距离上次检查超过五分钟会自动检查。
- “重试”只检查当前插件并刷新卡片，不安装文件、不重载 Shell。

## 卸载

```sh
omarchy plugin remove andy.git-sync
```

状态和备份保留在 `~/.local/state/omarchy-git-sync/`。

## 版本与来源

- 是否有更新看 Git 上游；“市场一致”对照[官方插件市场](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/registry.json)的版本。
- Git 记录落后时可更新；文件有改动或无法识别安装版本时可同步。
- 链接到开发目录的插件可改为独立安装，开发目录保持原样。
- 更新前自动备份，文件和 Git 记录一起同步，额外配置保留。Git 管理的文件会覆盖本地改动；未推送的提交或分叉需先处理。
- 系统内置插件、没有 Git 来源的本地插件不在此更新。
- 仓库 URL 不能内嵌访问令牌、密码、HTTP 用户名或查询参数及片段。请使用 SSH 密钥或无需交互提示的 Git credential helper。受影响插件会暂停检查和更新，修改 remote 后即可恢复；Git 地址重写也会经过检查。旧检查缓存和状态中的敏感 URL 会清理，已安装仓库及用户认证配置保持原样。

## 数据与命令

状态和备份：`~/.local/state/omarchy-git-sync/`。Git 和市场缓存：`~/.cache/omarchy-git-sync/`。支持 XDG 目录设置。

```sh
python3 backend.py check
python3 backend.py update <plugin-id>
python3 backend.py update all
python3 backend.py status
```

检查不改动安装文件。批量更新中某个插件失败不影响其他插件，通常在安装完成后统一重载一次插件。

## 开发

```sh
python3 -m unittest discover -s tests -v
python3 tests/smoke_qml.py
python3 tests/smoke_retry.py
python3 tests/smoke_batch_reload.py
```

测试使用临时 Git 仓库和插件。QML 测试需要运行中的 Wayland 会话，使用独立 Shell 实例或模拟重载命令。

## 许可证

Copyright (C) 2026 Andy Stewart.

[GPL-3.0-only](LICENSE)。
