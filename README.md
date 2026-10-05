# Omarchy Git Sync

一个简洁的 Omarchy Shell 插件更新面板。默认显示用户插件，按 Git 更新状态排序，有更新的在前，已最新的在后。检查期间实时显示进度和结果，卡片保持原有顺序，全部检查结束后统一排序。

- 自适应网格卡片：名称、简介、Git 上游最新提交、本地版本及官方市场一致性。
- 单独更新、全部更新、检查更新和全局重载 Shell。
- 检查旋转动画、更新流动进度条、批量进度及卡片移动动画。
- 点击名称或“详情”查看完整提交、时间、版本、仓库地址和错误。
- 更新任务独立运行，关闭面板或 Shell 热重载后仍会继续。

## 安装

需要 Omarchy Quattro 的 Shell 插件接口、Git 和 Python 3.12+，没有额外 Python 依赖。

```bash
git clone https://github.com/manateelazycat/omarchy-git-sync.git
cd omarchy-git-sync
./install.sh
```

安装到 `~/.config/omarchy/plugins/andy.git-sync`，添加到状态栏右侧；已有插件及 Shell 配置会先备份。安装结束后重启一次 Shell，避免插件扫描继续使用缓存中的旧 QML 界面。

点击状态栏的更新图标打开面板，也可运行：

```bash
omarchy-shell shell summon andy.git-sync
```

面板以普通浮动窗口在所在屏幕中央打开。点击外部空白或打开其他托盘菜单不会关闭；窗口获得焦点时按 Omarchy 的 Super+W、再次点击图标或按 Esc 可以关闭（Esc 在详情页先返回列表）。右上角不显示关闭按钮。本次桌面会话中，更新引发的插件重载或 Shell 重启后会在原屏幕恢复已打开的窗口；卸载窗口时不会误记为用户关闭，通过 Super+W 关闭后不会自动恢复。检查和更新进度只在顶部显示，左下角不重复显示任务进行中的提示。

F5 检查更新，Ctrl+U 更新全部；鼠标中键点击图标检查更新。打开面板时，距离上次检查超过五分钟会自动检查。

## 版本与来源

Git 上游决定是否有更新；[官方插件市场目录](https://github.com/omacom/omarchy-plugin-marketplace/blob/main/registry.json)的 `listingValidatedCommit` 决定是否“市场一致”。版本号相同但提交不同仍会显示差异。市场网络失败时使用缓存；Git 更新可独立进行。

检查遇到 Git 缓存中的遗留锁时，会先确认没有 Git 进程正在使用该缓存，再清理锁并继续；仍有 Git 任务时保留锁并提示稍后重试。恢复范围仅限本插件的检查缓存，不会清理安装目录或开发目录的 Git 锁。下载超时会先让 Git 释放锁，再结束未退出的传输和打包子进程，避免留下后台任务。

Git 插件使用自身 remote 和跟踪分支，没有跟踪分支时使用 origin 的默认 HEAD。本地提交落后于上游时显示“有更新”，文件已被替换或修改也可直接更新；提交已最新但文件有差异时显示“待同步”。没有 `.git` 的市场插件使用作者仓库；不能匹配本地提交的安装副本显示“待同步”，可以直接同步到 Git 上游。没有 Git 来源的手写插件保留为“本地插件”。Omarchy 随系统安装的内置插件由系统管理，不在这个面板中更新。

链接到本地开发目录的插件显示“待独立安装”。点击“独立安装”会从对应的远程仓库获取代码，备份当前插件和链接信息，验证后将安装链接替换为独立目录；本地开发目录的代码、提交和未提交改动原样保留。安装副本使用新建的 Git 元数据，不引用开发目录或缓存的对象库；套件中的子目录插件保留独立文件副本及状态中的提交记录。检查不会直接转换插件。此后按“本地开发 → 推送 GitHub → Git Sync 更新安装副本”的流程使用。

点击“更新”或“同步”会先完整备份当前插件，再把上游管理的文件同步到所选 Git 提交，覆盖这些文件的本地改动；Git 安装的 HEAD、跟踪引用和文件会一起更新。新版本与 Git 元数据在临时目录准备、验证后原子替换，原版（包含 `.git` 和本地改动）保存在状态目录的 `backups/`。上游没有管理的额外配置文件会保留；上游新加入的同名文件以 Git 版本为准，原文件留在备份中。尚未推送的本地提交和分叉仍需先处理。批量更新中某个失败不会阻止其他插件。

单个和批量更新均分为“准备”和“集中安装”：先下载、完整备份并验证全部新版本，再替换安装目录。准备失败的插件保持原版，其余继续；准备后安装目录发生变化的插件也会保留原版并提示重试。

本机 Omarchy 的插件监听由独立的 `inotifywait` 进程负责。集中安装期间，通过稳定的进程句柄暂停这个监听；安装和清理结束后，让该监听退出以丢弃积累的文件事件，Omarchy 自带的恢复机制会重新建立监听，再统一重载一次插件。独立的守护进程负责恢复：更新任务中断或超时也不会让监听永久暂停。不会暂停 Shell 进程；未识别到可安全控制的监听时，使用集中安装减少自动重载，结果会说明无法保证仅一次。全部失败或没有实际更新时不触发重载。

更新记录保存在 `update-journal.json`，意外退出后的下一次扫描、检查或更新会识别已安装的版本、恢复未完成的目录替换并清理临时文件。“重载 Shell”按钮仍调用 `omarchy restart shell`，更新期间禁用该按钮。

## 数据与命令

状态和备份：`~/.local/state/omarchy-git-sync/`。Git 缓存和市场目录：`~/.cache/omarchy-git-sync/`。遵循 XDG 环境变量。

```bash
python3 backend.py check
python3 backend.py update <plugin-id>
python3 backend.py update all
python3 backend.py status
python3 -m unittest discover -s tests -v
python3 tests/smoke_qml.py
python3 tests/smoke_batch_reload.py
```

`update all` 同步上一次检查中“有更新”和“待同步”的插件，并在应用前重新检查。检查本身不改动插件文件。进程锁防止多屏、热重载或多次点击启动重复任务。测试用临时本地 Git 仓库，不更新真实插件。

QML 联调测试需要运行中的 Wayland 会话，验证状态列表移动、单个 / 批量更新、详情和重载命令；重载通过测试替身验证，不会重启真实 Shell。

批量重载联调使用独立 Quickshell 和 Omarchy 原生文件监听，验证批量与单个更新各重载一次、失败插件保持原版、监听恢复后仍能检测文件变化；同时验证更新重载和完整 Shell 重启会在原屏幕恢复窗口，窗口管理器主动关闭后不再自动恢复。仓库、插件、进程控制和 IPC 均限定在临时测试环境。

测试和自定义环境可使用 `OMARCHY_GIT_SYNC_PLUGINS_DIR`、`OMARCHY_GIT_SYNC_STATE_DIR`、`OMARCHY_GIT_SYNC_CACHE_DIR`、`OMARCHY_GIT_SYNC_MARKET_URL`；设置 `OMARCHY_GIT_SYNC_NO_RESCAN=1` 可关闭更新后的 Shell 扫描。

## 许可证

Copyright (C) 2026 Andy Stewart.

本项目按 GNU General Public License 第 3 版（`GPL-3.0-only`）发布。完整许可条款见 [LICENSE](LICENSE)。
