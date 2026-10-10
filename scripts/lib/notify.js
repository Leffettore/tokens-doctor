const { spawnSync } = require('node:child_process');
const { log } = require('./log');

const run = (cmd, args) => spawnSync(cmd, args, { stdio: 'ignore' }).status === 0;

function alert(msg) {
  if (process.platform === 'darwin') return run('afplay', ['/System/Library/Sounds/Glass.aiff']);
  if (process.platform === 'win32') return run('powershell', ['-NoProfile', '-Command', '[console]::beep(880,400)']);
  run('notify-send', ['Claude Code', msg]);
  return run('canberra-gtk-play', ['-i', 'alarm-clock-elapsed'])
    || run('paplay', ['/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'])
    || process.stdout.write('\x07');
}

// Types /compact into the tmux pane Claude Code runs in.
function compact(pane) {
  const ok = run('tmux', ['send-keys', '-t', pane, '/compact']) && run('tmux', ['send-keys', '-t', pane, 'Enter']);
  log(ok ? `sent /compact to tmux pane ${pane}` : `tmux send-keys failed for pane ${pane}`);
}

module.exports = { alert, compact };
