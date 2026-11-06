// One place for the words people see, so raw codes like "whatsapp_active" never reach the screen.
export const STAGE_LABELS = {
  new: "New", verified: "Verified", enriched: "Enriched", contacted: "Contacted", engaged: "Engaged",
  interested: "Interested", whatsapp_active: "WhatsApp active", call_done: "Call done", trial: "Trial",
  customer: "Customer", advocate: "Advocate", not_now: "Not now", unsubscribed: "Unsubscribed", disqualified: "Disqualified",
};
export const STAGE_ORDER = Object.keys(STAGE_LABELS);
// The owner can rename how stages read (Setup → Brand & offer); the ids never change. Set from the brand (brand.js).
let stageOverrides = {};
export const DEFAULT_LEAD_TERMS = { open_roles: "Open roles", role: "Role" };
let leadTerms = { ...DEFAULT_LEAD_TERMS };
export const setLabelOverrides = (stages = {}, terms = {}) => {
  stageOverrides = { ...stages };
  leadTerms = { ...DEFAULT_LEAD_TERMS, ...Object.fromEntries(Object.entries(terms).filter(([, v]) => v)) };
};
export const stageLabel = (s) => stageOverrides[s] || STAGE_LABELS[s] || s;
// What the lead's numbers and fields mean for this business: leadTerm("open_roles") -> "Open roles" / "Units" / "Seats".
export const leadTerm = (key) => leadTerms[key] || DEFAULT_LEAD_TERMS[key] || key;

// Lead sources come from the server (GET /api/lead-sources) so the words live in one place. Until the list has loaded,
// a source shows its own name with the underscores taken out.
import { useEffect, useState } from "react";
import { api } from "./api.js";

let registry = { sources: [], inbound: [], aliases: {} };
let byId = {};
let loading = null;
const listeners = new Set();
export const setSourceRegistry = (data) => {
  registry = { sources: data.sources || [], inbound: data.inbound || [], aliases: data.aliases || {} };
  byId = Object.fromEntries(registry.sources.map((s) => [s.id, s]));
  listeners.forEach((fn) => fn((n) => n + 1));
};
export const loadSourceRegistry = () => (loading = loading || api.leadSources().then(setSourceRegistry).catch(() => { loading = null; }));
export function useSourceRegistry() {
  const [, bump] = useState(0);
  useEffect(() => { listeners.add(bump); loadSourceRegistry(); return () => listeners.delete(bump); }, []);
  return registry;
}
// "apollo_search", "web_scraper:https://x.in" or a lead object -> the registry id ("apollo", "web_scraper").
export const sourceId = (raw = "") => {
  const text = typeof raw === "object" && raw ? (raw.source_id || raw.source || "") : String(raw || "");
  const head = text.split(":")[0].trim().toLowerCase();
  return byId[head] ? head : registry.aliases[head] || head || "unknown";
};
export const sourceLabel = (raw = "") => byId[sourceId(raw)]?.label || sourceId(raw).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const isInboundSource = (id) => registry.inbound.includes(sourceId(id));
export const sourceOptions = (kinds) => registry.sources.filter((s) => !kinds || kinds.includes(s.kind));

export const BAND_LABELS = { hot: "Hot", warm: "Warm", cold: "Cold" };

export const SEQUENCE_STOPPED = {
  replied: "they replied", unsubscribed: "they unsubscribed", bounced: "the address bounced", send_failed: "sending failed 3 times",
};
