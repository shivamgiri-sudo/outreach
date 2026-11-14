// Screenshots of both apps after the round-trip scenario (called by dialhalo_roundtrip.py when SCREENSHOTS=1).
// Env: OUT, CT (base URLs), OUT_TOKEN, A_ID, D_ID, CT_EMAIL, CT_PASSWORD, SHOTS (folder), PLAYWRIGHT_CORE, CHROMIUM
const { chromium } = require(process.env.PLAYWRIGHT_CORE);
const { OUT, CT, OUT_TOKEN, A_ID, D_ID, CT_EMAIL, CT_PASSWORD, SHOTS } = process.env;
(async () => {
  const b = await chromium.launch({ executablePath: process.env.CHROMIUM });
  // MCN Outreach
  let ctx = await b.newContext({ viewport: { width: 1440, height: 1000 } });
  await ctx.addInitScript((t) => { try { sessionStorage.setItem("outreach-token", t); localStorage.setItem("outreach-onboarded", "1"); } catch (e) {} }, OUT_TOKEN);
  let p = await ctx.newPage();
  await p.goto(`${OUT}/#integrations?tab=calltracker`, { waitUntil: "networkidle" });
  await p.keyboard.press("Escape").catch(() => {});
  try { await p.getByTestId("ct-test").waitFor({ timeout: 15000 }); } catch (e) {
    await p.screenshot({ path: `${SHOTS}/outreach-debug.png` }); console.error("page text:", (await p.innerText("body")).slice(0, 400).replace(/\s+/g, " ")); throw e;
  }
  await p.getByTestId("ct-test").click();
  await p.getByTestId("ct-unmatched").waitFor({ timeout: 20000 });
  await p.waitForTimeout(600);
  await p.screenshot({ path: `${SHOTS}/outreach-card-unmatched.png`, fullPage: true });
  for (const [id, name] of [[A_ID, "outreach-lead-call-timeline"], [D_ID, "outreach-lead-wrong-number"]]) {
    await p.goto(`${OUT}/#leads?lead=${id}`, { waitUntil: "networkidle" });
    await p.waitForTimeout(1500);
    await p.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: false });
  }
  await ctx.close();
  // DialHalo portal
  ctx = await b.newContext({ viewport: { width: 1440, height: 900 } });
  p = await ctx.newPage();
  await p.goto(`${CT}/login`);
  await p.fill("input[type=email]", CT_EMAIL); await p.fill("input[type=password]", CT_PASSWORD); await p.keyboard.press("Enter");
  await p.waitForURL((u) => !String(u).includes("/login"), { timeout: 20000 });
  for (const [path, name] of [["calls", "dialhalo-call-history"], ["leads", "dialhalo-leads"]]) {
    await p.goto(`${CT}/${path}`, { waitUntil: "networkidle" });
    await p.waitForTimeout(1200);
    await p.screenshot({ path: `${SHOTS}/${name}.png` });
  }
  await b.close();
})().catch((e) => { console.error("screenshots failed:", e.message); process.exit(1); });
