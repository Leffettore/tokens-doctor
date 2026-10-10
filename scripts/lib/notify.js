const { spawnSync } = require('node:child_process');

const run = (cmd, args) => spawnSync(cmd, args, { stdio: 'ignore' }).status === 0;

function notify(msg) {
  if (process.platform === 'darwin') return run('osascript', ['-e', `display notification "${msg}" with title "Claude Code" sound name "Glass"`]);
  if (process.platform === 'win32') return run('powershell', ['-NoProfile', '-Command', '[console]::beep(880,400)']);
  run('notify-send', ['Claude Code', msg]);
  return run('canberra-gtk-play', ['-i', 'alarm-clock-elapsed'])
    || run('paplay', ['/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'])
    || process.stdout.write('\x07');
}

module.exports = { notify };
