// Indian number formats for the sales funnel: ₹12,34,567 in tables, ₹12.3 L / ₹1.2 Cr on tiles and axes.
const IN = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const DEC = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });

export const inr = (n) => (n === null || n === undefined ? "—" : `₹${IN.format(Math.round(n))}`);
export function inrShort(n) {
  if (n === null || n === undefined) return "—";
  const v = Math.abs(n);
  if (v >= 1e7) return `₹${DEC.format(Math.round(n / 1e5) / 100)} Cr`;
  if (v >= 1e5) return `₹${DEC.format(Math.round(n / 1e3) / 100)} L`;
  return inr(n);
}
export const num = (n) => (n === null || n === undefined ? "—" : IN.format(n));
export const pct = (n) => (n === null || n === undefined ? "—" : `${n}%`);
export const hours = (n) => (n === null || n === undefined ? "—" : `${n} h`);
export const days = (n) => (n === null || n === undefined ? "—" : `${n} d`);

// "▲ 12.5%" / "▼ 3%" against the previous period; a dash when there is no base to compare with.
export function changeText(c) {
  if (c === null || c === undefined) return { text: "—", tone: "flat" };
  if (c === 0) return { text: "no change", tone: "flat" };
  return { text: `${c > 0 ? "▲" : "▼"} ${Math.abs(c)}%`, tone: c > 0 ? "up" : "down" };
}

export const STATUS_SHORT = {
  "Inquiry / Raw Lead": "Inquiry", "Marketing Qualified Lead (MQL)": "MQL", "Sales Accepted Lead (SAL)": "SAL", "Sales Qualified Lead (SQL)": "SQL",
  "Discovery Meeting Held": "Meeting", "Proposal Submitted": "Proposal", "Negotiation / Commercials": "Negotiation", "Closed Won": "Won",
  "Closed Lost": "Lost", "Recycled / Nurture": "Recycled",
};
export const statusShort = (s) => STATUS_SHORT[s] || s;
export const STAGE_COLORS = ["#94a3b8", "#64748b", "#0284c7", "#0369a1", "#7c3aed", "#d97706", "#c2410c", "#15803d"];
