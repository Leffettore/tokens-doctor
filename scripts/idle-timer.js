#!/usr/bin/env node
// Idle timer for Claude Code, one detached timer per session. Modes (first argument):
//   (none)    Stop hook: start/restart the timer (tmux sessions only)
//   --reset   UserPromptSubmit hook: cancel the timer
//   --end     SessionEnd hook: cancel the timer, alarm if the context should have been compacted
//   --wait    internal: the detached timer
//   --alert   internal: detached alarm, so a hook can return before the sound finishes
// Options: --ms <n> delay override, --sid <id>, --tp <transcript>.
// Env: CLAUDE_IDLE_MS delay (default 300000), CLAUDE_WINDOW context tokens (default 200000),
// CLAUDE_IDLE_COMPACT=0 never types /compact.
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { log } = require('./lib/log');
const { metrics, decide } = require('./lib/decide');
const { session, findPane, pruneStale } = require('./lib/session');
const { alert, compact } = require('./lib/notify');

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const mode = ['--reset', '--end', '--wait', '--alert'].find((f) => args.includes(f)) || 'start';

const DELAY = Number(opt('--ms') || process.env.CLAUDE_IDLE_MS) || 300000;
const WINDOW = Number(process.env.CLAUDE_WINDOW) || 200000;

if (mode === '--alert') { alert(opt('--alert')); process.exit(0); }

// Hook input (session_id, transcript_path, reason); the detached workers have no stdin.
let hook = {};
if (mode !== '--wait') try { hook = JSON.parse(fs.readFileSync(0, 'utf8')); } catch {}
const sid = hook.session_id || opt('--sid');
if (!sid) { log('no session_id, nothing to do'); process.exit(0); }

const s = session(sid);
const label = () => `[${s.name()}]`;
const detach = (extra, env = process.env) =>
  spawn(process.execPath, [__filename, ...extra], { detached: true, stdio: 'ignore', env });

if (mode === '--reset') {
  s.killTimer();
  const { pid, ...rest } = s.read();
  if (rest.pane) s.write(rest); // dropping the pid also disowns a timer that survived the kill
} else if (mode === '--end') {
  const { pane } = s.read();
  const who = label(); // before the state file and the live session entry disappear
  s.killTimer();
  s.remove();
  // No state file: not a tmux session, or it never finished a turn. /clear and /resume start fresh contexts.
  if (pane && !['clear', 'resume'].includes(hook.reason)) {
    try {
      const d = decide(metrics(hook.transcript_path), WINDOW);
      log(`session ${who} ended: ${d.act} (${d.pct}% messages, T ${d.T}%)`);
      if (d.act !== 'skip') {
        detach(['--alert', d.act === 'compact'
          ? `Session closed with a large context: /compact it when you resume ${who}`
          : `Session closed after 2+ compactions: /clear next time ${who}`]).unref();
      }
    } catch {}
  }
} else if (mode === '--wait') {
  // Only the timer named in the state file may act: a newer Stop hook, a prompt (--reset) or a racing hook
  // replaces the pid, which turns this one into a no-op.
  process.on('SIGTERM', () => process.exit(0));
  setTimeout(() => {
    if (s.read().pid !== process.pid) process.exit(0);
    // Decide now, not in the Stop hook: the last reply may not be flushed to the transcript yet.
    let act = 'alarm'; // unreadable transcript falls back to the plain alarm
    try {
      const d = decide(metrics(opt('--tp')), WINDOW);
      act = d.act;
      log(`decision: ${act} (${d.pct}% messages, T ${d.T}%)`);
    } catch {}
    log(`idle ${DELAY}ms elapsed, ${act} ${label()}`);
    if (act === 'compact' && process.env.CLAUDE_IDLE_COMPACT !== '0') compact(process.env.TMUX_PANE);
    else if (act === 'alarm') alert(`Claude Code idle for ${Math.round(DELAY / 6000) / 10} min, consider /clear ${label()}`);
    process.exit(0);
  }, DELAY);
} else {
  // Stop hook: replace this session's timer. Outside tmux there is no timer.
  const pane = process.env.TMUX_PANE || findPane(sid);
  if (!pane) { log(`no tmux pane for session ${sid.slice(0, 8)}, no timer`); process.exit(0); }
  s.killTimer();
  const child = detach(['--wait', '--ms', String(DELAY), '--tp', String(hook.transcript_path), '--sid', sid], { ...process.env, TMUX_PANE: pane });
  child.unref();
  s.write({ pid: child.pid, pane, name: s.name() }); // last writer wins; the losing timer sees a foreign pid and exits
  pruneStale();
  log(`timer started pid=${child.pid} delay=${DELAY}ms`);
}
