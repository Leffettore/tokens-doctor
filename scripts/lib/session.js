const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { DIR } = require('./log');

const SESSIONS = path.join(DIR, 'sessions');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8'));

// Live session entries written by Claude Code in ~/.claude/sessions/<pid>.json.
function liveSession(sid) {
  try {
    for (const f of fs.readdirSync(SESSIONS)) {
      if (!f.endsWith('.json')) continue;
      const j = readJson(path.join(SESSIONS, f));
      if (j.sessionId === sid) return j;
    }
  } catch {}
  return null;
}

// Per-session state in ~/.claude/idle-timer-<sid>.json: {pid, pane, name}.
function session(sid) {
  const file = path.join(DIR, `idle-timer-${sid}.json`);
  const read = () => { try { return readJson(file); } catch { return {}; } };
  return {
    read,
    // tmp + rename so a concurrent reader never sees a half-written file.
    write(s) {
      const tmp = `${file}.${process.pid}`;
      fs.writeFileSync(tmp, JSON.stringify(s));
      fs.renameSync(tmp, file);
    },
    remove() { try { fs.unlinkSync(file); } catch {} },
    // Only kills a process that is still one of our timers (the pid may have been reused).
    killTimer() {
      try {
        const { pid } = read();
        if (pid && fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').includes('idle-timer')) process.kill(pid, 'SIGTERM');
      } catch {}
    },
    // Name set by /rename or auto-titled; falls back to the saved name, then the short id.
    name() {
      const live = liveSession(sid);
      return (live && live.name) || read().name || sid.slice(0, 8) || '?';
    },
  };
}

// Claude Code doesn't pass TMUX_PANE to hooks and runs them under its background daemon, so the hook's own
// ancestors are unrelated to the user's pane. Plain sessions list their pane in the "tmux" field; resumed or
// background ones are shown in a pane running `claude attach <id8>`, found through Linux /proc.
function findPane(sid) {
  const live = liveSession(sid);
  if (live && live.tmux && fs.existsSync(`/proc/${live.pid}`)) return live.tmux.split('.').pop();
  try {
    const out = spawnSync('tmux', ['list-panes', '-a', '-F', '#{pane_pid} #{pane_id}'], { encoding: 'utf8' }).stdout;
    const panes = Object.fromEntries(out.trim().split('\n').map((l) => l.split(' ')));
    for (const pid of fs.readdirSync('/proc').filter((p) => /^\d+$/.test(p))) {
      let cmd;
      try { cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').join(' '); } catch { continue; }
      const m = cmd.match(/ attach (\S+)/);
      if (m && sid.startsWith(m[1]) && panes[pid]) return panes[pid];
    }
  } catch {}
  return '';
}

// State files of crashed sessions (no SessionEnd) are never read again: drop the ones untouched for a week.
function pruneStale() {
  try {
    for (const f of fs.readdirSync(DIR)) {
      const p = path.join(DIR, f);
      if (/^idle-timer-.+\.json$/.test(f) && Date.now() - fs.statSync(p).mtimeMs > 7 * 864e5) fs.unlinkSync(p);
    }
  } catch {}
}

module.exports = { session, findPane, pruneStale };
