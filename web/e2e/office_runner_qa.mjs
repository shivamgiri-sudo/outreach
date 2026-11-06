// Office runner QA, through the real web UI (Playwright) at 1400 px and 375 px. Every portal action is done the way the
// owner does it: sign in on the login page, LinkedIn outreach page, Connect an office computer, copy the line, add the
// LinkedIn accounts, type email / password / code into the portal forms, switch on / off, where it runs, disconnect.
// Non-UI actions are only the office computer's side (running tools/linkedin_runner.mjs with the code from the copied
// line, killing it / its browser), the LinkedIn side (the stand-in: tapping Yes in the LinkedIn app, profile pages),
// stopping / starting the portal process (outage, restart) and reading data back to verify.
//
//   cd web && node e2e/office_runner_qa.mjs          (QA_WIDTHS=1400 or 375 to run one width; HEADED=1 to watch)
//
// Ports: API 8075, LinkedIn stand-in 8076, UI (built, static + /api proxy) 5275. Nothing here opens linkedin.com: the
// stand-in's address is passed to the runner (--linkedin-base) and the run fails if any log line shows a linkedin.com
// request or the stand-in did not see the agents' traffic.
import { chromium } from "playwright";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ROOT = path.resolve(WEB, "..");
const API = "http://127.0.0.1:8075", MOCK = "http://127.0.0.1:8076", UI = "http://127.0.0.1:5275";
const PASSWORD = "office-qa-pass-2026";
const PY = path.join(ROOT, ".venv", "bin", "python");
const WORK = fs.mkdtempSync(path.join(os.tmpdir(), "office-runner-qa-"));
const SHOTS = process.env.SHOT_DIR || path.join(WORK, "shots");
const DATA = path.join(WORK, "store.json"), CLOUD = path.join(WORK, "cloud");
const ACCTS = { A: ["asha@example.com", "Asha-Pass-1", "Asha Sender"], B: ["bala@example.com", "Bala-Pass-2", "Bala Sender"],
  C: ["chitra@example.com", "Chitra-Pass-3", "Chitra Sender"] };
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
const results = [];
const procs = [];
const logs = {};

// ---------------------------------------------------------------- processes
function run(name, cmd, args, opts = {}) {
  const file = path.join(WORK, `${name}.log`);
  const out = fs.openSync(file, "a");
  const p = spawn(cmd, args, { cwd: ROOT, stdio: ["ignore", out, out], ...opts });
  p.logFile = file;
  procs.push(p);
  logs[name] = file;
  return p;
}
const readLog = (name) => { try { return fs.readFileSync(logs[name], "utf8"); } catch { return ""; } };
const exited = (p) => new Promise((r) => (p.exitCode !== null || p.signalCode !== null ? r() : p.once("exit", r)));
async function until(fn, ms, what) {
  const end = Date.now() + ms;
  let last;
  while (Date.now() < end) { try { last = await fn(); if (last) return last; } catch (e) { last = e.message; } await wait(500); }
  throw new Error(`timed out (${Math.round(ms / 1000)} s) waiting for ${what} (last: ${String(JSON.stringify(last)).slice(0, 200)})`);
}
const alive = (pid) => { try { process.kill(pid, 0); return !fs.readFileSync(`/proc/${pid}/stat`, "utf8").split(")").pop().trim().startsWith("Z"); } catch { return false; } };
const kids = (pid) => spawnSync("pgrep", ["-P", String(pid)], { encoding: "utf8" }).stdout.split(/\s+/).filter(Boolean).map(Number);

let api = null;
async function startApi() {
  const env = { ...process.env, OUTREACH_ADMIN_PASSWORD: PASSWORD, OUTREACH_DATA_FILE: DATA, OUTREACH_CLOUD_LI_DIR: CLOUD,
    OUTREACH_OFFICE_LI_MAX: "3", OUTREACH_BASE_URL: API, OUTREACH_IGNORE_BUSINESS_HOURS: "1" };
  for (const k of ["OUTREACH_AUTORUN", "OUTREACH_TEST_MODE", "OUTREACH_CLOUD_LI_MAX", "OUTREACH_RUNNER_ALLOW_HTTP"]) delete env[k];
  api = run("api", PY, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8075", "--no-access-log"], { env });
  await until(async () => (await fetch(`${API}/api/auth/status`)).ok, 30000, "the API");
}
async function stopApi() { api.kill("SIGTERM"); await exited(api); }

const RUNNER_ARGS = ["--headless", "--interval", "2", "--heartbeat", "2", "--backoff-base", "1", "--stop-grace", "3",
  "--linkedin-base", MOCK, "--agent-args=--poll 1 --settle 300 --login-poll 1"];
function startRunner(name, home, line) {
  const args = [path.join(ROOT, "tools", "linkedin_runner.mjs"), "--home", home, ...RUNNER_ARGS];
  if (line) {                                      // the line copied from the portal: its address and one-time code
    const m = line.match(/'(https?:\/\/[^']+)\/api\/linkedin-runner\/install\.(sh|ps1)\?code=([A-Z0-9]{8})'/);
    if (!m) throw new Error(`cannot read the copied line: ${line}`);
    args.push("--portal", m[1], "--code", m[3]);
  }
  return run(name, "node", args);
}
const agentPids = (name, slot) => [...readLog(name).matchAll(new RegExp(`started agent for ${slot} \\([^)]*\\), pid (\\d+)`, "g"))].map((m) => Number(m[1]));
const mock = async (p) => (await fetch(MOCK + p)).json();

// read-only views of the portal data, to verify (never to change anything)
let ownerToken = "";
const read = async (p) => (await fetch(API + p, { headers: { Authorization: `Bearer ${ownerToken}` } })).json();
const cloudView = () => read("/api/linkedin-cloud");
const slotByLabel = async (label) => (await cloudView()).slots.find((s) => s.label === label);

// ---------------------------------------------------------------- one width
async function width(browser, w, h, full) {
  fs.mkdirSync(path.join(SHOTS, String(w)), { recursive: true });
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: UI });
  ctx.setDefaultTimeout(20000);
  const page = await ctx.newPage();
  const problems = [];
  let current = "";
  page.on("pageerror", (e) => problems.push(`[${current}] page error: ${e.message}`));
  page.on("console", (m) => { if (m.type() === "error" && !/Failed to load resource|ERR_CONNECTION_REFUSED|502/.test(m.text())) problems.push(`[${current}] console: ${m.text()}`); });
  page.on("response", async (r) => {
    if (!r.url().includes("/api/")) return;
    const body = await r.text().catch(() => "");
    if (/mcnr_[A-Za-z0-9_-]{10}|mcnx_[A-Za-z0-9_-]{10}/.test(body)) problems.push(`[${current}] a token reached the browser in ${r.url()}`);
  });
  let n = 0;
  const step = async (name, fn) => {
    current = name; n += 1;
    const t0 = Date.now();
    let ok = true, note = "";
    try { note = (await fn()) || ""; } catch (e) { ok = false; note = e.message.split("\n")[0]; }
    const html = await page.content().catch(() => "");
    if (/mcnr_|mcnx_/.test(html)) { ok = false; note += " | a token is visible in the page"; }
    const mine = problems.filter((p) => p.startsWith(`[${name}]`));
    if (mine.length) { ok = false; note += ` | ${mine.join("; ").slice(0, 300)}`; }
    results.push([`${w}px ${name}`, ok, note, Math.round((Date.now() - t0) / 1000)]);
    console.log(`${ok ? "PASS" : "FAIL"} ${w}px ${name} (${Math.round((Date.now() - t0) / 1000)} s)${note ? ` — ${note}` : ""}`);
    await page.screenshot({ path: path.join(SHOTS, String(w), `${String(n).padStart(2, "0")}-${name.replace(/[^a-z0-9]+/gi, "-").slice(0, 60)}.png`), fullPage: true }).catch(() => {});
    if (!ok && process.env.QA_STOP_ON_FAIL) throw new Error("stop");
    return ok;
  };
  const nav = (label) => page.getByRole("button", { name: label, exact: true }).first().click();
  const see = (t, timeout = 15000) => page.getByText(t, { exact: false }).first().waitFor({ timeout });
  const card = () => page.getByTestId("office-runner");
  const slotCard = (label) => page.getByTestId("cloud-slot").filter({ has: page.locator("b", { hasText: new RegExp(`^${label}$`) }) });
  const slotState = (label) => slotCard(label).getByTestId("cloud-state").innerText();
  const okDialog = async () => { await page.getByTestId("dialog-ok").click(); };
  const signIn = async () => {
    await page.goto(UI);
    await page.getByLabel("Password").waitFor();
    await page.getByLabel("Password").fill(PASSWORD);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("button", { name: "Sign out" }).waitFor();
    ownerToken = await page.evaluate(() => sessionStorage.getItem("outreach-token"));
    const later = page.getByRole("button", { name: "Later", exact: true });          // first-run setup wizard
    if (await later.waitFor({ timeout: 4000 }).then(() => true, () => false)) await later.click();
  };
  const openLinkedIn = async () => {
    if (w < 700 && await page.getByRole("button", { name: /menu/i }).count()) await page.getByRole("button", { name: /menu/i }).first().click().catch(() => {});
    await nav("LinkedIn outreach");
    await page.getByTestId("office-runner").waitFor();
  };
  const waitState = (label, re, ms = 90000) => until(async () => re.test(await slotState(label)) && slotState(label), ms, `${label}: ${re}`);
  const loginForm = async (label, [email, pw]) => {
    const c = slotCard(label);
    if (!(await c.getByTestId("cloud-login-form").count())) await c.getByRole("button", { name: "Log in", exact: true }).click();
    await c.getByTestId("cloud-email").fill(email);
    await c.getByTestId("cloud-password").fill(pw);
    await c.getByTestId("cloud-login-form").getByRole("button", { name: "Log in" }).click();
  };
  const copyLine = async (system = "Linux") => {
    await card().getByRole("tab", { name: system }).click();
    const shown = await card().getByTestId("runner-command").innerText();
    await card().getByTestId("runner-steps").getByRole("button", { name: "Copy" }).click();
    await card().getByRole("button", { name: "Copied" }).waitFor();
    const copied = await page.evaluate(() => navigator.clipboard.readText());
    if (copied !== shown) throw new Error("the copied line differs from the one shown");
    return copied;
  };

  await step("Sign in on the login page (a wrong password first)", async () => {
    await page.goto(UI);
    await page.getByLabel("Password").fill("not-the-password");
    await page.getByRole("button", { name: "Sign in" }).click();
    await see("Wrong password");
    problems.length = 0;                                   // the 401 of the wrong password is expected
    await signIn();
  });
  if (!full) {
    await step("Phone: LinkedIn page, office card, connect flow, copy line, cancel, uninstall lines", async () => {
      await openLinkedIn();
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      if (sw > w + 1) throw new Error(`horizontal scroll: page is ${sw} px wide at ${w} px`);
      const status = await card().getByTestId("runner-status").innerText();
      await card().getByTestId("runner-connect").click();
      if (/online|offline/i.test(status)) await okDialog();
      const line = await copyLine("Windows");
      if (!line.startsWith("powershell") || !/code=[A-Z0-9]{8}/.test(line)) throw new Error(`windows line: ${line}`);
      const linux = await copyLine("Linux");
      if (!linux.startsWith("curl -fsSL")) throw new Error(`linux line: ${linux}`);
      const box = await card().getByTestId("runner-command").boundingBox();
      if (box.x + box.width > w) throw new Error("the command runs off the screen");
      const sw2 = await page.evaluate(() => document.documentElement.scrollWidth);
      if (sw2 > w + 1) throw new Error(`horizontal scroll with the steps open: ${sw2} px`);
      await card().getByRole("button", { name: "Cancel" }).click();
      await card().getByTestId("runner-steps").waitFor({ state: "detached" });
      await card().getByText("How to remove it from the office computer").click();
      for (const t of ["uninstall.ps1", "MCN-LinkedIn-Runner/uninstall.sh", "mcn-linkedin-runner/uninstall.sh"]) await card().getByText(t, { exact: false }).first().waitFor();
      return `office card fits ${w} px; ${status}`;
    });
    await ctx.close();
    return;
  }

  // ======================= the owner prepares people to contact (through the UI) =======================
  await step("Import three people with LinkedIn links (pasted list) and start their LinkedIn sequences", async () => {
    for (const [slug, name] of [["ravi-example", "Ravi Kumar"], ["meera-example", "Meera Rao"]]) await mock(`/_qa/install?slug=${slug}&name=${encodeURIComponent(name)}`);
    await nav("Leads");
    await page.getByRole("button", { name: "Import leads" }).click();
    await page.locator("#import-paste").fill("company,contact_name,title,email,linkedin_url\n"
      + "Blocked Co,Bina Shah,HR Head,bina@blocked.example,https://www.linkedin.com/in/blocked\n"
      + "Acme Pvt Ltd,Ravi Kumar,Head of Talent Acquisition,ravi@acme.example,https://www.linkedin.com/in/ravi-example\n"
      + "Globex,Meera Rao,Founder,meera@globex.example,https://www.linkedin.com/in/meera-example\n");
    await page.getByRole("button", { name: "Next: check the columns" }).click();
    await page.getByRole("button", { name: "Next: dry run" }).click();
    await page.getByRole("button", { name: /^Import: 3 new/ }).click();
    await see("Done. Added 3 new lead(s)");
    await page.getByRole("button", { name: "Close", exact: true }).last().click();
    await openLinkedIn();
    for (const who of ["Bina Shah", "Ravi Kumar", "Meera Rao"]) {
      await page.locator(".task-list li", { hasText: who }).getByRole("button", { name: "Start LinkedIn sequence" }).click();
      await see("Added. Step 1 is in today's list.");
    }
    const agent = page.getByTestId("linkedin-agent");
    if (await agent.getByRole("button", { name: "Switch on" }).count()) await agent.getByRole("button", { name: "Switch on" }).click();
    await agent.getByText("Limits", { exact: true }).click();
    await agent.getByLabel("Start hour (India time)").fill("0");
    await agent.getByLabel("End hour (India time)").fill("24");
    await agent.getByLabel("Minutes between two actions").fill("3");
    const wd = agent.getByLabel("Monday to Friday only");
    if (await wd.isChecked()) await wd.uncheck();
    await agent.getByRole("button", { name: "Save limits" }).click();
    await see("Limits saved.");
    const leads = await read("/api/leads");
    if (leads.filter((l) => l.linkedin_seq).length !== 3) throw new Error("three sequences expected");
  });

  // ======================= connect the office computer =======================
  let home1 = path.join(WORK, "office-pc"), runner1 = null, line1 = "";
  await step("Connect an office computer: steps, Linux/Mac/Windows lines, copy, insecure-address note", async () => {
    await openLinkedIn();
    if (await card().getByTestId("runner-status").innerText() !== "Not connected") throw new Error("expected Not connected");
    await card().getByTestId("runner-connect").click();
    await card().getByTestId("runner-steps").waitFor();
    const win = await copyLine("Windows"), mac = await copyLine("Mac");
    line1 = await copyLine("Linux");
    const code = line1.match(/code=([A-Z0-9]{8})/)[1];
    if (!win.includes(`code=${code}`) || !mac.includes(`code=${code}`) || !win.includes("install.ps1") || !mac.includes("install.sh")) throw new Error("lines disagree");
    await card().getByText("Works once, for").waitFor();
    await card().getByText("The portal address is not https://").waitFor();     // OUTREACH_BASE_URL is http here (local test)
    if (await card().getByTestId("runner-wait").innerText() !== "Waiting for the office computer… (this page checks by itself)") throw new Error("wait text");
    return `code ${code.slice(0, 2)}…`;
  });
  await step("Run the copied line's runner on the office computer: badge turns online by itself", async () => {
    runner1 = startRunner("runner1", home1, line1);
    await card().getByTestId("runner-status").filter({ hasText: "Office computer online" }).waitFor({ timeout: 30000 });
    await card().getByTestId("runner-steps").waitFor({ state: "detached", timeout: 15000 });
    const details = await card().getByTestId("runner-details").innerText();
    if (!details.includes(os.hostname().slice(0, 20))) throw new Error(`details: ${details}`);
    const cfg = JSON.parse(fs.readFileSync(path.join(home1, "config.json"), "utf8"));
    if (!cfg.token.startsWith("mcnr_") || (fs.statSync(path.join(home1, "config.json")).mode & 0o777) !== 0o600) throw new Error("config.json");
    if (fs.readFileSync(DATA, "utf8").includes(cfg.token)) throw new Error("raw runner token in the data file");
    return details;
  });
  await step("The same line cannot be used twice; a second runner copy on this computer is refused", async () => {
    const again = spawnSync("node", [path.join(ROOT, "tools/linkedin_runner.mjs"), "--home", path.join(WORK, "other"), "--portal", API,
      "--code", line1.match(/code=([A-Z0-9]{8})/)[1], "--pair-only"], { encoding: "utf8", timeout: 30000 });
    if (again.status !== 2 || !/wrong, already used or expired/.test(again.stdout)) throw new Error(`reuse: ${again.status} ${again.stdout}`);
    const second = spawnSync("node", [path.join(ROOT, "tools/linkedin_runner.mjs"), "--home", home1], { encoding: "utf8", timeout: 30000 });
    if (second.status !== 0 || !/already running/.test(second.stdout)) throw new Error(`second copy: ${second.stdout}`);
  });

  // ======================= three LinkedIn accounts, each logged in from the portal =======================
  await step("Add three LinkedIn accounts (the most the office computer runs)", async () => {
    for (const label of ["Asha", "Bala", "Chitra"]) {
      await page.getByLabel("Name for the LinkedIn account").fill(label);
      await page.getByTestId("cloud-add").click();
      await see("LinkedIn account added. Log it in below.");
      await slotCard(label).waitFor();
    }
    await see("The office computer runs 3 LinkedIn accounts at most");
    for (const label of ["Asha", "Bala", "Chitra"]) await waitState(label, /Needs login/, 90000);
  });
  await step("Asha: a wrong password is refused, then the right one asks for a code typed in the portal", async () => {
    await loginForm("Asha", [ACCTS.A[0], "wrong-password"]);
    await until(async () => /did not accept/.test(await slotCard("Asha").innerText()), 60000, "the refusal");
    await loginForm("Asha", ACCTS.A);
    await waitState("Asha", /Enter the code/);
    await slotCard("Asha").getByTestId("cloud-code").fill("246810");
    await slotCard("Asha").getByRole("button", { name: "Send code" }).click();
    await waitState("Asha", /Connected as Asha Sender|Paused/);          // it may meet the security-check person first
    if ((await slotByLabel("Asha")).account !== "asha-sender") throw new Error("Asha's LinkedIn account");
  });
  await step("Bala: approve-in-the-app path (the owner taps Yes on the phone)", async () => {
    await loginForm("Bala", ACCTS.B);
    await waitState("Bala", /Approve the sign-in/);
    await slotCard("Bala").getByText("tap Yes", { exact: false }).waitFor();
    await mock("/_qa/approve");                            // LinkedIn's side: the phone approves
    await waitState("Bala", /Connected as Bala Sender|Paused/);
    if ((await slotByLabel("Bala")).account !== "bala-sender") throw new Error("Bala's LinkedIn account");
  });
  await step("Chitra: a CAPTCHA stops the login and asks for a person; then removed", async () => {
    await loginForm("Chitra", ACCTS.C);
    await waitState("Chitra", /Needs a person/);
    await slotCard("Chitra").getByText("CAPTCHA", { exact: false }).waitFor();
    await slotCard("Chitra").getByRole("button", { name: "Remove" }).click();
    await okDialog();
    await slotCard("Chitra").waitFor({ state: "detached" });
    await until(() => !fs.existsSync(path.join(home1, "profiles")) || fs.readdirSync(path.join(home1, "profiles")).length === 2, 20000, "its profile deleted");
  });
  await step("Two accounts work side by side: no person is contacted by both; a security check pauses only that account", async () => {
    const leads0 = await read("/api/leads");
    const ids = Object.fromEntries(leads0.map((l) => [l.linkedin_url.split("/in/")[1], l.id]));
    const done = await until(async () => {
      const ev = (await mock("/_qa/events")).filter((e) => e.kind === "invite");
      const ag = await read("/api/linkedin-agent");
      const paused = (ag.accounts || []).filter((a) => a.paused);
      return ev.length + paused.length >= 2 && { ev, paused, ag };
    }, 150000, "two first actions (invites or a safety pause)");
    const leads = await read("/api/leads");
    const claim = Object.fromEntries(leads.map((l) => [l.linkedin_url.split("/in/")[1], (l.linkedin_seq || {}).account || ""]));
    const slugs = done.ev.map((e) => (e.note || "").startsWith("Hi Ravi") ? "ravi-example" : (e.note || "").startsWith("Hi Meera") ? "meera-example" : "?");
    if (new Set(slugs).size !== slugs.length) throw new Error(`a person was invited twice: ${slugs}`);
    for (const [i, e] of done.ev.entries()) if (claim[slugs[i]] !== e.by) throw new Error(`${slugs[i]} claimed by ${claim[slugs[i]]} but invited by ${e.by}`);
    if (done.paused.length > 1) throw new Error("both accounts paused");
    if (done.paused.length === 1) {
      const p = done.paused[0].account, other = p === "asha-sender" ? "Bala" : "Asha";
      if (claim.blocked !== p) throw new Error(`paused ${p} but the security-check person belongs to ${claim.blocked}`);
      await waitState(p === "asha-sender" ? "Asha" : "Bala", /Paused/, 30000);
      if (!/Connected/.test(await slotState(other))) throw new Error(`${other} should keep running`);
      await page.getByTestId("linkedin-agent").getByText("Paused:", { exact: false }).first().waitFor();
    }
    return `invites ${slugs.join(",")} by ${done.ev.map((e) => e.by).join(",")}; paused ${done.paused.map((a) => a.account).join(",") || "none yet"}; ids ${Object.keys(ids).length}`;
  });
  await step("Switch Asha off and on: only Asha's browser stops and starts", async () => {
    const asha = await slotByLabel("Asha"), bala = await slotByLabel("Bala");
    const balaPid = agentPids("runner1", bala.id).at(-1);
    await slotCard("Asha").getByRole("button", { name: "Switch off" }).click();
    await see("Asha switched off.");
    await until(() => readLog("runner1").includes(`stopping agent for ${asha.id}: switched off in the portal`), 20000, "Asha stopped");
    await waitState("Asha", /Stopped/, 20000);
    if (!alive(balaPid)) throw new Error("Bala's agent stopped too");
    await slotCard("Asha").getByRole("button", { name: "Switch on" }).click();
    await until(() => agentPids("runner1", asha.id).length >= 2, 20000, "Asha started again");
    await waitState("Asha", /Connected as Asha|Paused/, 60000);
    if (!alive(balaPid)) throw new Error("Bala's agent restarted");
  });

  // ======================= where it runs =======================
  await step("Office → server: the office computer stops its browsers and keeps the sessions; the server supervisor takes over", async () => {
    const before = (await cloudView()).slots.map((s) => s.id);
    const sup = run("supervisor", "node", [path.join(ROOT, "tools/linkedin_cloud_supervisor.mjs"), "--dir", CLOUD, "--portal", API, "--interval", "1",
      "--max", "3", "--agent", path.join(ROOT, "tests/agent_sim/fake_cloud_agent.mjs")]);
    await wait(3000);
    if (fs.existsSync(path.join(CLOUD, "events.log")) && /start /.test(fs.readFileSync(path.join(CLOUD, "events.log"), "utf8"))) throw new Error("the server supervisor ran an agent in office mode");
    const slotsFile = JSON.parse(fs.readFileSync(path.join(CLOUD, "slots.json"), "utf8"));
    if (slotsFile.slots.some((s) => s.enabled)) throw new Error("slots.json has enabled slots in office mode");
    await page.getByTestId("cloud-where").getByRole("radio", { name: "Portal server" }).click();
    await okDialog();
    await see("LinkedIn now runs on the server.");
    await until(() => before.every((id) => readLog("runner1").includes(`stopping agent for ${id}: switched off in the portal`)), 20000, "office agents stopped");
    for (const id of before) if (!fs.existsSync(path.join(home1, "profiles", id))) throw new Error(`profile ${id} deleted`);
    await until(() => fs.existsSync(path.join(CLOUD, "events.log")) && (fs.readFileSync(path.join(CLOUD, "events.log"), "utf8").match(/start /g) || []).length >= 1, 20000, "the server supervisor starting agents");
    await page.getByText("LinkedIn blocks logins from our cloud server").first().waitFor();
    sup.slotsBefore = before;
    globalThis.supervisor = sup;
  });
  await step("Server → office: the server idles again, the office computer restarts both accounts already logged in", async () => {
    const attempts = (await mock("/_qa/login")).attempts.length;
    await page.getByTestId("cloud-where").getByRole("radio", { name: /Office computer/ }).click();
    await okDialog();
    await see("LinkedIn now runs on the office computer.");
    await until(() => /stop /.test(fs.readFileSync(path.join(CLOUD, "events.log"), "utf8")), 20000, "the server supervisor stopping its agents");
    await waitState("Asha", /Connected as Asha|Paused/, 90000);
    await waitState("Bala", /Connected as Bala|Paused/, 90000);
    if ((await mock("/_qa/login")).attempts.length !== attempts) throw new Error("the accounts had to log in again (sessions lost)");
    globalThis.supervisor.kill("SIGTERM");
  });

  // ======================= resilience =======================
  await step("The LinkedIn window is closed (browser killed): a new window opens and the account is connected again", async () => {
    const asha = await slotByLabel("Asha");
    const pid = agentPids("runner1", asha.id).at(-1);
    const browser = await until(() => kids(pid).length && kids(pid), 30000, "the agent's browser process");
    for (const b of browser) process.kill(b, "SIGKILL");
    await until(() => agentPids("runner1", asha.id).at(-1) !== pid, 30000, "a new agent");
    await waitState("Asha", /Connected as Asha|Paused/, 60000);
  });
  await step("Portal down for 2 minutes: the runner backs off, then comes back online by itself", async () => {
    await stopApi();
    const t0 = Date.now();
    await wait(120000);
    const out = readLog("runner1");
    const tries = (out.match(/Could not reach the portal/g) || []).length;
    if (tries < 3 || tries > 30) throw new Error(`${tries} retries in 2 minutes (backoff broken?)`);
    if (!/trying again in (30|60) s/.test(out)) throw new Error("the wait never grew");
    await startApi();
    await until(() => readLog("runner1").includes("The portal answers again"), 70000, "the runner to reconnect");
    await page.reload();
    await openLinkedIn().catch(async () => { await signIn(); await openLinkedIn(); });
    await card().getByTestId("runner-status").filter({ hasText: "Office computer online" }).waitFor({ timeout: 30000 });
    if (runner1.exitCode !== null) throw new Error("the runner exited");
    return `${tries} retries over ${Math.round((Date.now() - t0) / 1000)} s`;
  });
  await step("Network drop mid-login: the portal goes away while LinkedIn waits for a code; the code typed afterwards works", async () => {
    await slotCard("Asha").getByRole("button", { name: "Log out" }).click();
    await okDialog();
    await waitState("Asha", /Needs login/, 60000);
    await loginForm("Asha", ACCTS.A);
    await waitState("Asha", /Enter the code/);
    await stopApi();
    await wait(20000);
    await startApi();
    await page.reload();
    await openLinkedIn().catch(async () => { await signIn(); await openLinkedIn(); });
    await waitState("Asha", /Enter the code/, 30000);
    await slotCard("Asha").getByTestId("cloud-code").fill("246810");
    await slotCard("Asha").getByRole("button", { name: "Send code" }).click();
    await waitState("Asha", /Connected as Asha|Paused/, 90000);           // still paused by the earlier security check
    if ((await slotByLabel("Asha")).account !== "asha-sender") throw new Error("not logged in after the drop");
  });
  await step("Portal restart during a login: the lost login shows Log in again (no endless Logging in…)", async () => {
    await slotCard("Bala").getByRole("button", { name: "Log out" }).click();
    await okDialog();
    await waitState("Bala", /Needs login/, 60000);
    runner1.kill("SIGTERM");                                 // the agent cannot take the job before the restart
    await exited(runner1);
    await loginForm("Bala", ACCTS.B);
    await waitState("Bala", /Logging in/, 10000);
    await wait(1500);
    await stopApi();
    await startApi();
    await page.reload();
    await openLinkedIn().catch(async () => { await signIn(); await openLinkedIn(); });
    await waitState("Bala", /Needs login/, 15000);
    await slotCard("Bala").getByText("Log in again", { exact: false }).first().waitFor();
    await slotCard("Bala").getByTestId("cloud-login-form").waitFor();
    runner1 = startRunner("runner1b", home1);
    logs.runner1 = logs.runner1b;
    await loginForm("Bala", ACCTS.B);
    await waitState("Bala", /Approve the sign-in|Connected as Bala/, 90000);
    await mock("/_qa/approve");
    await waitState("Bala", /Connected as Bala/, 90000);
  });

  // ======================= replace, offline, disconnect =======================
  let runner2 = null;
  const home2 = path.join(WORK, "new-pc");
  await step("Connect a different computer: confirmation, then the old one is disconnected and its browsers stop", async () => {
    const oldPids = (await cloudView()).slots.map((s) => agentPids("runner1", s.id).at(-1)).filter(Boolean);
    await card().getByTestId("runner-connect").click();
    await page.getByText("Connect a different computer?").waitFor();
    await okDialog();
    const line = await copyLine("Linux");
    if (!(await cloudView()).pairing.replace) throw new Error("pairing not marked as replace");
    if (runner1.exitCode !== null) throw new Error("the old runner stopped before the new one paired");
    runner2 = startRunner("runner2", home2, line);
    await exited(runner1);                                    // within one poll (2 s here)
    if (runner1.exitCode !== 0 || !/disconnected in the portal/.test(readLog("runner1"))) throw new Error("old runner exit");
    await until(() => oldPids.every((p) => !alive(p)), 20000, "the old agents to stop");
    if ("token" in JSON.parse(fs.readFileSync(path.join(home1, "config.json"), "utf8"))) throw new Error("old token kept");
    await card().getByTestId("runner-status").filter({ hasText: "Office computer online" }).waitFor({ timeout: 30000 });
    await waitState("Asha", /Needs login/, 90000);             // a new computer has no LinkedIn session yet
  });
  await step("Stop the runner: the badge turns offline after about 90 s", async () => {
    runner2.kill("SIGTERM");
    await exited(runner2);
    const t0 = Date.now();
    await card().getByTestId("runner-status").filter({ hasText: "Office computer offline" }).waitFor({ timeout: 150000 });
    await card().getByText("Check that the office computer is switched on").waitFor();
    const s = Math.round((Date.now() - t0) / 1000);
    if (s < 60) throw new Error(`offline after only ${s} s`);
    return `offline after ${s} s (90 s threshold + page refresh)`;
  });
  await step("Disconnect: confirmation, Not connected, uninstall instructions", async () => {
    await card().getByTestId("runner-revoke").click();
    await okDialog();
    await see("Office computer disconnected.");
    await card().getByTestId("runner-status").filter({ hasText: "Not connected" }).waitFor();
    await card().getByText("How to remove it from the office computer").click();
    for (const t of ["uninstall.ps1", "MCN-LinkedIn-Runner/uninstall.sh", "mcn-linkedin-runner/uninstall.sh"]) await card().getByText(t, { exact: false }).first().waitFor();
    const again = spawnSync("node", [path.join(ROOT, "tools/linkedin_runner.mjs"), "--home", home2, ...RUNNER_ARGS], { encoding: "utf8", timeout: 30000 });
    if (!/disconnected in the portal/.test(again.stdout) || again.status !== 0) throw new Error(`revoked runner: ${again.stdout.slice(-300)}`);
  });
  await step("Nothing went to linkedin.com: every agent request reached the stand-in", async () => {
    const all = Object.keys(logs).filter((k) => k.startsWith("runner")).map(readLog).join("\n");
    const bad = all.split("\n").filter((l) => /linkedin\.com/.test(l) && !/linkedin\.com\/in\//.test(l));
    if (bad.length) throw new Error(`linkedin.com in logs: ${bad.slice(0, 3)}`);
    if (!all.includes(`LinkedIn address: ${MOCK}`)) throw new Error("agents did not announce the stand-in");
    const reqs = await mock("/_qa/requests");
    if (reqs.length < 20 || reqs.some((r) => r.host !== "127.0.0.1:8076")) throw new Error("stand-in traffic");
    return `${reqs.length} requests, all to the stand-in`;
  });
  await ctx.close();
}

// ---------------------------------------------------------------- main
async function main() {
  for (const [port, what] of [[8075, "API"], [8076, "stand-in"], [5275, "UI"]]) {
    if (await fetch(`http://127.0.0.1:${port}/`).then(() => true, () => false)) throw new Error(`port ${port} (${what}) is busy`);
  }
  const dist = path.join(WORK, "dist");
  const built = spawnSync("npx", ["vite", "build", "--outDir", dist, "--emptyOutDir"], { cwd: WEB, encoding: "utf8" });
  if (built.status !== 0) throw new Error(`vite build: ${built.stderr.slice(-500)}`);
  run("mock", PY, [path.join(ROOT, "tests/qa_mock_linkedin_server.py"), "8076"]);
  run("ui", "node", [path.join(WEB, "e2e/static_proxy.mjs"), dist, "5275", API]);
  await startApi();
  await until(async () => (await fetch(`${MOCK}/_qa/login`)).ok && (await fetch(UI)).ok, 15000, "stand-in and UI");
  const browser = await chromium.launch({ headless: !process.env.HEADED });
  const widths = (process.env.QA_WIDTHS || "1400,375").split(",").map(Number);
  try {
    for (const w of widths) await width(browser, w, w < 700 ? 812 : 900, w >= 700);
  } finally {
    await browser.close();
  }
}

main().catch((e) => { console.error(e); results.push(["setup", false, e.message, 0]); }).finally(async () => {
  for (const p of procs) if (p.exitCode === null) p.kill("SIGTERM");
  await wait(3000);
  for (const p of procs) if (p.exitCode === null) p.kill("SIGKILL");
  const failed = results.filter((r) => !r[1]);
  console.log(`\n${results.length - failed.length}/${results.length} passed. Logs and screenshots: ${WORK}`);
  process.exit(failed.length ? 1 : 0);
});
