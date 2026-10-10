#!/usr/bin/env node
// Idle timer for Claude Code, one detached timer per session. Modes:
//   (none)   Stop hook: start/restart the timer
//   --reset  UserPromptSubmit hook: cancel the timer
//   --end    SessionEnd hook: cancel the timer and forget the session
//   --wait   internal: the detached timer
// When the timer fires and the context is at least CLAUDE_MIN_TOKENS, it notifies you: the prompt cache has
// expired, so the next message re-reads the whole context at full price (/compact or /clear first).
// Env: CLAUDE_IDLE_MS delay (default 300000), CLAUDE_MIN_TOKENS (default 50000).
const fs = require('node:fs');
const { spawn } = require('node:child_process');
const { log } = require('./lib/log');
const { contextTokens } = require('./lib/context');
const { session } = require('./lib/session');
const { notify } = require('./lib/notify');

const args = process.argv.slice(2);
const opt = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const mode = ['--reset', '--end', '--wait'].find((f) => args.includes(f)) || 'start';

const DELAY = Number(opt('--ms') || process.env.CLAUDE_IDLE_MS) || 300000;
const MIN_TOKENS = Number(process.env.CLAUDE_MIN_TOKENS) || 50000;

// Hook input (session_id, transcript_path); the detached timer has no stdin.
let hook = {};
if (mode !== '--wait') try { hook = JSON.parse(fs.readFileSync(0, 'utf8')); } catch {}
const sid = hook.session_id || opt('--sid');
if (!sid) { log('no session_id, nothing to do'); process.exit(0); }
const s = session(sid);

if (mode === '--reset' || mode === '--end') {
  s.stop();
} else if (mode === '--wait') {
  process.on('SIGTERM', () => process.exit(0));
  setTimeout(() => {
    if (s.pid() !== process.pid) process.exit(0); // replaced by a newer timer
    let tokens = 0;
    try { tokens = contextTokens(opt('--tp')); } catch (e) { log(`cannot read transcript: ${e.message}`); }
    const k = Math.round(tokens / 1000);
    log(`idle ${DELAY}ms elapsed, ${k}k tokens in context, ${tokens >= MIN_TOKENS ? 'notify' : 'skip'} [${sid.slice(0, 8)}]`);
    if (tokens >= MIN_TOKENS) notify(`Cache expired: your next message re-reads ${k}k tokens. Consider /compact or /clear.`);
    process.exit(0);
  }, DELAY);
} else {
  s.stop();
  const child = spawn(process.execPath, [__filename, '--wait', '--ms', String(DELAY), '--tp', String(hook.transcript_path), '--sid', sid], { detached: true, stdio: 'ignore' });
  child.unref();
  s.setPid(child.pid); // last writer wins; the losing timer sees a foreign pid and exits
  log(`timer started pid=${child.pid} delay=${DELAY}ms [${sid.slice(0, 8)}]`);
}
