// Usage: node tools/site-smoke/smoke.mjs site/index.html [--out shots/] [--tab id ...]
// Opens the built dashboard from file:// in Chrome (CHROME_PATH, or Playwright's "chrome" channel) at 1440 and
// 390 px, visits every tab, opens and closes a drawer, and fails on console errors, page errors, failed requests
// or horizontal page scroll at 390 px. Prints a JSON report.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const { chromium } = require("playwright-core");

const args = process.argv.slice(2);
const index = resolve(args.find((a) => !a.startsWith("--")) || "site/index.html");
const outIdx = args.indexOf("--out");
const out = outIdx >= 0 ? resolve(args[outIdx + 1]) : null;
if (out) mkdirSync(out, { recursive: true });

const launch = process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" };
const browser = await chromium.launch({ headless: true, ...launch });
const report = { index, viewports: [] };
let bad = 0;

for (const [w, h] of [[1440, 900], [390, 844]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push("console: " + m.text()); });
  page.on("pageerror", (e) => errors.push("pageerror: " + e.message));
  page.on("requestfailed", (r) => errors.push("requestfailed: " + r.url() + " " + (r.failure()?.errorText || "")));
  await page.goto(pathToFileURL(index).href, { waitUntil: "networkidle" });
  const vp = { width: w, tabs: [], drawer: [], errors };
  const tabs = await page.$$eval("#tabs [role=tab]", (b) => b.map((x) => ({ id: x.id.replace(/^tab-/, ""), label: x.textContent.trim() })));
  for (const t of tabs) {
    await page.click(`#tab-${t.id}`);
    await page.waitForTimeout(250);
    const info = await page.evaluate((id) => {
      const p = document.getElementById("view-" + id);
      return { visible: !!p && !p.hidden && p.offsetHeight > 0, children: p ? p.querySelectorAll("*").length : 0,
        overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth };
    }, t.id);
    vp.tabs.push({ ...t, ...info });
    if (!info.visible || info.children === 0) errors.push(`tab ${t.id} rendered nothing`);
    if (w <= 400 && info.overflowX > 0) errors.push(`tab ${t.id}: horizontal page scroll of ${info.overflowX}px at ${w}px`);
    if (out) await page.screenshot({ path: `${out}/${t.id}-${w}.png`, fullPage: true });
    // open the first drawer target in this tab: a matrix cell button, else a clickable row
    const opener = (await page.$(`#view-${t.id} .cell-btn`)) || (await page.$(`#view-${t.id} tr.clickable`));
    if (opener) {
      await opener.click();
      await page.waitForTimeout(350);
      const open = await page.evaluate(() => document.getElementById("drawer").classList.contains("open"));
      const title = await page.textContent("#drTitle");
      const evBlocks = await page.$$eval("#drMain .ev, #drMain .empty", (n) => n.length);
      if (out) await page.screenshot({ path: `${out}/${t.id}-drawer-${w}.png` });
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      const closed = await page.evaluate(() => !document.getElementById("drawer").classList.contains("open"));
      const focusBack = await page.evaluate(() => document.activeElement && document.activeElement !== document.body);
      vp.drawer.push({ tab: t.id, open, title, evBlocks, closed, focusBack });
      if (!open || !closed || !evBlocks) errors.push(`tab ${t.id}: drawer open=${open} closed=${closed} blocks=${evBlocks}`);
    }
  }
  bad += errors.length;
  report.viewports.push(vp);
  await page.close();
}
await browser.close();
report.ok = bad === 0;
console.log(JSON.stringify(report, null, 2));
process.exit(bad ? 1 : 0);
