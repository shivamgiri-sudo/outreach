// Stand-in for tools/linkedin_agent.mjs when testing tools/linkedin_cloud_supervisor.mjs: no browser. It appends what
// happened to <cloud dir>/events.log and behaves as <cloud dir>/<slot id>.mode says: "run" (default), "crash" (exit 1
// after a moment) or "safety" (exit 3, the agent's "stopped itself for safety" code).
import fs from "node:fs";
import path from "node:path";

const at = process.argv.indexOf("--profile");
const profile = process.argv[at + 1];
const slot = path.basename(profile);
const dir = path.resolve(profile, "..", "..");
const say = (line) => fs.appendFileSync(path.join(dir, "events.log"), `${line}\n`);
let mode = "run";
try { mode = fs.readFileSync(path.join(dir, `${slot}.mode`), "utf8").trim(); } catch { /* default */ }
say(`start ${slot} pid=${process.pid} token_env=${process.env.MCN_TOKEN ? "yes" : "no"} token_argv=${process.argv.some((a) => a.startsWith("mcnx_")) ? "yes" : "no"} cloud=${process.argv.includes("--cloud")}`);
fs.writeFileSync(path.join(profile, "Cookies"), "session");
process.on("SIGTERM", () => { say(`stop ${slot}`); process.exit(0); });
if (mode === "crash") setTimeout(() => { say(`crash ${slot}`); process.exit(1); }, 200);
else if (mode === "safety") setTimeout(() => { say(`safety ${slot}`); process.exit(3); }, 200);
else setInterval(() => {}, 1000);
