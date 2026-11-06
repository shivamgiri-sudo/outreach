#!/usr/bin/env node
// Runs the cloud LinkedIn agents on the server: one headless `tools/linkedin_agent.mjs --cloud` per enabled slot in
// slots.json (written by the portal, app/linkedin_cloud.py), at most OUTREACH_CLOUD_LI_MAX. Every pass (15 s) it hands the
// slots to the shared agent pool (tools/linkedin_agent_pool.mjs: start / stop / restart with backoff / safety hold /
// log out / remove, exactly like the office runner) and writes supervisor.json (a heartbeat the portal shows).
// When the portal is in "office" mode (LinkedIn runs on an office computer, tools/linkedin_runner.mjs) every slot in
// slots.json is switched off, so this supervisor idles.
// The slot token goes to the agent in its environment (MCN_TOKEN), not on the command line, so `ps` does not show it.
//
//   node tools/linkedin_cloud_supervisor.mjs [--dir /var/lib/outreach/linkedin-cloud] [--portal http://127.0.0.1:8000]
//        [--interval 15] [--max 1] [--agent tools/linkedin_agent.mjs] [--agent-args="--poll 20"]
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createPool, writeJson } from "./linkedin_agent_pool.mjs";

// --key value, or --key=value (needed for --agent-args="--poll 5", whose value starts with --)
const args = Object.fromEntries(process.argv.slice(2).reduce((acc, a, i, all) => {
  if (a.startsWith("--") && a.includes("=")) acc.push([a.slice(2, a.indexOf("=")), a.slice(a.indexOf("=") + 1)]);
  else if (a.startsWith("--")) acc.push([a.slice(2), all[i + 1] && !all[i + 1].startsWith("--") ? all[i + 1] : true]);
  return acc;
}, []));
const HERE = path.dirname(fileURLToPath(import.meta.url));
const DIR = path.resolve(String(args.dir || process.env.OUTREACH_CLOUD_LI_DIR || "/var/lib/outreach/linkedin-cloud"));
const PORTAL = String(args.portal || process.env.OUTREACH_CLOUD_LI_PORTAL || "http://127.0.0.1:8000");
const INTERVAL = Number(args.interval || process.env.OUTREACH_CLOUD_LI_INTERVAL || 15) * 1000;
const MAX = Math.max(0, Number(args.max || process.env.OUTREACH_CLOUD_LI_MAX || 1));
const AGENT = path.resolve(String(args.agent || path.join(HERE, "linkedin_agent.mjs")));
const EXTRA = String(args["agent-args"] || process.env.OUTREACH_CLOUD_LI_AGENT_ARGS || "").split(/\s+/).filter(Boolean);

const log = (...a) => console.log(new Date().toISOString(), ...a);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function readSlots() {
  try {
    const data = JSON.parse(fs.readFileSync(path.join(DIR, "slots.json"), "utf8"));
    return Array.isArray(data.slots) ? data.slots : [];
  } catch (e) {
    if (e.code !== "ENOENT") log("could not read slots.json:", e.message);
    return [];
  }
}

fs.mkdirSync(DIR, { recursive: true, mode: 0o700 });
const pool = createPool({ profilesDir: path.join(DIR, "profiles"), agentPath: AGENT, portal: PORTAL, extraArgs: EXTRA, max: MAX,
  backoffBase: Number(args["backoff-base"] || 5) * 1000, stopGrace: Number(args["stop-grace"] || 10) * 1000,
  stateFile: path.join(DIR, "supervisor-state.json"), headless: true,
  env: { HOME: process.env.HOME || DIR }, log, line: (tag, text) => console.log(tag, text) });

let stopping = false;
async function shutdown(signal) {
  if (stopping) return;
  stopping = true;
  log(`${signal}: stopping all agents`);
  await pool.stopAll("the supervisor is stopping");
  process.exit(0);
}
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));

log(`cloud LinkedIn supervisor: ${DIR}, portal ${PORTAL}, at most ${MAX} agent(s), every ${INTERVAL / 1000} s`);
for (;;) {
  try {
    const agents = await pool.pass(readSlots());
    try { writeJson(path.join(DIR, "supervisor.json"), { at: new Date().toISOString(), pid: process.pid, max: MAX, agents }); }
    catch (e) { log("could not write supervisor.json:", e.message); }
  } catch (e) { log("pass failed:", e.message); }
  await sleep(INTERVAL);
}
