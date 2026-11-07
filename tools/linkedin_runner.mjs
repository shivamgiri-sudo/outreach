#!/usr/bin/env node
// MCN office runner: runs the portal's LinkedIn agents on THIS always-on office computer, because LinkedIn blocks the
// portal's cloud server. Everything is managed in the portal (add an account, log in, codes, switch on/off); this program
// only hosts the browsers. It does not hide anything: a normal Chromium window on this computer's normal connection.
//
// First time (the one-line installer from the portal does this for you):
//   node tools/linkedin_runner.mjs --portal https://your-portal --code ABCD2345
// Afterwards (auto-start at logon runs exactly this):
//   node tools/linkedin_runner.mjs
//
// Every 15 s it asks the portal which LinkedIn accounts are switched on (GET /api/linkedin-runner/slots, runner token) and
// runs one `linkedin_agent.mjs --cloud` per account through the same agent pool as the server supervisor
// (tools/linkedin_agent_pool.mjs). Every 30 s it reports to the portal (heartbeat) so the portal shows "Office computer online".
// A dropped network is retried with a growing wait (the agents keep going on their own). If the owner disconnects this
// computer in the portal, it stops every agent, forgets its token and exits with a clear message.
//
// Files (the app-data folder, private to this user): config.json (portal + runner token, mode 0600), profiles/<account>
// (the browser sessions), logs/runner.log (rotated at 1 MB, 3 old files kept).
// Do not close the LinkedIn browser window: it is how the agent works. If it is closed, it opens again within a minute.
//
// Options: --headless (no window), --home <folder>, --interval 15, --max N, --pair-only, --agent-args="..." and
// --linkedin-base <url> (tests only: a LinkedIn stand-in).
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createPool, validSlots } from "./linkedin_agent_pool.mjs";

export const VERSION = "1.0.0";
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--") && a.includes("=")) acc.push([a.slice(2, a.indexOf("=")), a.slice(a.indexOf("=") + 1)]);
  else if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const HERE = path.dirname(fileURLToPath(import.meta.url));

export function defaultHome() {
  if (process.env.MCN_RUNNER_HOME) return process.env.MCN_RUNNER_HOME;
  if (process.platform === "win32") return path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "MCN-LinkedIn-Runner");
  if (process.platform === "darwin") return path.join(os.homedir(), "Library", "Application Support", "MCN-LinkedIn-Runner");
  return path.join(process.env.XDG_DATA_HOME || path.join(os.homedir(), ".local", "share"), "mcn-linkedin-runner");
}

const HOME = path.resolve(String(args.home || defaultHome()));
const CONFIG = path.join(HOME, "config.json");
const LOGS = path.join(HOME, "logs");
const INTERVAL = Math.max(1, Number(args.interval || 15)) * 1000;
const HEARTBEAT = Math.max(1, Number(args.heartbeat || 30)) * 1000;
const AGENT = path.resolve(String(args.agent || path.join(HERE, "linkedin_agent.mjs")));
const EXTRA = String(args["agent-args"] || "").split(/\s+/).filter(Boolean);
const LINKEDIN_BASE = args["linkedin-base"] ? String(args["linkedin-base"]) : "";
const HEADLESS = !!args.headless;
const LOG_MAX = 1024 * 1024;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

fs.mkdirSync(LOGS, { recursive: true, mode: 0o700 });
try { fs.chmodSync(HOME, 0o700); } catch { /* Windows: the folder is already private to this user */ }
const logFile = path.join(LOGS, "runner.log");
function writeLog(text) {
  const lineText = `${new Date().toISOString()} ${text}\n`;
  process.stdout.write(lineText);
  try {
    if (fs.existsSync(logFile) && fs.statSync(logFile).size > LOG_MAX) {
      for (let i = 2; i >= 1; i--) { try { fs.renameSync(`${logFile}.${i}`, `${logFile}.${i + 1}`); } catch { /* none yet */ } }
      fs.renameSync(logFile, `${logFile}.1`);
    }
    fs.appendFileSync(logFile, lineText, { mode: 0o600 });
  } catch { /* logging must never stop the runner */ }
}
const log = (...a) => writeLog(a.join(" "));

function readConfig() {
  try { return JSON.parse(fs.readFileSync(CONFIG, "utf8")); } catch { return {}; }
}
function saveConfig(data) {
  const temp = `${CONFIG}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data, null, 1), { mode: 0o600 });
  fs.renameSync(temp, CONFIG);
  try { fs.chmodSync(CONFIG, 0o600); } catch { /* Windows */ }
}

const machine = () => ({ hostname: os.hostname().slice(0, 80), os: `${os.type()} ${os.release()} (${process.platform}/${process.arch})`.slice(0, 80),
  version: `${VERSION} node ${process.versions.node}` });

class Revoked extends Error {}
async function call(portal, token, pathname, method = "GET", body) {
  const res = await fetch(portal + pathname, { method, signal: AbortSignal.timeout(20000),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: body ? JSON.stringify(body) : undefined });
  if (res.status === 401 && token) throw new Revoked("the portal no longer accepts this computer's token");
  if (!res.ok) {
    let detail = "";
    try { detail = (await res.json()).detail || ""; } catch { /* not JSON */ }
    const err = new Error(`${method} ${pathname}: ${res.status} ${String(detail).slice(0, 200)}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

async function pairWith(portal, code) {
  const out = await call(portal, "", "/api/linkedin-runner/pair", "POST", { code, ...machine() });
  saveConfig({ portal, token: out.token, runner_id: out.runner_id, paired_at: new Date().toISOString() });
  log(`Connected to ${portal} as office computer ${out.runner_id}. The token is saved in ${CONFIG} (only this user can read it).`);
}

// One runner per computer: a second copy (auto-start plus a manual start) would drive the same LinkedIn accounts twice.
// The running copy touches runner.lock every few seconds, so a lock that is not fresh is stale even when its pid is alive:
// after a reboot the old pid can belong to another program (Windows reuses pids quickly), and that must not block auto-start.
const LOCK_FRESH = 30000;
function takeLock() {
  const lock = path.join(HOME, "runner.lock");
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const fd = fs.openSync(lock, "wx", 0o600);         // exclusive: two copies starting at once cannot both get it
      fs.writeSync(fd, String(process.pid));
      fs.closeSync(fd);
      break;
    } catch (e) {
      if (e.code !== "EEXIST") throw e;
    }
    let pid = 0, age = Infinity;
    try { pid = Number(fs.readFileSync(lock, "utf8")); age = Date.now() - fs.statSync(lock).mtimeMs; } catch { /* just removed */ }
    let running = false;
    if (pid && pid !== process.pid && age < LOCK_FRESH) { try { process.kill(pid, 0); running = true; } catch { /* gone */ } }
    if (running) {
      log(`The office runner is already running on this computer (process ${pid}). Not starting a second copy.`);
      process.exit(0);
    }
    try { fs.rmSync(lock); } catch { /* another copy removed it */ }
  }
  const touch = setInterval(() => { try { const t = new Date(); fs.utimesSync(lock, t, t); } catch { /* removed */ } }, 5000);
  touch.unref();
  return () => { clearInterval(touch); try { if (Number(fs.readFileSync(lock, "utf8")) === process.pid) fs.rmSync(lock); } catch { /* gone */ } };
}

async function main() {
  let config = readConfig();
  const portalArg = args.portal ? String(args.portal).replace(/\/+$/, "") : "";
  if (args.code) {
    const portal = portalArg || config.portal;
    if (!portal) { log("Give the portal address: --portal https://your-portal --code ABCD2345"); process.exit(2); }
    try { await pairWith(portal, String(args.code)); }
    catch (e) { log(`Could not connect this computer: ${e.message}`); process.exitCode = 2; return; }   // no process.exit() after fetch, see below
    config = readConfig();
    // Let Node finish on its own: process.exit() right after fetch() can trip a libuv assertion on Windows
    // ("!(handle->flags & UV_HANDLE_CLOSING)", src\win\async.c) and turn a good pairing into a crash.
    if (args["pair-only"]) { process.exitCode = 0; return; }
  }
  const portal = portalArg || config.portal;
  if (!portal || !config.token) {
    log("This computer is not connected to the portal. In the portal open LinkedIn outreach, Office computer, press "
      + "\"Connect an office computer\" and paste the one-line command it shows.");
    process.exit(0);
  }
  const release = takeLock();
  // The runner is started without a console (start-runner.vbs), so a crash message on stderr is lost: write it to the
  // log, and log every exit. Before this the runner could stop with nothing at all in runner.log (2026-11-07).
  process.on("uncaughtException", (e) => { writeLog(`The office runner crashed: ${String(e && e.stack || e).split("\n").slice(0, 6).join(" | ")}`); process.exit(1); });
  process.on("unhandledRejection", (e) => { writeLog(`The office runner crashed (unhandled promise): ${String(e && e.stack || e).split("\n").slice(0, 6).join(" | ")}`); process.exit(1); });
  process.on("exit", (code) => writeLog(`The office runner stopped (exit code ${code}).`));
  const env = LINKEDIN_BASE ? { MCN_LINKEDIN_BASE: LINKEDIN_BASE } : {};
  const extra = LINKEDIN_BASE && !EXTRA.includes("--linkedin-base") ? [...EXTRA, "--linkedin-base", LINKEDIN_BASE] : EXTRA;
  const pool = createPool({ profilesDir: path.join(HOME, "profiles"), agentPath: AGENT, portal, extraArgs: extra, max: Number(args.max || 3),
    backoffBase: Number(args["backoff-base"] || 5) * 1000, stopGrace: Number(args["stop-grace"] || 10) * 1000,
    stateFile: path.join(HOME, "runner-state.json"), headless: HEADLESS, env, log,
    line: (tag, text) => writeLog(`${tag} ${text}`) });

  let stopping = false;
  const finish = async (why, code) => {
    if (stopping) return;
    stopping = true;
    await pool.stopAll(why);
    release();
    // Not process.exit() at once: right after fetch() it can trip a libuv assertion on Windows (see --pair-only). Node
    // exits by itself when nothing is left; the timer (unref'd, so it never keeps Node alive) ends it if something is.
    process.exitCode = code;
    setTimeout(() => process.exit(code), 3000).unref();
  };
  process.on("SIGTERM", () => { log("SIGTERM: stopping all agents"); finish("the office runner is stopping", 0); });
  process.on("SIGINT", () => { log("Ctrl+C: stopping all agents"); finish("the office runner is stopping", 0); });

  const revoked = async () => {
    log("This computer was disconnected in the portal (or replaced by another office computer). All LinkedIn agents here are "
      + "stopped and its token is deleted. To connect it again, press \"Connect an office computer\" in the portal and paste the new command.");
    const { token, ...rest } = readConfig();
    saveConfig(rest);
    await finish("this computer was disconnected in the portal", 0);
  };

  log(`MCN office runner ${VERSION}: portal ${portal}, folder ${HOME}, ${HEADLESS ? "headless" : "visible browser windows"}, every ${INTERVAL / 1000} s`);
  if (!portal.startsWith("https://") && !/^http:\/\/(127\.0\.0\.1|localhost)(:|\/|$)/.test(portal)) log("Warning: the portal address is not HTTPS.");
  let failures = 0, lastBeat = 0, lastWhere = "";
  for (;;) {
    if (stopping) return;                               // Ctrl+C / SIGTERM: finish() is stopping the agents; start nothing new
    let wait = INTERVAL;
    try {
      const data = await call(portal, config.token, "/api/linkedin-runner/slots");
      // A reply without a proper account list (a proxy page, a half-deployed portal) must never be read as "every
      // account was removed": that would stop the agents and delete their saved LinkedIn logins.
      if (!data || typeof data !== "object" || !Array.isArray(data.slots) || (data.where !== "office" && data.where !== "server")) {
        throw new Error("the portal's answer had no account list; keeping everything as it is");
      }
      if (failures) log(`The portal answers again (after ${failures} failed attempt${failures === 1 ? "" : "s"}).`);
      failures = 0;
      if (data.where !== lastWhere) {
        log(data.where === "office" ? "LinkedIn runs on this office computer." : "The portal runs LinkedIn on its server now: no agents here.");
        lastWhere = data.where;
      }
      if (Number.isFinite(data.max)) pool.setMax(data.max);
      await pool.pass(validSlots(data.slots));
      if (Date.now() - lastBeat >= HEARTBEAT) {
        await call(portal, config.token, "/api/linkedin-runner/heartbeat", "POST", { ...machine(), agents: pool.details() });
        lastBeat = Date.now();
      }
    } catch (e) {
      if (e instanceof Revoked) { await revoked(); return; }
      failures += 1;
      wait = Math.min(60000, 5000 * 2 ** Math.min(failures - 1, 4));
      log(`Could not reach the portal (${String(e.message || e).split("\n")[0].slice(0, 200)}); trying again in ${Math.round(wait / 1000)} s. `
        + "LinkedIn agents already running keep going.");
    }
    await sleep(wait);
  }
}

main();
