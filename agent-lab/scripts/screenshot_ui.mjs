// UI duman testi + belge ekran görüntüleri (STATİK replay modu — canlı ajan gerekmez).
//
// Kullanım:
//   NODE_PATH=$(npm root -g) PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
//     node scripts/screenshot_ui.mjs [http://127.0.0.1:8799] [proof/] [replay-loop.ndjson]
//
// Sayfayı `?replay=/fixtures/<fixture>` ile açar: sunucu yalnızca statik dosya
// sunar, olaylar tarayıcıda oynatılır. Konsolda hata olursa çıkış kodu 1.
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

// Playwright yerel node_modules'ta olmayabilir (global kurulum): ESM NODE_PATH'i yok sayar.
async function loadPlaywright() {
  try { return await import('playwright'); } catch { /* global kuruluma düş */ }
  const root = process.env.PLAYWRIGHT_ROOT ?? execSync('npm root -g').toString().trim();
  return await import(pathToFileURL(path.join(root, 'playwright', 'index.js')).href);
}
const pw = await loadPlaywright();
const chromium = pw.chromium ?? pw.default?.chromium;   // CJS/ESM interop

const base = process.argv[2] ?? 'http://127.0.0.1:8799';
const out = process.argv[3] ?? 'proof';
const fixture = process.argv[4] ?? 'replay-loop.ndjson';
fs.mkdirSync(out, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const errors = [];
const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-gpu'] });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 1000 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
page.on('console', (m) => { if (m.type() === 'error') errors.push('console: ' + m.text()); });

const url = `${base}/?replay=/fixtures/${fixture}`;
console.log('→', url);
await page.goto(url, { waitUntil: 'load' });

const shots = [
  ['10-ui-start.png', 900],    // sahne kuruldu, ilk olaylar
  ['11-ui-working.png', 2600], // ajan çalışıyor, kod akışı doluyor
  ['12-ui-done.png', 4200],    // görev sonucu tabelası
];
for (const [name, waitMs] of shots) {
  await sleep(waitMs);
  await page.screenshot({ path: path.join(out, name) });
  console.log('  ✓', name);
}
// yalnızca sahne (dokümanlar için kırpılmış)
await page.locator('#scene').screenshot({ path: path.join(out, '13-scene.png') });
console.log('  ✓ 13-scene.png');

// UI durumunun makine-okunur özeti (regresyon karşılaştırması için)
const state = await page.evaluate(() => ({
  mode: document.querySelector('#bMode').textContent,
  task: document.querySelector('#taskText').textContent,
  goal: document.querySelector('#goalCount').textContent,
  conf: document.querySelector('#confVal').textContent,
  events: document.querySelector('#bEvents').textContent,
  logLines: document.querySelectorAll('#log .ln').length,
  lastLine: document.querySelector('#log .ln:last-child')?.textContent ?? '',
}));
fs.writeFileSync(path.join(out, 'ui-state.json'), JSON.stringify(state, null, 2));
console.log(state);

await ctx.close();
await browser.close();

if (errors.length) { console.error('UI HATALARI:\n' + errors.join('\n')); process.exit(1); }
console.log('OK — konsol hatası yok, ekran görüntüleri:', out);
