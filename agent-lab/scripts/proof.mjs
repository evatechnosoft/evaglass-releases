// Kanıt: gateway'i açar, loop + git-push görevlerini tetikler, sahneyi video + PNG olarak kaydeder.
// Kullanım: NODE_PATH=$(npm root -g) node scripts/proof.mjs http://127.0.0.1:8799 proof/
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';

const base = process.argv[2] ?? 'http://127.0.0.1:8799';
const out = process.argv[3] ?? 'proof';
fs.mkdirSync(out, { recursive: true });

const post = (p, body) => fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
const sleep = ms => new Promise(r => setTimeout(r, ms));

const browser = await chromium.launch({ args: ['--no-sandbox'] });
const ctx = await browser.newContext({ viewport: { width: 1360, height: 900 }, recordVideo: { dir: out, size: { width: 1360, height: 900 } } });
const page = await ctx.newPage();
page.on('pageerror', e => console.log('PAGEERROR', e.message));
page.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text()); });

await page.goto(base + '/', { waitUntil: 'load' });
await sleep(800);
await page.screenshot({ path: path.join(out, '00-idle.png') });

const loop = await post('/sessions/new/commands', { cmd: 'start', task: 'loop', driver: 'scripted', agent: 'goat', pace: 0.35 });
console.log('loop session', loop);
await sleep(2200);
await page.screenshot({ path: path.join(out, '01-loop-running.png') });
await sleep(3000);
await page.screenshot({ path: path.join(out, '02-loop-done.png') });

const gp = await post('/sessions/new/commands', { cmd: 'start', task: 'git-push', driver: 'scripted', agent: 'pengu', pace: 0.5 });
console.log('git-push session', gp);
await sleep(2500);
await page.screenshot({ path: path.join(out, '03-git-push-running.png') });
// onay bekliyorsa yakala ve onayla
for (let i = 0; i < 20; i++) {
  const ss = await fetch(base + '/sessions').then(r => r.json());
  const s = (ss.sessions ?? ss).find(x => x.session === gp.session || x.id === gp.session);
  if (s && (s.last_type ?? s.last) === 'approval.requested') {
    await page.screenshot({ path: path.join(out, '04-approval-pending.png') });
    if (s.pending_approval) await post(`/sessions/${gp.session}/commands`, { cmd: 'approve', approval_id: s.pending_approval });
    break;
  }
  await sleep(300);
}
await sleep(3500);
await page.screenshot({ path: path.join(out, '05-git-push-done.png') });

const sessions = await fetch(base + '/sessions').then(r => r.json());
fs.writeFileSync(path.join(out, 'sessions.json'), JSON.stringify(sessions, null, 2));
await ctx.close();
await browser.close();
// videoyu sabit isme taşı
for (const f of fs.readdirSync(out)) if (f.endsWith('.webm')) fs.renameSync(path.join(out, f), path.join(out, 'demo.webm'));
console.log('proof written to', out);
