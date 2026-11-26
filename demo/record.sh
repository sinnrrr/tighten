#!/bin/sh
set -e
here=$(cd "$(dirname "$0")" && pwd)
proj=${DEMO_DIR:-/tmp/ledger-demo}
"$here/project.sh" "$proj"
cd "$proj"
DEMO_TRUST=1 uv run --quiet "$here/drive.py" >/dev/null
asciinema rec --headless --overwrite --window-size 100x34 --idle-time-limit 2 \
  --command "uv run --quiet $here/drive.py" "$here/demo.cast"
root=$(cd "$here/.." && pwd)
jq -c --arg root "$root" --arg home "$HOME" 'if type == "object" then del(.command, .env) else (.[2] |= (gsub("\u00a0"; " ") | gsub($root; "${CLAUDE_PLUGIN_ROOT}") | gsub($home; "~"))) end' "$here/demo.cast" >"$here/demo.cast.tmp" && mv "$here/demo.cast.tmp" "$here/demo.cast"
agg -q --font-family "JetBrainsMono Nerd Font Mono,JetBrainsMono Nerd Font,Menlo" --font-size 16 --theme github-dark --speed 1.5 --idle-time-limit 2 --last-frame-duration 4 "$here/demo.cast" "$here/demo.gif"
cd /
rm -rf "$proj"
