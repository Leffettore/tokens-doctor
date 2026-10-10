const fs = require('node:fs');

// Context size in tokens as of the last main-thread reply (what the next message re-reads when the cache is cold).
// A compaction resets it to 0 until the next reply reports the real size.
function contextTokens(file) {
  let tokens = 0;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    let e;
    try { e = JSON.parse(line); } catch { continue; }
    if (e.isSidechain) continue;
    if (e.subtype === 'compact_boundary') tokens = 0;
    const u = e.type === 'assistant' && e.message && e.message.usage;
    if (u) tokens = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
  }
  return tokens;
}

module.exports = { contextTokens };
