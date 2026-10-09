#!/usr/bin/env bash
# Installs idle-timer.js into ~/.claude and registers its three hooks in ~/.claude/settings.json.
#   ./install.sh              install (safe to re-run; backs up settings.json first)
#   ./install.sh --uninstall  remove the hooks and the script
set -euo pipefail
command -v node >/dev/null || { echo "node is required" >&2; exit 1; }
command -v tmux >/dev/null || echo "warning: tmux not found, the timer only runs inside tmux" >&2

DIR="${CLAUDE_HOME:-$HOME/.claude}"
mkdir -p "$DIR"
SETTINGS="$DIR/settings.json"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp -n "$SETTINGS" "$SETTINGS.bak" || true # keep the first backup, the original

MODE=install; [ "${1:-}" = "--uninstall" ] && MODE=uninstall
if [ "$MODE" = install ]; then cp "$(dirname "$0")/idle-timer.js" "$DIR/idle-timer.js"; else rm -f "$DIR/idle-timer.js"; fi

MODE=$MODE SETTINGS=$SETTINGS node -e '
const fs = require("fs");
const { MODE, SETTINGS } = process.env;
const s = JSON.parse(fs.readFileSync(SETTINGS, "utf8"));
s.hooks = s.hooks || {};
const want = { Stop: "", UserPromptSubmit: " --reset", SessionEnd: " --end" };
for (const [event, flag] of Object.entries(want)) {
  const cmd = `node ~/.claude/idle-timer.js${flag}`;
  // drop any earlier idle-timer entry so re-running never duplicates it
  s.hooks[event] = (s.hooks[event] || [])
    .map((g) => ({ ...g, hooks: g.hooks.filter((h) => !h.command.includes("idle-timer.js")) }))
    .filter((g) => g.hooks.length);
  if (MODE === "install") s.hooks[event].push({ hooks: [{ type: "command", command: cmd }] });
  if (!s.hooks[event].length) delete s.hooks[event];
}
fs.writeFileSync(SETTINGS, JSON.stringify(s, null, 2) + "\n");
'
echo "$MODE done. Previous settings saved as $SETTINGS.bak. Restart running Claude Code sessions to pick up the hooks."
