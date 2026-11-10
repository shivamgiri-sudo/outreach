// In-guide search: client side, over the chapters the person can see plus the glossary. Every word typed must appear;
// a hit in the title counts most, then the summary, then the body.
import { GLOSSARY } from "../glossary.js";
import { plainText } from "./markup.jsx";

const words = (q) => q.toLowerCase().split(/[^a-z0-9/+]+/).filter(Boolean);

export function searchGuide(query, chapters) {
  const ws = words(query);
  if (!ws.length) return [];
  const hits = [];
  const score = (title, summary, body) => {
    let s = 0;
    for (const w of ws) {
      if (title.includes(w)) s += 10; else if (summary.includes(w)) s += 4; else if (body.includes(w)) s += 1; else return 0;
    }
    return s;
  };
  for (const c of chapters) {
    const s = score(c.title.toLowerCase(), (c.summary || "").toLowerCase(), plainText(c.blocks).toLowerCase());
    if (s) hits.push({ type: "chapter", id: c.id, title: c.title, text: c.summary, href: `#help/${c.id}`, score: s });
  }
  for (const g of GLOSSARY) {
    const s = score(`${g.term} ${g.full}`.toLowerCase(), g.short.toLowerCase(), `${g.long} ${g.where}`.toLowerCase());
    if (s) hits.push({ type: "term", id: g.id, title: g.full && g.full !== g.term ? `${g.term}: ${g.full}` : g.term, text: g.short, href: `#help/glossary/${g.id}`, score: s });
  }
  return hits.sort((a, b) => b.score - a.score).slice(0, 40);
}
