// Whole-app crawl through the real UI: every page at 1400/768/375, click tabs/sorts/pagers/chips/selects, report problems.
//   node e2e/qa_ui_crawl.mjs <ui url> <password> [label] [widths=desktop,tablet,phone] [pages=a,b]
import { chromium } from "playwright";
import { PAGES, WIDTHS, watch, textProblems, overflow, signIn, goPage, clickAround, settle } from "./qa_ui_lib.mjs";

const [UI, PASS, LABEL = "crawl", W = "desktop,tablet,phone", ONLY = ""] = process.argv.slice(2);
const browser = await chromium.launch();
const report = {};
for (const [size, width, height] of WIDTHS.filter(([n]) => W.split(",").includes(n))) {
  const problems = [];
  const ctxB = await browser.newContext({ viewport: { width, height } });
  const page = await ctxB.newPage();
  page.setDefaultTimeout(10000);
  const ctx = watch(page, problems);
  ctx.where = `${size} login`;
  await signIn(page, UI, PASS);
  const phone = width <= 760;
  for (const id of PAGES.filter((p) => !ONLY || ONLY.split(",").includes(p))) {
    ctx.where = `${size} ${id}`;
    try {
      await goPage(page, id, phone);
      await settle(page, 600);
      const h = await page.locator("main h1, main h2").first().innerText().catch(() => "(no heading)");
      for (const t of await textProblems(page)) problems.push(`[${size} ${id}] ${t}`);
      const o = await overflow(page); if (o) problems.push(`[${size} ${id}] ${o}`);
      const clicks = await clickAround(page, problems, ctx, `${size} ${id}`, { phoneCheck: phone, max: size === "desktop" ? 25 : 8 });
      const o2 = await overflow(page); if (o2 && o2 !== o) problems.push(`[${size} ${id}] after clicks: ${o2}`);
      if (size === "desktop") await page.screenshot({ path: `${process.env.SHOTS || "/tmp"}/${LABEL}-${size}-${id}.png`, fullPage: true }).catch(() => {});
      else await page.screenshot({ path: `${process.env.SHOTS || "/tmp"}/${LABEL}-${size}-${id}.png` }).catch(() => {});
      console.log(`${size} ${id}: "${h.slice(0, 40)}" clicks=${clicks}`);
    } catch (e) {
      problems.push(`[${size} ${id}] FAILED: ${e.message.split("\n")[0]}`); console.log(problems.at(-1));
    }
  }
  report[size] = [...new Set(problems)];
  await ctxB.close();
}
await browser.close();
for (const [s, ps] of Object.entries(report)) { console.log(`\n==== ${s}: ${ps.length} problems`); ps.forEach((p) => console.log(" -", p)); }
