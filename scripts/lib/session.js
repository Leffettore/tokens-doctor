const fs = require('node:fs');
const path = require('node:path');
const { DIR } = require('./log');

// One state file per session, ~/.claude/idle-timer-<sid>.json, holding the pid of the live timer.
// shortcut: files of crashed sessions (no SessionEnd) are never pruned, they are a few bytes each.
function session(sid) {
  const file = path.join(DIR, `idle-timer-${sid}.json`);
  const pid = () => { try { return JSON.parse(fs.readFileSync(file, 'utf8')).pid; } catch { return 0; } };
  return {
    pid,
    // tmp + rename so a concurrent reader never sees a half-written file.
    setPid(p) {
      const tmp = `${file}.${process.pid}`;
      fs.writeFileSync(tmp, JSON.stringify({ pid: p }));
      fs.renameSync(tmp, file);
    },
    // Only kills a process that is still one of our timers (the pid may have been reused).
    stop() {
      try {
        const p = pid();
        if (p && fs.readFileSync(`/proc/${p}/cmdline`, 'utf8').includes('idle-timer')) process.kill(p, 'SIGTERM');
      } catch {}
      try { fs.unlinkSync(file); } catch {}
    },
  };
}

module.exports = { session };
