const fs = require('node:fs');

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

// Idle case (user away 5+ min, same task). Compact when messages / (window - overhead) >= T, where
// T = 70 - 15 (away) - 10/5 (>50 / 20-50 messages) - 5 (one earlier compaction) - 5 (tool output > 50%), floor 40.
// Not modelled: peak load, task boundary (not readable from a transcript).
// skip: nothing worth doing; compact: silent /compact; alarm: sound + notification (time to /clear).
function decide(m, window) {
  const pct = Math.round(((m.tokens - m.overhead) / (window - m.overhead)) * 100);
  const T = Math.max(40, 55 - (m.msgs > 50 ? 10 : m.msgs >= 20 ? 5 : 0) - (m.compactions ? 5 : 0) - (m.toolHeavy ? 5 : 0));
  if (m.tokens - m.overhead < 20000) return { act: 'skip', pct, T }; // a cold pass over this is cheap
  if (m.compactions >= 2) return { act: 'alarm', pct, T }; // never a third compaction: /clear with a handoff note
  return { act: pct >= T ? 'compact' : 'skip', pct, T };
}

module.exports = { metrics, decide };
