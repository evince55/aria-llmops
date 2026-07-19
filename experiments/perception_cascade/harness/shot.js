// Capture a page's rendered state: screenshot + console/page errors.
// Usage: node shot.js <url> <out.png> <console.txt> [viewportWxH]
// The console log is part of the captured state on purpose — "is the console
// clean after refresh?" is one of the tier-0 verification questions the
// experiment exists to test.
const { chromium } = require('playwright');

(async () => {
  const [url, outPng, outConsole, viewport = '1280x800'] = process.argv.slice(2);
  if (!url || !outPng || !outConsole) {
    console.error('usage: node shot.js <url> <out.png> <console.txt> [WxH]');
    process.exit(2);
  }
  const [w, h] = viewport.split('x').map(Number);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: w, height: h } });

  const lines = [];
  page.on('console', (msg) => lines.push(`[${msg.type()}] ${msg.text()}`));
  page.on('pageerror', (err) => lines.push(`[pageerror] ${err.message}`));
  page.on('requestfailed', (req) =>
    lines.push(`[requestfailed] ${req.url()} :: ${req.failure()?.errorText}`));

  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });
  } catch (e) {
    lines.push(`[goto-error] ${e.message}`);
  }
  await page.waitForTimeout(1200); // let late JS/animations settle
  await page.screenshot({ path: outPng, fullPage: false });
  require('fs').writeFileSync(outConsole, lines.join('\n') + '\n');
  await browser.close();
  console.log(`shot: ${outPng} (${lines.length} console lines)`);
})();
