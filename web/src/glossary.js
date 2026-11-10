// The glossary: one source of truth for every short term the portal uses. Data lives in glossaryData/*.js (one file per
// topic, so each stays small). The <Abbr> tooltip, the Help centre's glossary page and the guide search all read from here.
import sales from "./glossaryData/sales.js";
import email from "./glossaryData/email.js";
import security from "./glossaryData/security.js";
import tools from "./glossaryData/tools.js";

const GROUPS = [["Sales and funnel", sales], ["Email and sending", email], ["Security, privacy and data", security], ["Channels, tools and roles", tools]];

export const GLOSSARY = GROUPS.flatMap(([group, list]) => list.map((e) => ({ ...e, group, id: glossaryId(e.term) })))
  .sort((a, b) => a.term.localeCompare(b.term, "en", { sensitivity: "base" }));

// "MQL" -> "MQL"; "A/B test" -> "A-B-test"; "Est. monthly value" -> "Est-monthly-value" (safe inside a #help/glossary/<id> link).
export function glossaryId(term) {
  return String(term).trim().replace(/[^A-Za-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}
const BY_ID = Object.fromEntries(GLOSSARY.map((e) => [e.id.toLowerCase(), e]));
export const findTerm = (term) => BY_ID[glossaryId(term).toLowerCase()] || null;
export const glossaryHref = (term) => `#help/glossary/${glossaryId(term)}`;
