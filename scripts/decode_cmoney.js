// Decode CMoney __NUXT__ IIFE payload to extract stockId list for a concept group
// Usage: node decode_cmoney.js <conceptCode>
// e.g.:  node decode_cmoney.js C50852

const https = require('https');
const vm    = require('vm');

const conceptCode = process.argv[2] || 'C50852';
const url = `https://www.cmoney.tw/forum/concept/${conceptCode}`;

function fetch(url) {
  return new Promise((resolve, reject) => {
    const req = https.get(url, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' },
      rejectUnauthorized: false,
    }, res => {
      let data = '';
      res.setEncoding('utf8');
      res.on('data', c => data += c);
      res.on('end', () => resolve(data));
    });
    req.on('error', reject);
    req.setTimeout(20000, () => { req.destroy(); reject(new Error('timeout')); });
  });
}

function extractNuxtPayload(html) {
  const m = html.match(/window\.__NUXT__\s*=\s*([\s\S]+?);\s*<\/script>/);
  return m ? m[1] : null;
}

function collectStockIds(obj, found = new Set()) {
  if (!obj || typeof obj !== 'object') return found;
  if (Array.isArray(obj)) {
    obj.forEach(item => collectStockIds(item, found));
  } else {
    for (const [k, v] of Object.entries(obj)) {
      if (k === 'stockId' && (typeof v === 'string' || typeof v === 'number')) {
        const s = String(v);
        if (/^\d{4,5}$/.test(s)) found.add(s);
      }
      collectStockIds(v, found);
    }
  }
  return found;
}

(async () => {
  try {
    process.stderr.write(`Fetching ${url} ...\n`);
    const html = await fetch(url);
    const payload = extractNuxtPayload(html);
    if (!payload) { process.stderr.write('__NUXT__ not found\n'); process.exit(1); }

    // Execute the IIFE in a sandbox
    const sandbox = { window: {}, __NUXT__: null };
    vm.createContext(sandbox);
    vm.runInContext(`window.__NUXT__ = ${payload}`, sandbox, { timeout: 10000 });

    const nuxt = sandbox.window.__NUXT__;
    if (!nuxt) { process.stderr.write('NUXT eval returned null\n'); process.exit(1); }

    const ids = collectStockIds(nuxt);
    process.stderr.write(`Found ${ids.size} stock IDs\n`);
    // Output as JSON array to stdout
    console.log(JSON.stringify([...ids].sort()));
  } catch (e) {
    process.stderr.write(`Error: ${e.message}\n`);
    process.exit(1);
  }
})();
