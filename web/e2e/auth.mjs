// Login screen check. Run with the API started with OUTREACH_ADMIN_PASSWORD=e2e-password.
import { chromium } from "playwright";

const UI = "http://localhost:5173";
const PASSWORD = process.env.E2E_PASSWORD || "e2e-password";
const results = [];
let page;

async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}

const browser = await chromium.launch();
page = await (await browser.newContext()).newPage();

await step("Signed-out visitors see only the login form", async () => {
  await page.goto(UI);
  await page.getByLabel("Password").waitFor();
  if ((await page.getByText("Dashboard").count()) > 0) throw new Error("app content visible before login");
});
await step("Wrong password shows an error and stays on login", async () => {
  await page.getByLabel("Password").fill("nope");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Wrong password").waitFor();
});
await step("Right password opens the app", async () => {
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("heading", { name: "Dashboard" }).waitFor();
});
await step("Reload keeps the session", async () => {
  await page.reload();
  await page.getByRole("heading", { name: "Dashboard" }).waitFor();
});
await step("Sign out returns to login and API calls are refused", async () => {
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Password").waitFor();
  const r = await fetch("http://localhost:8000/api/leads");
  if (r.status !== 401) throw new Error(`API answered ${r.status}`);
});
await step("Public pages stay reachable without login", async () => {
  const r = await fetch("http://localhost:8000/demo/not-a-token");
  if (r.status !== 404) throw new Error(`demo answered ${r.status}`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
