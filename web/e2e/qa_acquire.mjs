// QA sweep "acquire": browser regressions that need no outside website.
//   E2E_UI=http://localhost:5281 E2E_PASSWORD=... node e2e/qa_acquire.mjs   (API started with OUTREACH_ADMIN_PASSWORD)
import { chromium } from "playwright";

const UI = process.env.E2E_UI || "http://localhost:5173";
const PASSWORD = process.env.E2E_PASSWORD || "e2e-password";
const results = [];
async function step(name, fn) {
  try { await fn(); results.push([true, name]); } catch (e) { results.push([false, `${name} — ${e.message.split("\n")[0]}`]); }
}
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
await page.goto(UI);
await page.getByLabel("Password").fill(PASSWORD);
await page.getByRole("button", { name: "Sign in" }).click();
await page.locator("nav.side").waitFor();
const skip = page.getByRole("button", { name: "Skip setup" });
if (await skip.count()) await skip.click();
await page.goto(`${UI}/#leads`);

await step("Import wizard refuses a CSV with an unclosed quote instead of making one junk lead", async () => {
  await page.getByRole("button", { name: "Import leads" }).click();
  await page.locator("#import-file").setInputFiles({ name: "broken.csv", mimeType: "text/csv",
    buffer: Buffer.from('company,email,phone\n"Unclosed Quote Ltd,ceo@unclosed.in,9876500000\nGood Ltd,hello@good.in,9876500001\n') });
  await page.getByRole("button", { name: "Next: check the columns" }).click();
  await page.getByText("opens a quote (\") that is never closed").first().waitFor();
  await page.getByRole("button", { name: "Import leads" }).click();
});
await step("Find contacts accepts a typed domain without https://", async () => {
  await page.getByRole("button", { name: "Find contacts on a website" }).click();
  await page.getByPlaceholder("https://company.com/contact").fill("qa-nowhere.invalid/contact");
  await page.getByRole("button", { name: "Find & add" }).click();
  await page.getByText("Couldn't fetch that page.").waitFor();   // read as https://..., not refused as "Only http/https"
});
await step("A jobs CSV whose first column is not company is still read as CSV", async () => {
  await page.getByRole("button", { name: "Read job pages" }).click();
  await page.getByPlaceholder("Optional: paste page HTML or CSV here").fill(
    "title,company,location,date_posted,website\nPicker,Deccan Couriers QA,Pune,2026-11-01,https://deccancouriers-qa.in\n");
  await page.getByRole("button", { name: "Read jobs", exact: true }).click();
  await page.getByText("Read 1 job(s)").waitFor();
});

for (const [ok, name] of results) console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
await browser.close();
process.exit(results.every(([ok]) => ok) ? 0 : 1);
