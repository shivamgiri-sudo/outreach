// Login screen check. Run with the API started with OUTREACH_ADMIN_PASSWORD=e2e-password.
import { chromium } from "playwright";

const UI = "http://localhost:5173";
const API = "http://localhost:8000";
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
await step("Owner adds a caller login on the Setup page", async () => {
  await page.getByRole("button", { name: "Setup" }).first().click();
  await page.getByRole("tab", { name: "Connections" }).click();
  const card = page.getByTestId("users-card");
  await card.getByLabel("Email").fill("caller@e2e.invalid");
  await card.getByLabel("Name").fill("Priya");
  await card.getByLabel("Role").selectOption("caller");
  await card.getByLabel("Password (10+ characters)").fill("caller-password-1");
  await card.getByRole("button", { name: "Add login" }).click();
  await card.getByText("Login added for caller@e2e.invalid").waitFor();
});
await step("Sign out returns to login, API calls are refused and the old token is revoked", async () => {
  const token = await page.evaluate(() => sessionStorage.getItem("outreach-token"));
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Password").waitFor();
  const r = await fetch(`${API}/api/leads`);
  if (r.status !== 401) throw new Error(`API answered ${r.status}`);
  const old = await fetch(`${API}/api/leads`, { headers: { Authorization: `Bearer ${token}` } });
  if (old.status !== 401) throw new Error(`revoked token answered ${old.status}`);
});
await step("Wrong email or password shows an error", async () => {
  await page.getByLabel("Email (team login, optional)").fill("caller@e2e.invalid");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByText("Wrong email or password").waitFor();
});
await step("A caller signs in with email; settings stay owner-only", async () => {
  await page.getByLabel("Password").fill("caller-password-1");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("button", { name: "Sign out" }).waitFor();          // reopens the last page (Setup)
  await page.getByRole("button", { name: "Setup" }).first().click();
  await page.getByRole("tab", { name: "Connections" }).click();
  await page.getByText("Integrations page").first().waitFor();
  if (await page.getByTestId("users-card").count()) throw new Error("caller sees the Team logins card");
  const token = await page.evaluate(() => sessionStorage.getItem("outreach-token"));
  const r = await fetch(`${API}/api/settings`, { method: "PUT", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ daily_cap: 10 }) });
  if (r.status !== 403) throw new Error(`caller changed settings: ${r.status}`);
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Password").waitFor();
});
await step("Public pages stay reachable without login", async () => {
  const r = await fetch(`${API}/demo/not-a-token`);
  if (r.status !== 404) throw new Error(`demo answered ${r.status}`);
});

await browser.close();
for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
const failed = results.filter(([ok]) => !ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
