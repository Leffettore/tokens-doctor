const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const DIR = path.join(os.homedir(), '.claude');
const LOG_FILE = path.join(DIR, 'idle.log');

const log = (msg) => {
  try { fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} ${msg}\n`); } catch {}
};

module.exports = { DIR, log };
