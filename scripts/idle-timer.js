#!/usr/bin/env node
// Idle timer for Claude Code. One handler per session: its state lives in ~/.claude/idle-timer-<session_id>.json.
//   node idle-timer.js            start/restart the timer (Stop hook, tmux sessions only)
//   node idle-timer.js --reset    cancel the timer (UserPromptSubmit hook)
//   node idle-timer.js --end      cancel the timer and alarm if the context should have been compacted (SessionEnd hook)
//   node idle-timer.js --ms 5000  override the delay (testing)
// Env: CLAUDE_IDLE_MS sets the default delay (default 300000 = 5 min), CLAUDE_WINDOW the context window
// in tokens (default 200000), CLAUDE_IDLE_COMPACT=0 disables the /compact step.
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawn, spawnSync } = require('child_process');

const DIR = path.join(os.homedir(), '.claude');
const args = process.argv.slice(2);
// Hook input (session_id, transcript_path, reason); the detached --wait/--alert workers have no stdin.
let hook = {};
if (!process.stdin.isTTY && !['--wait', '--alert', '--selftest'].some((a) => args.includes(a))) try { hook = JSON.parse(fs.readFileSync(0, 'utf8')); } catch {}

const SID = hook.session_id || (args.includes('--sid') ? args[args.indexOf('--sid') + 1] : '');
const STATE = path.join(DIR, `idle-timer-${SID}.json`); // {pid, pane}: pid of the live timer, tmux pane of the session
const LOG_FILE = path.join(DIR, 'idle.log');
const msArg = args.indexOf('--ms');
const DELAY = Number(msArg >= 0 ? args[msArg + 1] : process.env.CLAUDE_IDLE_MS) || 300000;
const WINDOW = Number(process.env.CLAUDE_WINDOW) || 200000;

const log = (msg) => {
  try { fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${msg}\n`); } catch {}
};

const readState = () => {
  try { return JSON.parse(fs.readFileSync(STATE, 'utf8')); } catch { return {}; }
};
// tmp + rename so a concurrent reader never sees a half-written file.
const writeState = (s) => {
  const tmp = `${STATE}.${process.pid}`;
  fs.writeFileSync(tmp, JSON.stringify(s));
  fs.renameSync(tmp, STATE);
};

// Claude Code doesn't pass TMUX_PANE to hooks and runs them under its background daemon, so the
// hook's own ancestors are unrelated to the user's pane. Plain sessions list their pane in
// ~/.claude/sessions/<pid>.json ("tmux" field); resumed/background ones are shown in a pane that runs
// `claude attach <id8>`, so find that process (Linux /proc) and match it to a tmux pane.
function findPane() {
  try {
    const dir = path.join(DIR, 'sessions');
    for (const f of fs.readdirSync(dir)) {
      if (!f.endsWith('.json')) continue;
      const j = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      if (j.sessionId === SID && j.tmux && fs.existsSync(`/proc/${j.pid}`)) return j.tmux.split('.').pop();
    }
  } catch {}
  try {
    const out = spawnSync('tmux', ['list-panes', '-a', '-F', '#{pane_pid} #{pane_id}'], { encoding: 'utf8' }).stdout;
    const panes = Object.fromEntries(out.trim().split('\n').map((l) => l.split(' ')));
    for (const pid of fs.readdirSync('/proc').filter((p) => /^\d+$/.test(p))) {
      let cmd = '';
      try { cmd = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0').join(' '); } catch { continue; }
      const m = cmd.match(/ attach (\S+)/);
      if (m && SID && SID.startsWith(m[1]) && panes[pid]) return panes[pid];
    }
  } catch {}
  return '';
}

// Only kills a process that is still one of our timers (the pid may have been reused).
function killPrevious() {
  try {
    const { pid } = readState();
    if (pid && fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').includes('idle-timer')) process.kill(pid, 'SIGTERM');
  } catch {}
}

// Session name (set by /rename or auto-titled), read live from ~/.claude/sessions; falls back to the name saved
// in the state file, then to the short session id.
function sessionName() {
  try {
    const dir = path.join(DIR, 'sessions');
    for (const f of fs.readdirSync(dir)) {
      if (!f.endsWith('.json')) continue;
      const j = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
      if (j.sessionId === SID && j.name) return j.name;
    }
  } catch {}
  return readState().name || SID.slice(0, 8) || '?';
}
const label = () => `[${sessionName()}]`;

// Everything decide() needs, from the main-thread transcript. Counters restart at each compact_boundary.
// overhead = context of the first reply (system prompt, tools, skills, memory): the part /compact can't free.
function metrics(file) {
  const m = { tokens: 0, overhead: 0, compactions: 0, msgs: 0, toolHeavy: false };
  let tool = 0, other = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    let e;
    try { e = JSON.parse(line); } catch { continue; }
    if (e.isSidechain) continue;
    if (e.subtype === 'compact_boundary') {
      m.compactions++;
      m.tokens = (e.compactMetadata.postTokens || 0) + m.overhead; // postTokens counts messages only
      m.msgs = tool = other = 0;
      continue;
    }
    const c = e.message && e.message.content;
    const u = e.type === 'assistant' && e.message.usage;
    if (u) {
      m.tokens = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
      m.overhead = m.overhead || m.tokens;
    }
    if (!c || e.isMeta || e.isCompactSummary) continue;
    const size = JSON.stringify(c).length;
    if (e.type === 'user' && Array.isArray(c) && c.some((x) => x.type === 'tool_result')) tool += size;
    else {
      other += size;
      if (e.type === 'user') m.msgs++;
    }
  }
  m.toolHeavy = tool > other;
  return m;
}

// Rules from the "when to compact or clear" guide, for the idle case (user away 5+ min, same task):
// T = 70 - 15 (away) - 10/5 (>50 / 20-50 messages) - 5 (one earlier compaction) - 5 (tool output > 50%), never below 40.
// Compact when messages / (window - overhead) >= T. Not modelled: peak load, task boundary (not readable).
// skip: nothing worth doing; compact: silent /compact; alarm: sound + notification (time to /clear).
function decide(m) {
  const usable = WINDOW - m.overhead;
  const pct = Math.round(((m.tokens - m.overhead) / usable) * 100);
  const T = Math.max(40, 55 - (m.msgs > 50 ? 10 : m.msgs >= 20 ? 5 : 0) - (m.compactions ? 5 : 0) - (m.toolHeavy ? 5 : 0));
  let act = pct >= T ? 'compact' : 'skip';
  if (m.compactions >= 2) act = 'alarm'; // never a third compaction: /clear with a handoff note
  if (m.tokens - m.overhead < 20000) act = 'skip'; // a cold pass over this is cheap
  return { act, pct, T };
}

function alert(msg) {
  const run = (cmd, a) => spawnSync(cmd, a, { stdio: 'ignore' }).status === 0;
  if (process.platform === 'darwin') return run('afplay', ['/System/Library/Sounds/Glass.aiff']);
  if (process.platform === 'win32') {
    return run('powershell', ['-NoProfile', '-Command', '[console]::beep(880,400)']);
  }
  run('notify-send', ['Claude Code', msg]);
  return run('canberra-gtk-play', ['-i', 'alarm-clock-elapsed'])
    || run('paplay', ['/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga'])
    || process.stdout.write('\x07');
}

// Types /compact into the tmux pane Claude Code runs in (TMUX_PANE is set by the hook entry point).
// Skipped when CLAUDE_IDLE_COMPACT=0.
function compact() {
  const pane = process.env.TMUX_PANE;
  if (!pane || process.env.CLAUDE_IDLE_COMPACT === '0') return;
  const ok = spawnSync('tmux', ['send-keys', '-t', pane, '/compact'], { stdio: 'ignore' }).status === 0
    && spawnSync('tmux', ['send-keys', '-t', pane, 'Enter'], { stdio: 'ignore' }).status === 0;
  log(ok ? `sent /compact to tmux pane ${pane}` : `tmux send-keys failed for pane ${pane}`);
}

if (args.includes('--selftest')) {
  const assert = require('assert');
  const m = (tokens, o = {}) => ({ tokens, overhead: 30000, compactions: 0, msgs: 10, toolHeavy: false, ...o });
  assert.strictEqual(decide(m(45000)).act, 'skip'); // 15k of messages
  assert.strictEqual(decide(m(100000)).act, 'skip'); // 70k / 170k = 41% < T 55
  assert.strictEqual(decide(m(125000)).act, 'compact'); // 95k / 170k = 56%
  assert.strictEqual(decide(m(107000, { compactions: 1, toolHeavy: true })).act, 'compact'); // guide's example: 77k = 45% >= T 45
  assert.strictEqual(decide(m(100000, { compactions: 1, toolHeavy: true })).act, 'skip'); // 41% < 45
  assert.strictEqual(decide(m(100000, { compactions: 1, toolHeavy: true, msgs: 60 })).act, 'compact'); // T floors at 40
  assert.strictEqual(decide(m(100000, { compactions: 2 })).act, 'alarm');
  assert.strictEqual(decide(m(45000, { compactions: 3 })).act, 'skip');
  console.log('ok');
  process.exit(0);
}

if (args.includes('--alert')) { // detached so a SessionEnd hook can return before the sound finishes
  alert(args[args.indexOf('--alert') + 1]);
  process.exit(0);
}

if (!SID) { log('no session_id, nothing to do'); process.exit(0); }

if (args.includes('--reset')) {
  killPrevious();
  const { pid, ...rest } = readState();
  if (rest.pane) writeState(rest); // dropping the pid also disowns a timer that survived the kill
  process.exit(0);
}

if (args.includes('--end')) {
  const { pane } = readState();
  const who = label(); // before the state file (and the live sessions entry) disappear
  killPrevious();
  try { fs.unlinkSync(STATE); } catch {}
  // No state file: not a tmux session, or it never finished a turn. /clear and /resume start fresh contexts.
  if (pane && !['clear', 'resume'].includes(hook.reason)) {
    try {
      const d = decide(metrics(hook.transcript_path));
      log(`session ${who} ended: ${d.act} (${d.pct}% messages, T ${d.T}%)`);
      if (d.act !== 'skip') {
        const msg = d.act === 'compact' ? `Session closed with a large context: /compact it when you resume ${who}` : `Session closed after 2+ compactions: /clear next time ${who}`;
        spawn(process.execPath, [__filename, '--alert', msg, '--sid', SID], { detached: true, stdio: 'ignore' }).unref();
      }
    } catch {}
  }
  process.exit(0);
}

if (args.includes('--wait')) {
  // Detached background worker. Only the timer named in the state file may act: a newer Stop hook,
  // a prompt (--reset) or a racing hook replaces the pid, which turns this one into a no-op.
  process.on('SIGTERM', () => process.exit(0));
  setTimeout(() => {
    if (readState().pid !== process.pid) process.exit(0);
    // Decide now, not in the hook: at Stop time the last reply may not be flushed to the transcript yet.
    let act = 'alarm'; // unreadable transcript falls back to the plain alarm
    try {
      const d = decide(metrics(args[args.indexOf('--tp') + 1]));
      act = d.act;
      log(`decision: ${act} (${d.pct}% messages, T ${d.T}%)`);
    } catch {}
    log(`idle ${DELAY}ms elapsed, ${act} ${label()}`);
    if (act === 'compact') compact(); else if (act === 'alarm') alert(`Claude Code idle for ${Math.round(DELAY / 6000) / 10} min, consider /clear ${label()}`);
    process.exit(0);
  }, DELAY);
} else {
  // Hook entry point: replace this session's timer, then detach a new one. Outside tmux there is no timer.
  const pane = process.env.TMUX_PANE || findPane();
  if (!pane) { log(`no tmux pane for session ${SID.slice(0, 8)}, no timer`); process.exit(0); }
  killPrevious();
  const child = spawn(process.execPath, [__filename, '--wait', '--ms', String(DELAY), '--tp', String(hook.transcript_path), '--sid', SID], {
    detached: true,
    stdio: 'ignore',
    env: { ...process.env, TMUX_PANE: pane },
  });
  child.unref();
  writeState({ pid: child.pid, pane, name: sessionName() }); // last writer wins; the losing timer sees a foreign pid and exits
  // State files of crashed sessions (no --end) are never read again: drop the ones untouched for a week.
  try {
    for (const f of fs.readdirSync(DIR)) {
      const p = path.join(DIR, f);
      if (/^idle-timer-.+\.json$/.test(f) && Date.now() - fs.statSync(p).mtimeMs > 7 * 864e5) fs.unlinkSync(p);
    }
  } catch {}
  log(`timer started pid=${child.pid} delay=${DELAY}ms`);
  process.exit(0);
}
