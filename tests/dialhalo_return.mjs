// Reads a JSON list of hashes on stdin, prints readConnectReturn(hash) for each (web/src/pages/integrations/dialhaloReturn.js).
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const mod = await import(pathToFileURL(process.argv[2]).href);
process.stdout.write(JSON.stringify(JSON.parse(readFileSync(0, "utf8")).map((h) => mod.readConnectReturn(h))));
