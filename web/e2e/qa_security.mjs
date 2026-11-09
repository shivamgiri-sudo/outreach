// QA (area "security"): login history, two-step login and the owner's switch, through the web UI.
//   API: a fresh OUTREACH_DATA_FILE, OUTREACH_ADMIN_PASSWORD=qa-admin-pass-1 (no OUTREACH_SUPER_ADMIN)
//   UI:  npx vite build --outDir <temp>/dist, served by e2e/static_proxy.mjs with /api proxied to the API
//   cd web && E2E_UI=http://localhost:5284 node e2e/qa_security.mjs
// Uses five password sign-ins in total (the API allows five a minute per address); codes are computed here (RFC 6238).
import crypto from "node:crypto";
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5284";
const ADMIN = process.env.E2E_PASSWORD || "qa-admin-pass-1";
const SHOTS = process.env.SHOT_DIR || "/tmp";
const ANN = { email: "ann.security@qa.test", password: "ann-long-password-1" };
const results = [];
const browser = await chromium.launch();

function totp(secret, offsetSeconds = 0) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/\s+/g, "").toUpperCase()) bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor((Date.now() / 1000 + offsetSeconds) / 30)));
  const mac = crypto.createHmac("sha1", key).update(counter).digest();
  const o = mac[mac.length - 1] & 15;
  return String((mac.readUInt32BE(o) & 0x7fffffff) % 1e6).padStart(6, "0");
}
async function session() {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(`pageerror ${e.message}`));
  return { ctx, page, errors };
}
async function password(page, email, pw) {
  await page.goto(UI);
  await page.getByLabel("Password").waitFor();
  if (email) await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(pw);
  await page.getByRole("button", { name: "Sign in" }).click();
}
async function intoApp(page) {
  await page.locator("main").waitFor();
  const later = page.getByRole("button", { name: "Skip setup" });
  await later.waitFor({ timeout: 6000 }).then(async () => { await later.click(); await later.waitFor({ state: "detached", timeout: 20000 }); }, () => {});
}
const nav = async (page, label) => { await page.locator("nav.side").getByRole("button", { name: label, exact: true }).click(); await page.waitForTimeout(700); };
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const keyText = async (page) => (await page.getByTestId("two-step-key").innerText()).replace(/\s+/g, "");

const owner = await session();
let secret = "";
await step("Owner signs in and adds a caller login", async () => {
  await password(owner.page, "", ADMIN);
  await intoApp(owner.page);
  const status = await owner.page.evaluate(async (u) => {
    const r = await fetch("/api/users", { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${sessionStorage.getItem("outreach-token")}` },
      body: JSON.stringify({ email: u.email, name: "Ann Security", role: "caller", password: u.password }) });
    return r.status;
  }, ANN);
  if (status !== 200) throw new Error(`add user ${status}`);
});
await step("Setup > Security shows the owner's sign-in in the login history", async () => {
  await nav(owner.page, "Setup");
  await owner.page.getByRole("tab", { name: "Security" }).click();
  await owner.page.getByTestId("login-table").waitFor();
  const t = await owner.page.getByTestId("login-table").innerText();
  if (!/admin password/.test(t) || !/Success/.test(t)) throw new Error(t.slice(0, 200));
  await owner.page.getByTestId("two-step-policy").getByText("Not covered: the server admin password").waitFor();
});

const ann = await session();
await step("Ann turns on two-step login from Setup > My password and gets 8 recovery codes", async () => {
  await password(ann.page, ANN.email, ANN.password);
  await intoApp(ann.page);
  await nav(ann.page, "Setup");
  await ann.page.getByRole("tab", { name: "My password" }).click();
  await ann.page.getByRole("button", { name: "Set up two-step login" }).click();
  secret = await keyText(ann.page);
  if (secret.length !== 32) throw new Error(`key ${secret}`);
  await ann.page.getByLabel("3. Type the 6-digit code the app shows").fill(totp(secret));
  await ann.page.getByRole("button", { name: "Turn on" }).click();
  await ann.page.getByTestId("recovery-codes").waitFor();
  if ((await ann.page.getByTestId("recovery-codes").locator("li").count()) !== 8) throw new Error("not 8 codes");
  await ann.page.getByTestId("two-step-card").getByText("On", { exact: true }).waitFor();
});
await step("Signing in again asks for the code; a wrong code is refused, the right one lets her in", async () => {
  await ann.ctx.clearCookies();
  await ann.page.evaluate(() => sessionStorage.clear());
  await password(ann.page, ANN.email, ANN.password);
  await ann.page.getByTestId("login-two-step").waitFor();
  await ann.page.getByLabel("Code", { exact: true }).fill("000000" === totp(secret, 30) ? "111111" : "000000");
  await ann.page.getByRole("button", { name: "Sign in" }).click();
  await ann.page.getByText("That code is not right").waitFor();
  await ann.page.getByLabel("Code", { exact: true }).fill(totp(secret, 30));      // the next step: the setup code was already used
  await ann.page.getByRole("button", { name: "Sign in" }).click();
  await intoApp(ann.page);
});
await step("Owner sees Ann's sign-ins (failed code, two-step yes), requires two-step for everyone and resets Ann's", async () => {
  await owner.page.getByRole("tab", { name: "Audit" }).click();
  await owner.page.getByRole("tab", { name: "Security" }).click();
  await owner.page.getByTestId("login-table").waitFor();
  const t = await owner.page.getByTestId("login-table").innerText();
  if (!/wrong two-step code/.test(t) || !/Waiting for code/.test(t)) throw new Error(t.slice(0, 400));
  const policy = owner.page.getByTestId("two-step-policy");
  await policy.getByText("Two-step on").waitFor();
  await owner.page.getByTestId("require-two-step").click();
  await owner.page.getByRole("button", { name: "Require it" }).click();
  await policy.getByText("Two-step login is now required.").waitFor();
  await policy.getByRole("button", { name: "Reset two-step" }).click();
  await owner.page.getByRole("button", { name: "Reset", exact: true }).click();
  await policy.getByText(`Two-step login reset for ${ANN.email}.`).waitFor();
  await owner.page.screenshot({ path: `${SHOTS}/security-tab.png`, fullPage: true });
});
await step("With two-step required, Ann (reset) sets it up again while signing in", async () => {
  await ann.page.evaluate(() => sessionStorage.clear());
  await password(ann.page, ANN.email, ANN.password);
  await ann.page.getByRole("heading", { name: "Set up two-step login" }).waitFor();
  await ann.page.getByTestId("two-step-key").waitFor();
  const fresh = await keyText(ann.page);
  await ann.page.getByLabel("Code the app shows").fill(totp(fresh));
  await ann.page.getByRole("button", { name: "Turn on and sign in" }).click();
  await ann.page.getByTestId("recovery-codes").waitFor();
  await ann.page.waitForTimeout(700);                                         // the card's rise-in animation
  await ann.page.screenshot({ path: `${SHOTS}/security-login-setup.png` });
  await ann.page.getByRole("button", { name: "I saved them, continue" }).click();
  await intoApp(ann.page);
  await nav(ann.page, "Setup");
  if (await ann.page.getByRole("tab", { name: "Security" }).count()) throw new Error("a caller sees the Security tab");
});
for (const s of [owner, ann]) if (s.errors.length) results.push([false, `page errors: ${s.errors.join(" | ").slice(0, 300)}`]);

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
