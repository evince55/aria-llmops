// Deterministic grader for the perception-cascade experiment.
// Usage: node grade.js <url> <bug:b1|b2|b3> <out.json>
//
// The grader is the ground-truth instrument: it knows which bug was planted
// and asserts the *rendered* fixed state with Playwright — computed styles,
// bounding boxes, console — never the builder's claims. Builders never see
// this file; their in-run verifier (tier-0 VLM) is a separate, generic tool.
const { chromium } = require('playwright');
const fs = require('fs');

function lum(c) {
  const s = c.map((v) => {
    v /= 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  });
  return 0.2126 * s[0] + 0.7152 * s[1] + 0.0722 * s[2];
}
function contrast(a, b) {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}
const parseRgb = (str) => (str.match(/[\d.]+/g) || []).slice(0, 3).map(Number);

(async () => {
  const [url, bug, outPath] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const consoleLines = [];
  page.on('console', (m) => consoleLines.push(`[${m.type()}] ${m.text()}`));
  page.on('pageerror', (e) => consoleLines.push(`[pageerror] ${e.message}`));
  await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(1500);

  const checks = {};

  // Global sanity: the page wasn't vandalised into "fixed".
  checks.title_present = (await page.locator('.hero__title').count()) === 1 &&
    (await page.locator('.hero__title').innerText()).trim() === 'Eugene Vincent';

  if (bug === 'b1') {
    const tl = page.locator('.hero__tagline');
    checks.tagline_present = (await tl.count()) === 1;
    checks.tagline_text_intact = checks.tagline_present &&
      (await tl.innerText()).includes('I build production infrastructure');
    if (checks.tagline_present) {
      const { color, bg } = await tl.evaluate((el) => {
        // effective background = nearest ancestor with non-transparent bg
        let n = el, bg = 'rgb(7, 7, 11)';
        while (n) {
          const b = getComputedStyle(n).backgroundColor;
          if (b && !b.includes('0, 0, 0, 0') && b !== 'transparent') { bg = b; break; }
          n = n.parentElement;
        }
        return { color: getComputedStyle(el).color, bg };
      });
      checks.tagline_contrast = contrast(parseRgb(color), parseRgb(bg));
      checks.tagline_visible = checks.tagline_contrast >= 4.5;
    }
    checks.pass = !!(checks.tagline_text_intact && checks.tagline_visible && checks.title_present);
  }

  if (bug === 'b2') {
    const boxes = {};
    for (const sel of ['.hero__ctas', '.hero__proof']) {
      const el = page.locator(sel);
      checks[`${sel}_present`] = (await el.count()) === 1;
      if (checks[`${sel}_present`]) boxes[sel] = await el.boundingBox();
    }
    if (boxes['.hero__ctas'] && boxes['.hero__proof']) {
      const a = boxes['.hero__ctas'], b = boxes['.hero__proof'];
      const overlapY = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
      const overlapX = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
      checks.overlap_px = Math.round(Math.max(0, overlapY)) * (overlapX > 0 ? 1 : 0);
      checks.no_overlap = !(overlapY > 2 && overlapX > 2);
      checks.proof_below_ctas = b.y >= a.y + a.height - 2;
    }
    checks.pass = !!(checks.no_overlap && checks.proof_below_ctas && checks.title_present);
  }

  if (bug === 'b4') {
    const btn = page.locator('.hero__ctas .btn--primary').first();
    checks.btn_present = (await btn.count()) === 1;
    if (checks.btn_present) {
      const bg = await btn.evaluate((el) => getComputedStyle(el).backgroundColor);
      const rgb = parseRgb(bg);
      checks.btn_bg = bg;
      checks.btn_luminance = Math.round(lum(rgb) * 1000) / 1000;
      checks.btn_contrast_vs_page = Math.round(contrast(rgb, [7, 7, 11]) * 100) / 100;
      // pristine cyan: lum ~0.55, contrast ~12; sabotaged dark: lum ~0.03, contrast ~1.5
      checks.btn_visible = checks.btn_luminance > 0.15 && checks.btn_contrast_vs_page >= 3;
    }
    checks.pass = !!(checks.btn_visible && checks.title_present);
  }

  if (bug === 'b3') {
    const errs = consoleLines.filter((l) =>
      /pageerror|does not provide an export|SyntaxError|TypeError/i.test(l));
    checks.module_errors = errs;
    checks.console_clean = errs.length === 0;
    // With the module running against the (stale) local feed, the honesty
    // contract must visibly kick in: the note un-hides or the updated line
    // changes off its static seed.
    checks.live_js_ran = await page.evaluate(() => {
      const note = document.querySelector('[data-live-note]');
      const upd = document.querySelector('[data-live-updated]');
      return (note && !note.hidden && note.textContent.trim() !== '') ||
             (upd && upd.textContent.trim() !== 'updated every ~10 min');
    });
    checks.pass = !!(checks.console_clean && checks.live_js_ran && checks.title_present);
  }

  const verdict = { bug, url, pass: !!checks.pass, checks, console_tail: consoleLines.slice(-6) };
  fs.writeFileSync(outPath, JSON.stringify(verdict, null, 2));
  console.log(JSON.stringify({ bug, pass: verdict.pass }));
  await browser.close();
})();
