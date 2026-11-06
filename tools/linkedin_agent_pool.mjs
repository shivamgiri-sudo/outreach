// Runs one `linkedin_agent.mjs --cloud` per enabled slot. Shared by the server supervisor (tools/linkedin_cloud_supervisor.mjs,
// slots from slots.json) and the office runner (tools/linkedin_runner.mjs, slots from the portal). Each pass:
//   - starts agents for enabled slots (at most `max`), each with its own browser profile folder (0700);
//   - stops agents whose slot was switched off or removed, and deletes the browser profile of removed slots;
//   - restarts an agent that crashed, waiting longer each time (5 s, 10 s, 20 s ... up to 5 minutes);
//   - does NOT restart an agent that stopped itself for safety (exit code 3) until the owner acts in the portal
//     (the slot's run_seq changes: Log in, Restart, Switch on);
//   - "Restart" in the portal raises restart_seq: a running agent is stopped and started again;
//   - "Log out" in the portal raises wipe_seq: the agent is stopped, its browser profile deleted, and it starts again logged out;
//   - a slot whose token changed (rotated by the portal) is restarted with the new token.
// The slot token goes to the agent in its environment (MCN_TOKEN), never on the command line, so `ps` does not show it.
import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

const SLOT_ID = /^[a-z0-9]{4,20}$/;
// Only these variables reach an agent (never the rest of the parent's environment). The Windows / desktop ones are needed
// for a visible Chromium window on an office computer.
const PASS_ENV = ["PATH", "Path", "HOME", "LANG", "TZ", "PLAYWRIGHT_BROWSERS_PATH", "MCN_PLAYWRIGHT_DIR", "MCN_LINKEDIN_BASE",
  "SYSTEMROOT", "SystemRoot", "WINDIR", "windir", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "TEMP", "TMP", "TMPDIR", "PATHEXT",
  "COMSPEC", "ComSpec", "USER", "USERNAME", "LOGNAME", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR",
  "DBUS_SESSION_BUS_ADDRESS", "PROGRAMFILES", "ProgramFiles", "PROGRAMDATA", "ProgramData", "SYSTEMDRIVE", "SystemDrive"];

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export function validSlots(list) {
  return (Array.isArray(list) ? list : []).filter((s) => s && SLOT_ID.test(s.slot_id || "") && (s.token || !s.enabled));
}

export function writeJson(file, data) {
  const temp = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(data), { mode: 0o600 });
  fs.renameSync(temp, file);
}

export function createPool({ profilesDir, agentPath, portal, extraArgs = [], max = 1, backoffBase = 5000, backoffMax = 5 * 60 * 1000,
  stopGrace = 10000, stateFile, headless = true, env = {}, log = console.log, line = (tag, text) => console.log(tag, text) }) {
  const running = new Map();       // slot_id -> {child, startedAt, runSeq, restartSeq, token, stopping}
  const held = new Map();          // slot_id -> {runSeq, why}  (stopped itself for safety: waiting for the owner)
  const crashes = new Map();       // slot_id -> {count, until}
  let wiped = {};                  // slot_id -> wipe_seq already handled (persisted in stateFile)
  let closing = false;
  let limit = max;
  let lastSlots = [];
  try { wiped = JSON.parse(fs.readFileSync(stateFile, "utf8")).wiped || {}; } catch { wiped = {}; }
  fs.mkdirSync(profilesDir, { recursive: true, mode: 0o700 });

  // A slot's browser profile is always <profilesDir>/<slot id> (ids are checked): nothing from the portal is used as a path.
  const profileOf = (id) => path.join(profilesDir, id);
  const wipe = (id) => fs.rmSync(profileOf(id), { recursive: true, force: true });

  function start(slot) {
    const profile = profileOf(slot.slot_id);
    fs.mkdirSync(profile, { recursive: true, mode: 0o700 });
    try { fs.chmodSync(profile, 0o700); } catch { /* not ours */ }
    const childEnv = Object.fromEntries(PASS_ENV.filter((k) => process.env[k] !== undefined).map((k) => [k, process.env[k]]));
    Object.assign(childEnv, { LANG: process.env.LANG || "C.UTF-8", TZ: process.env.TZ || "Asia/Kolkata", ...env,
      MCN_TOKEN: slot.token, MCN_PORTAL: portal });
    const argv = [agentPath, "--cloud", headless ? "--headless" : "--headed", "--portal", portal, "--profile", profile, ...extraArgs];
    const child = spawn(process.execPath, argv, { env: childEnv, stdio: ["ignore", "pipe", "pipe", "ipc"], windowsHide: headless });
    const tag = `[${slot.slot_id}]`;
    for (const stream of [child.stdout, child.stderr]) {
      let buf = "";
      stream.on("data", (d) => { buf += d; let i; while ((i = buf.indexOf("\n")) >= 0) { line(tag, buf.slice(0, i)); buf = buf.slice(i + 1); } });
    }
    child.on("error", (e) => log(`agent for ${slot.slot_id} could not start: ${e.message}`));
    const entry = { child, startedAt: Date.now(), runSeq: slot.run_seq || 0, restartSeq: slot.restart_seq || 0, token: slot.token, stopping: false };
    running.set(slot.slot_id, entry);
    log(`started agent for ${slot.slot_id} (${slot.label || "no label"}), pid ${child.pid}`);
    child.on("exit", (code, signal) => {
      if (running.get(slot.slot_id) === entry) running.delete(slot.slot_id);
      if (entry.stopping || closing) { log(`agent for ${slot.slot_id} stopped`); return; }
      if (code === 3) {
        held.set(slot.slot_id, { runSeq: entry.runSeq, why: "stopped itself for safety" });
        log(`agent for ${slot.slot_id} stopped itself for safety (exit 3); it starts again when the owner acts in the portal`);
        return;
      }
      const before = crashes.get(slot.slot_id) || { count: 0 };
      const count = Date.now() - entry.startedAt > 10 * 60 * 1000 ? 1 : before.count + 1;     // a long healthy run resets the backoff
      const wait = Math.min(backoffMax, backoffBase * 2 ** (count - 1));
      crashes.set(slot.slot_id, { count, until: Date.now() + wait });
      log(`agent for ${slot.slot_id} exited (code ${code}${signal ? `, ${signal}` : ""}); restarting in ${Math.round(wait / 1000)} s`);
    });
  }

  async function stop(id, why) {
    const entry = running.get(id);
    if (!entry) return;
    entry.stopping = true;
    log(`stopping agent for ${id}: ${why}`);
    const alive = () => entry.child.exitCode === null && entry.child.signalCode === null;
    try { if (entry.child.connected) entry.child.send("stop"); } catch { /* channel gone */ }
    if (process.platform !== "win32") entry.child.kill("SIGTERM");        // on Windows a signal is a hard kill: ask first
    for (let waited = 0; waited < stopGrace && alive(); waited += 200) await sleep(200);
    if (alive()) entry.child.kill(process.platform === "win32" ? undefined : "SIGKILL");
    running.delete(id);
  }

  function stateOf(s) {
    return running.has(s.slot_id) ? "running" : held.has(s.slot_id) ? "held" : !s.enabled ? "off"
      : crashes.has(s.slot_id) && Date.now() < crashes.get(s.slot_id).until ? "restarting" : "waiting";
  }

  async function pass(input) {
    const slots = validSlots(input);
    lastSlots = slots;
    const known = new Set(slots.map((s) => s.slot_id));
    for (const id of [...running.keys()]) if (!known.has(id)) await stop(id, "its cloud account was removed");
    try {                                            // browser profiles of removed accounts
      for (const name of fs.readdirSync(profilesDir)) if (!known.has(name) && !running.has(name)) { wipe(name); log(`deleted the browser profile of removed account ${name}`); }
    } catch { /* no profiles yet */ }
    for (const id of Object.keys(wiped)) if (!known.has(id)) delete wiped[id];
    for (const map of [crashes, held]) for (const id of [...map.keys()]) if (!known.has(id)) map.delete(id);
    let started = running.size;
    for (const slot of slots) {
      const id = slot.slot_id;
      if ((slot.wipe_seq || 0) > (wiped[id] || 0)) {
        if (running.has(id)) { await stop(id, "logged out in the portal"); started -= 1; }
        wipe(id);
        wiped[id] = slot.wipe_seq || 0;
        if (stateFile) writeJson(stateFile, { wiped });
        log(`deleted the browser session of ${id} (logged out)`);
      }
      if (!slot.enabled) { if (running.has(id)) { await stop(id, "switched off in the portal"); started -= 1; } held.delete(id); continue; }
      if (running.has(id) && (slot.restart_seq || 0) > running.get(id).restartSeq) { await stop(id, "restart pressed in the portal"); started -= 1; crashes.delete(id); }
      if (running.has(id) && running.get(id).token !== slot.token) { await stop(id, "its token was changed by the portal"); started -= 1; crashes.delete(id); }
      if (running.has(id)) continue;
      const hold = held.get(id);
      if (hold && hold.runSeq === (slot.run_seq || 0)) continue;
      held.delete(id);
      const crash = crashes.get(id);
      if (crash && Date.now() < crash.until) continue;
      if (started >= limit) continue;
      start(slot);
      started += 1;
    }
    return Object.fromEntries(slots.map((s) => [s.slot_id, stateOf(s)]));
  }

  async function stopAll(why) {
    closing = true;
    await Promise.all([...running.keys()].map((id) => stop(id, why)));
  }

  return {
    pass, stopAll,
    setMax: (n) => { if (Number.isFinite(n) && n >= 0) limit = n; },
    details: () => lastSlots.map((s) => ({ slot_id: s.slot_id, pid: running.get(s.slot_id)?.child.pid ?? null, state: stateOf(s) })),
    get size() { return running.size; },
  };
}
