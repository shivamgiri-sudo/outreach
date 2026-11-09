import terms from "./terms.js";
import privacy from "./privacy.js";
import dpa from "./dpa.js";

export const DOCS = { terms, privacy, dpa };
export const LEGAL_PATHS = ["terms", "privacy", "dpa"];

// Which legal page the address asks for: the path (/terms) or the hash (#terms, #/terms, #terms/section), else "".
export function legalFromLocation(loc = window.location) {
  const path = loc.pathname.replace(/\/+$/, "").replace(/^\//, "");
  if (LEGAL_PATHS.includes(path)) return path;
  const h = loc.hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return LEGAL_PATHS.includes(h) ? h : "";
}

function texts(doc) {
  const out = [doc.lede];
  for (const s of doc.sections) {
    out.push(s.title);
    for (const b of s.blocks) {
      if (typeof b === "string") out.push(b);
      else if (Array.isArray(b)) out.push(...b);
      else b.rows.forEach((r) => out.push(...r));
    }
  }
  return out;
}

// Every [PLACEHOLDER] in a document, in first-seen order, so the banner at the top can never miss one.
export function placeholdersOf(doc) {
  const seen = new Set();
  for (const t of texts(doc)) for (const m of t.match(/\[[^\]]+\]/g) || []) seen.add(m);
  return [...seen];
}
