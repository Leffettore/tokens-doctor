const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { metrics, decide } = require('../scripts/lib/decide');

const W = 200000;
const m = (tokens, o = {}) => ({ tokens, overhead: 30000, compactions: 0, msgs: 10, toolHeavy: false, ...o });
const act = (...a) => decide(...a, W).act;

test('decide', () => {
  assert.strictEqual(act(m(45000)), 'skip'); // 15k of messages is cheap
  assert.strictEqual(act(m(100000)), 'skip'); // 41% < T 55
  assert.strictEqual(act(m(125000)), 'compact'); // 56%
  assert.strictEqual(act(m(107000, { compactions: 1, toolHeavy: true })), 'compact'); // 45% >= T 45
  assert.strictEqual(act(m(100000, { compactions: 1, toolHeavy: true })), 'skip'); // 41% < 45
  assert.strictEqual(act(m(100000, { compactions: 1, toolHeavy: true, msgs: 60 })), 'compact'); // T floors at 40
  assert.strictEqual(act(m(100000, { compactions: 2 })), 'alarm');
  assert.strictEqual(act(m(45000, { compactions: 3 })), 'skip');
});

test('metrics reads a transcript', () => {
  const usage = (n) => ({ input_tokens: n, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 });
  const lines = [
    { type: 'user', message: { content: 'hi' } },
    { type: 'assistant', message: { content: 'a', usage: usage(30000) } },
    { type: 'user', message: { content: [{ type: 'tool_result', content: 'x'.repeat(500) }] } },
    { type: 'assistant', message: { content: 'b', usage: usage(80000) }, isSidechain: true },
    { type: 'assistant', message: { content: 'c', usage: usage(60000) } },
  ];
  const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'td-')), 't.jsonl');
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n'));
  assert.deepStrictEqual(metrics(file), { tokens: 60000, overhead: 30000, compactions: 0, msgs: 1, toolHeavy: true });

  lines.push({ subtype: 'compact_boundary', compactMetadata: { postTokens: 5000 } });
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n'));
  assert.deepStrictEqual(metrics(file), { tokens: 35000, overhead: 30000, compactions: 1, msgs: 0, toolHeavy: false });
});
