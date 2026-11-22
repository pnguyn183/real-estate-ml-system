import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';

import { chromium } from 'playwright-core';
import { preview } from 'vite';


function browserPath() {
  const configured = process.env.PLAYWRIGHT_BROWSER_PATH;
  const candidates = configured ? [configured] : process.platform === 'win32' ? [
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft Edge\\Application\\msedge.exe',
    path.join(process.env.LOCALAPPDATA || '', 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
  ] : process.platform === 'darwin' ? [
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ] : [
    '/usr/bin/microsoft-edge',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
  ];
  const selected = candidates.find((candidate) => candidate && existsSync(candidate));
  assert.ok(selected, 'Set PLAYWRIGHT_BROWSER_PATH to an installed Chromium/Edge executable.');
  return selected;
}


test('public evidence demo renders and opens the protected workspace', { timeout: 60_000 }, async (context) => {
  const server = await preview({
    preview: { host: '127.0.0.1', port: 4173, strictPort: false },
  });
  context.after(() => new Promise((resolve, reject) => {
    server.httpServer.close((error) => error ? reject(error) : resolve());
  }));
  const url = server.resolvedUrls?.local[0] || 'http://127.0.0.1:4173/';

  const browser = await chromium.launch({
    executablePath: browserPath(),
    headless: true,
  });
  context.after(() => browser.close());
  const page = await browser.newPage();
  context.after(() => page.close());
  const pageErrors = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await page.goto(url, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: /Bi\u1ebfn lu\u1ed3ng tin \u0111\u0103ng/ }).waitFor();

  const body = await page.locator('body').innerText();
  assert.match(body, /t\u1ea3i synthetic nh\u1ecf/i);
  assert.match(body, /Ch\u01b0a \u0111\u1ee7 b\u1eb1ng ch\u1ee9ng/);
  assert.match(body, /docs\/evidence\/container-lifecycle-recovery-20261114\.json/);
  assert.match(body, /ch\u01b0a ch\u1ee9ng minh t\u1ed5ng qu\u00e1t h\u00f3a ngo\u00e0i snapshot/i);

  await page.getByRole('button', { name: /M\u1edf khu v\u1ef1c d\u1ef1 \u0111o\u00e1n/ }).click();
  await page.getByRole('heading', { name: 'Sign in' }).waitFor();
  await page.getByRole('button', { name: /Quay l\u1ea1i demo h\u1ec7 th\u1ed1ng/ }).click();
  await page.getByRole('heading', { name: /Bi\u1ebfn lu\u1ed3ng tin \u0111\u0103ng/ }).waitFor();

  assert.deepEqual(pageErrors, []);
});
