const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { contextTokens } = require('../scripts/lib/context');

const usage = (n, cache = 0) => ({ input_tokens: n, cache_read_input_tokens: cache, cache_creation_input_tokens: 0 });
const reply = (u, extra) => ({ type: 'assistant', message: { content: 'x', usage: u }, ...extra });

test('contextTokens', () => {
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'td-')), 't.jsonl');
  const tokens = (lines) => { fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n')); return contextTokens(file); };

  assert.strictEqual(tokens([]), 0);
  // last main-thread reply wins, sidechains are ignored, cache reads count
  assert.strictEqual(tokens([reply(usage(10, 20000)), reply(usage(1), { isSidechain: true })]), 20010);
  // a compaction resets until the next reply
  assert.strictEqual(tokens([reply(usage(90000)), { subtype: 'compact_boundary' }]), 0);
  assert.strictEqual(tokens([reply(usage(90000)), { subtype: 'compact_boundary' }, reply(usage(8000))]), 8000);
});
