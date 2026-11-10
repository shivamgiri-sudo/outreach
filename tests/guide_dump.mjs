// Prints the glossary and every guide chapter as JSON, for tests/test_guide_static.py (plain Node, no bundler).
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const src = path.resolve(process.argv[2]);
const { GLOSSARY } = await import(pathToFileURL(path.join(src, "glossary.js")));
const dir = path.join(src, "guide", "chapters");
const chapters = [];
for (const f of fs.readdirSync(dir).filter((n) => n.endsWith(".js")).sort()) {
  const m = await import(pathToFileURL(path.join(dir, f)));
  chapters.push({ file: f, lines: fs.readFileSync(path.join(dir, f), "utf8").split("\n").length, ...m.default });
}
const meta = await import(pathToFileURL(path.join(src, "guide", "meta.js")));
console.log(JSON.stringify({ glossary: GLOSSARY, chapters, groups: meta.GROUPS }));
