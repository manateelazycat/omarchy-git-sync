#!/usr/bin/env bash
set -euo pipefail

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
plugin_id=andy.git-sync
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}/omarchy
target_dir=$config_dir/plugins/$plugin_id
state_dir=${XDG_STATE_HOME:-$HOME/.local/state}/omarchy-git-sync
hypr_config=${XDG_CONFIG_HOME:-$HOME/.config}/hypr/hyprland.lua

for dependency in python3 git omarchy hyprctl; do
  command -v "$dependency" >/dev/null || { echo "缺少依赖：$dependency" >&2; exit 1; }
done
[[ -f $hypr_config ]] || { echo "缺少 Hyprland Lua 配置：$hypr_config" >&2; exit 1; }
omarchy plugin validate "$source_dir"
mkdir -p "$config_dir/plugins" "$state_dir/backups"
stamp=$(date +%Y%m%d-%H%M%S-%N)
if [[ -f $config_dir/shell.json ]]; then
  cp -a "$config_dir/shell.json" "$state_dir/backups/shell-$stamp.json"
fi
if [[ -e $target_dir ]]; then
  cp -a "$target_dir" "$state_dir/backups/$plugin_id-$stamp"
fi
stage_dir=$(mktemp -d "$config_dir/plugins/.git-sync-install.XXXXXX")
trap 'rm -rf -- "$stage_dir"' EXIT
for file in "$source_dir"/*.qml "$source_dir/manifest.json" "$source_dir/backend.py" "$source_dir/reload_guard.py" "$source_dir/hyprland.lua" "$source_dir"/README*.md "$source_dir/LICENSE"; do
  cp -- "$file" "$stage_dir/"
done
omarchy plugin validate "$stage_dir"
if [[ -e $target_dir ]]; then
  mv -- "$target_dir" "$stage_dir.old"
fi
if ! mv -- "$stage_dir" "$target_dir"; then
  [[ ! -e $stage_dir.old ]] || mv -- "$stage_dir.old" "$target_dir"
  exit 1
fi
[[ ! -e $stage_dir.old ]] || rm -rf -- "$stage_dir.old"
# Keep the window rule with the plugin so later Git updates carry it as well.
python3 - "$hypr_config" "$state_dir/backups" <<'PY'
from datetime import datetime
from pathlib import Path
import shutil
import sys

config = Path(sys.argv[1])
marker = '-- >>> omarchy-git-sync >>>'
text = config.read_text()
if marker not in text:
    backup = Path(sys.argv[2])
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config, backup / ('hyprland-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.lua'))
    config.write_text(text.rstrip() + '''

-- >>> omarchy-git-sync >>>
do
  local path = (os.getenv("XDG_CONFIG_HOME") or (os.getenv("HOME") .. "/.config"))
    .. "/omarchy/plugins/andy.git-sync/hyprland.lua"
  local file = io.open(path, "r")
  if file then file:close(); dofile(path) end
end
-- <<< omarchy-git-sync <<<
''')
PY
hyprctl reload >/dev/null
config_errors=$(hyprctl configerrors)
if [[ -n ${config_errors//[[:space:]]/} ]]; then
  printf '加载 Git Sync 窗口规则后 Hyprland 配置出错：\n%s\n' "$config_errors" >&2
  exit 1
fi
python3 "$target_dir/backend.py" scan
omarchy plugin enable "$plugin_id" --section right
# A plugin rescan can retain compiled QML for the same source URL. Restart
# once after installation so the desktop uses the newly installed interface.
omarchy restart shell
echo "Git Sync 已安装。点击状态栏更新图标，或运行：omarchy-shell shell summon $plugin_id"
