// Reads [{lead, history}] JSON on stdin, prints the JS email states (web/src/emailState.js) as a JSON list.
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
const cases = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify(cases.map((c) => mod.computeEmailState(c.lead, c.history))));
