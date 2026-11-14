import { STAGE_ORDER, sourceId } from "../labels.js";

// Pure Leads filter and sort logic (no React), kept apart from the page so the page stays small.
export const SORTS = {
  score: (a, b) => b.score - a.score,
  company: (a, b) => (a.company || a.domain || "").localeCompare(b.company || b.domain || ""),
  stage: (a, b) => STAGE_ORDER.indexOf(a.stage) - STAGE_ORDER.indexOf(b.stage),
  activity: (a, b) => (b.last_activity_at || "").localeCompare(a.last_activity_at || ""),
  due: (a, b) => (a.next_task_due || "9999").localeCompare(b.next_task_due || "9999"),
};

// Owner filter values: "" everyone, "__me" the signed-in person, "__none" nobody yet, else a name.
export function ownerMatches(lead, owner, me) {
  if (!owner) return true;
  if (owner === "__none") return !lead.owner;
  if (owner === "__me") return Boolean(lead.owner) && (lead.owner === me.name || lead.owner === me.email);
  return lead.owner === owner;
}

// A phone typed the way people write it ("+91 98907 79946", "098907-79946") finds the stored "+919890779946".
export function phoneMatches(phone, query) {
  const want = String(query || "").replace(/\D/g, "").replace(/^0+/, "");
  return want.length >= 6 && /^[\d\s()+.-]+$/.test(String(query).trim()) && String(phone || "").replace(/\D/g, "").includes(want);
}

// "Has" filter: every ticked detail must be on the lead (tick Phone + Email for leads with both).
export const HAS_FIELDS = [["phone", "Phone number"], ["email", "Email"], ["linkedin", "LinkedIn profile"]];
export function hasDetail(lead, what) {
  if (what === "phone") return Boolean(String(lead.phone || "").trim());
  if (what === "email") return Boolean(String(lead.email || "").trim());
  if (what === "linkedin") return /linkedin\.com\/(in|pub)\//i.test(String(lead.linkedin_url || ""));
  return true;
}

export function matches(lead, query, stage, band, onlyIds, sources = [], inboundOnly = false, owner = "", tag = "", me = {}, has = []) {
  const q = query.trim().toLowerCase();
  const text = [lead.company, lead.domain, lead.contact_name, lead.email, lead.phone, lead.role].join(" ").toLowerCase();
  return (!q || text.includes(q) || phoneMatches(lead.phone, q)) && (!stage || lead.stage === stage) && (!band || lead.band === band) && (!onlyIds || onlyIds.includes(lead.id))
    && (!sources.length || sources.includes(sourceId(lead))) && (!inboundOnly || Boolean(lead.inbound)) && ownerMatches(lead, owner, me)
    && (!tag || (lead.tags || []).some((t) => String(t).toLowerCase() === tag.toLowerCase()))
    && has.every((w) => hasDetail(lead, w));
}

// A short plain-English line about the campaign, ad or form behind a lead (shown as a tooltip and in the drawer).
export function attributionText(a = {}) {
  const parts = [a.campaign_name || a.utm_campaign, a.adset_name, a.ad_name, a.form_name || (a.form_id && `form ${a.form_id}`), a.utm_source && `source ${a.utm_source}`]
    .filter(Boolean);
  return [...new Set(parts)].join(" · ");
}