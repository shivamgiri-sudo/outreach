// "Save to portal" in the Chrome assistant, end to end: the unpacked extension in Chromium, a local fixture profile page
// and a local backend. Real linkedin.com is never contacted: every https://www.linkedin.com/* request is answered from
// the fixture server on 127.0.0.1:5274 (context.route), and every other non-local request is aborted.
//
//   cd web && node e2e/extension_save_profile.mjs
//
// Starts its own backend (../.venv/bin/uvicorn on 127.0.0.1:8074, fresh temp data file, no admin password = local mode)
// and fixture server (127.0.0.1:5274), and stops both at the end. Exits 1 on any failed check or console error.
//
// Test-only difference from a real install: the temp copy of the extension lists http://127.0.0.1/* under
// host_permissions. In real use that origin is the optional permission Chrome asks for when the person presses
// "Save and connect" in the popup; a permission prompt cannot be clicked in an automated browser.
// The side panel is opened as its own window (chrome-extension://<id>/sidepanel.html) because Playwright cannot open
// Chrome's side panel; the panel then reads the active LinkedIn tab of the other window.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..", "..");
const API_PORT = 8074, FIX_PORT = 5274;
const API = `http://127.0.0.1:${API_PORT}`;
const SHOTS = process.env.SHOT_DIR || path.join(ROOT, "web", "e2e-shots", "save-profile");
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "save-profile-e2e-"));
fs.mkdirSync(SHOTS, { recursive: true });

let failed = false;
const check = (label, ok, extra = "") => { console.log(ok ? "PASS" : "FAIL", label, extra); if (!ok) failed = true; };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------- fixture server: a profile, a profile with a hidden headline, a search results page ----------
const profileHtml = fs.readFileSync(path.join(HERE, "fixtures", "linkedin-profile.html"), "utf8");
const searchHtml = fs.readFileSync(path.join(HERE, "fixtures", "linkedin-search.html"), "utf8");
const fixtureHits = [];
const fixtures = http.createServer((req, res) => {
  const p = new URL(req.url, "http://x").pathname;
  fixtureHits.push(p);
  let body = null;
  if (/^\/in\/test-person(\/|\/details\/experience\/?)?$/.test(p)) body = profileHtml.replace("__HEADLINE_STYLE__", "");
  else if (/^\/in\/hidden-headline\/?$/.test(p)) body = profileHtml.replace("__HEADLINE_STYLE__", "display:none").replace(/Test Person/g, "Hidden Headline");
  else if (/^\/in\/una-update\/?$/.test(p)) body = profileHtml.replace("__HEADLINE_STYLE__", "").replace(/Test Person/g, "Una Update").replace(/Initech/g, "Umbrella");
  else if (p.startsWith("/search/results/people")) body = searchHtml;
  res.writeHead(body ? 200 : 404, { "Content-Type": "text/html; charset=utf-8" });
  res.end(body || "not found");
});
await new Promise((r) => fixtures.listen(FIX_PORT, "127.0.0.1", r));

// ---------- backend ----------
const api = spawn(path.join(ROOT, ".venv", "bin", "uvicorn"), ["app.main:app", "--host", "127.0.0.1", "--port", String(API_PORT)], {
  cwd: ROOT, env: { ...process.env, OUTREACH_DATA_FILE: path.join(tmp, "d.json"), OUTREACH_ADMIN_PASSWORD: "" }, stdio: ["ignore", "ignore", "pipe"],
});
let apiErr = "";
api.stderr.on("data", (d) => { apiErr += d; });
for (let i = 0; i < 60; i++) {
  try { if ((await fetch(API + "/api/auth/status")).ok) break; } catch (_) { /* not up yet */ }
  await wait(500);
}
const token = (await (await fetch(API + "/api/extension/pair", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })).json()).token;
check("pairing token made", typeof token === "string" && token.startsWith("mcnx_"));

// ---------- extension copy (see the header for the one manifest difference) ----------
const extDir = path.join(tmp, "ext");
fs.cpSync(path.join(ROOT, "extensions", "linkedin-assistant"), extDir, { recursive: true });
const manifest = JSON.parse(fs.readFileSync(path.join(extDir, "manifest.json"), "utf8"));
check("manifest version bumped", manifest.version === "0.2.0", manifest.version);
manifest.host_permissions = [...manifest.host_permissions, "http://127.0.0.1/*"];
fs.writeFileSync(path.join(extDir, "manifest.json"), JSON.stringify(manifest));

const context = await chromium.launchPersistentContext(path.join(tmp, "profile"), {
  channel: "chromium", headless: true, viewport: { width: 420, height: 900 },
  // safety net: no host name except the local ones resolves, so nothing can reach the real LinkedIn even if a route misses
  args: [`--disable-extensions-except=${extDir}`, `--load-extension=${extDir}`, "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1, EXCLUDE localhost"],
});
const leaked = [];
await context.route("**/*", async (route) => {
  const u = new URL(route.request().url());
  if (process.env.E2E_DEBUG) console.log("ROUTE", route.request().method(), u.href);
  if (u.hostname === "www.linkedin.com") {
    const r = await fetch(`http://127.0.0.1:${FIX_PORT}${u.pathname}${u.search}`);
    return route.fulfill({ status: r.status, contentType: "text/html; charset=utf-8", body: await r.text() });
  }
  if (["127.0.0.1", "localhost"].includes(u.hostname) || u.protocol === "chrome-extension:" || u.protocol === "data:") return route.continue();
  leaked.push(u.href);
  return route.abort();
});

try {
  const sw = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
  await sw.evaluate(([server, token]) => chrome.storage.local.set({ server, token }), [API, token]);

  const openWindow = async (url) => {
    const [page] = await Promise.all([context.waitForEvent("page"), sw.evaluate((u) => chrome.windows.create({ url: u, focused: true }), url)]);
    if (page.url() !== url) await page.waitForURL(url, { timeout: 15000 });
    await page.waitForLoadState("load");
    return page;
  };
  // the browser's first tab plays the person's LinkedIn tab; Playwright navigates it like a person typing the address
  const li = context.pages()[0] || await context.newPage();
  await li.goto("https://www.linkedin.com/in/test-person/");
  check("fixture profile loaded through the route", (await li.textContent("h1")) === "Test Person");
  const panel = await openWindow(await sw.evaluate(() => chrome.runtime.getURL("sidepanel.html")));
  const errors = [];
  panel.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  panel.on("pageerror", (e) => errors.push(String(e)));
  const refresh = async () => { await panel.click("#refresh"); await wait(600); };

  // 1. a profile that is not a lead -> Save to portal -> editable confirm box with only what the page shows
  await refresh();
  await panel.waitForSelector("#save-profile", { timeout: 10000 });
  check("Save to portal shown on a non-lead profile", (await panel.textContent("#save-profile")) === "Save to portal");
  await panel.click("#save-profile");
  await panel.waitForSelector("#save-confirm-box");
  const vals = await panel.evaluate(() => Object.fromEntries(["name", "title", "company", "location"].map((k) => [k, document.getElementById("save-" + k).value])));
  check("confirm box: name", vals.name === "Test Person", vals.name);
  check("confirm box: headline", vals.title === "Head of Talent Acquisition at Initech", vals.title);
  check("confirm box: current company", vals.company === "Initech Pvt Ltd", vals.company);
  check("confirm box: location", vals.location === "Pune, Maharashtra, India", vals.location);
  check("nothing from 'People also viewed'", !JSON.stringify(vals).includes("Other Co"));
  const before = (await (await fetch(API + "/api/leads")).json()).length;
  check("nothing saved before pressing Save", before === 0, String(before));
  await panel.fill("#save-title", "Head of TA (edited)");
  await panel.screenshot({ path: path.join(SHOTS, "1-confirm.png") });
  await panel.click("#save-confirm");
  await panel.waitForSelector(".banner.ok", { timeout: 10000 });
  check("result banner", (await panel.textContent(".banner.ok")).includes("Saved to the portal as a new lead"));
  await panel.screenshot({ path: path.join(SHOTS, "2-saved.png") });
  const leads = await (await fetch(API + "/api/leads")).json();
  const saved = leads.find((l) => l.linkedin_url === "https://www.linkedin.com/in/test-person");
  check("one lead created", leads.length === 1 && !!saved, String(leads.length));
  check("lead fields", saved && saved.contact_name === "Test Person" && saved.title === "Head of TA (edited)" && saved.company === "Initech Pvt Ltd"
    && saved.city === "Pune" && saved.source === "linkedin_manual", JSON.stringify(saved && { t: saved.title, c: saved.company, s: saved.source }));
  check("panel now shows the lead", (await panel.textContent("#view")).includes("Sequence:") && await panel.locator("#save-profile").count() === 0);

  // 2. the same profile again: matched, nothing missing, no button
  await refresh();
  check("matched profile: no Save button", await panel.locator("#save-profile").count() === 0 && (await panel.textContent("#view")).includes("Test Person"));

  // 3. other LinkedIn pages: no Save button
  await li.goto("https://www.linkedin.com/search/results/people/?keywords=hr");
  await refresh();
  check("search results: no Save button", await panel.locator("#save-profile").count() === 0, (await panel.textContent("#view")).slice(0, 80));
  await panel.screenshot({ path: path.join(SHOTS, "3-search.png") });
  await li.goto("https://www.linkedin.com/in/test-person/details/experience/");
  await refresh();
  check("profile sub-page: no Save/Update button", await panel.locator("#save-profile").count() === 0);

  // 4. only visible text is read: a hidden headline is not offered
  await li.goto("https://www.linkedin.com/in/hidden-headline/");
  await refresh();
  await panel.waitForSelector("#save-profile", { timeout: 10000 });
  await panel.click("#save-profile");
  const hidden = await panel.evaluate(() => ({ name: document.getElementById("save-name").value, title: document.getElementById("save-title").value }));
  check("hidden headline not read", hidden.name === "Hidden Headline" && hidden.title === "", JSON.stringify(hidden));
  await panel.click("text=Cancel");
  check("cancel saves nothing", (await (await fetch(API + "/api/leads")).json()).length === 1);

  // 5. an existing lead (same name + company, no profile link, no title; another company than step 1, because an import
  //    without a contact would merge into that lead): "Update lead" fills only the empty fields
  await fetch(API + "/api/leads/import", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csv: "company,contact_name,email\nUmbrella Pvt Ltd,Una Update,una@umbrella.test\n", source: "apollo" }) });
  await li.goto("https://www.linkedin.com/in/una-update/");
  await refresh();
  await panel.waitForSelector("#save-profile", { timeout: 10000 });
  check("Update lead shown for a lead with empty fields", (await panel.textContent("#save-profile")) === "Update lead");
  await panel.click("#save-profile");
  await panel.click("#save-confirm");
  await panel.waitForSelector(".banner.ok", { timeout: 10000 });
  const upd = await panel.textContent(".banner.ok");
  check("update banner lists what was added", upd.includes("Lead updated") && upd.includes("Title") && upd.includes("LinkedIn profile"), upd);
  await panel.screenshot({ path: path.join(SHOTS, "4-updated.png") });
  const una = (await (await fetch(API + "/api/leads")).json()).find((l) => l.contact_name === "Una Update");
  check("existing lead kept its source and email, got title + link", una && una.source === "apollo" && una.email === "una@umbrella.test"
    && una.title === "Head of Talent Acquisition at Umbrella" && una.linkedin_url === "https://www.linkedin.com/in/una-update");
  check("no duplicate lead", (await (await fetch(API + "/api/leads")).json()).length === 2);
  await refresh();
  check("after the update: no button", await panel.locator("#save-profile").count() === 0);

  check("no console errors in the panel", errors.length === 0, errors.join(" | "));
  check("no request left this computer", leaked.length === 0, leaked.join(", "));
  check("every LinkedIn request was answered by the fixture", fixtureHits.length >= 4, fixtureHits.join(","));
} catch (err) {
  console.log("FAIL exception", err && err.stack || err);
  failed = true;
} finally {
  await context.close();
  api.kill("SIGTERM");
  fixtures.close();
  if (failed && apiErr) console.log(apiErr.split("\n").slice(-20).join("\n"));
  fs.rmSync(tmp, { recursive: true, force: true });
}
console.log(failed ? "RESULT: FAILED" : "RESULT: all checks passed");
process.exit(failed ? 1 : 0);
