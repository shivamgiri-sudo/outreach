import { STAGE_ORDER, sourceId } from "../labels.js";
import { isYoungDomain, noMailAuth } from "./domainSignals.js";
import { emailStateOf } from "../emailState.js";

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
// Same rules as app/lead_grid.py, so "Download these N" matches the grid.
// A third item is the whole tick-box phrase when "With <text>" does not read well (domain trust: app/domain_signals.py).
export const HAS_FIELDS = [["phone", "Phone number"], ["mobile", "Mobile number"], ["email", "Email"], ["linkedin", "LinkedIn profile"], ["hiring", "Hiring now (open roles)"],
  ["young_domain", "Young domain", "Domain younger than 1 year"], ["no_mail_auth", "No SPF/DMARC", "No SPF or DMARC mail record"]];
// Mobile only: a mobile or "mobile or landline" number; older leads without a line type use phone_type.
export const isMobile = (lead) => Boolean(String(lead.phone || "").trim()) && (lead.phone_line_type ? ["mobile", "mobile_or_fixed"].includes(lead.phone_line_type) : lead.phone_type === "mobile");
// Open roles seen on the company's own career pages (hiring_signal) or from a job import (open_roles).
export const hiringRoles = (lead) => Number(lead.hiring_signal?.open_roles || lead.open_roles || 0);
export function hasDetail(lead, what) {
  if (what === "phone") return Boolean(String(lead.phone || "").trim());
  if (what === "mobile") return isMobile(lead);
  if (what === "email") return String(lead.email || "").includes("@");   // a pattern guess (email_guess) never counts
  if (what === "hiring") return hiringRoles(lead) > 0;
  if (what === "linkedin") return /linkedin\.com\/(in|pub)\//i.test(String(lead.linkedin_url || ""));
  if (what === "young_domain") return isYoungDomain(lead);
  if (what === "no_mail_auth") return noMailAuth(lead);
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

// Grid filters: minimum rating (a lead without one is left out), hide DND (flag or a number on the uploaded DND list),
// lead harvest ("__any" = any job, else one job id).
export const RATINGS = ["3", "3.5", "4", "4.5"];
export const ratingOf = (lead) => (lead.rating === null || lead.rating === undefined || lead.rating === "" || Number.isNaN(Number(lead.rating)) ? null : Number(lead.rating));
export const isDnd = (lead) => Boolean(lead.dnc || lead.on_dnd);
export const fromHarvest = (lead, job) => { const jobs = lead.harvest_jobs || []; return job === "__any" ? jobs.length > 0 : jobs.includes(job); };
// emailState: "" any, else one state of web/src/emailState.js (confirmed | domain_ready | candidate | bad).
export function gridMatches(lead, { minRating = "", noDnd = false, harvest = "", emailState = "" } = {}) {
  if (minRating && (ratingOf(lead) === null || ratingOf(lead) < Number(minRating))) return false;
  if (noDnd && isDnd(lead)) return false;
  if (emailState && emailStateOf(lead) !== emailState) return false;
  return !harvest || fromHarvest(lead, harvest);
}
// The same filters as download parameters (app/lead_grid.py).
export const gridParams = (has, { minRating = "", noDnd = false, harvest = "" } = {}) =>
  ({ has: has.join(","), min_rating: minRating, no_dnd: noDnd ? "1" : "", harvest: harvest === "__any" ? "any" : harvest });

// A short plain-English line about the campaign, ad or form behind a lead (shown as a tooltip and in the drawer).
export function attributionText(a = {}) {
  const parts = [a.campaign_name || a.utm_campaign, a.adset_name, a.ad_name, a.form_name || (a.form_id && `form ${a.form_id}`), a.utm_source && `source ${a.utm_source}`]
    .filter(Boolean);
  return [...new Set(parts)].join(" · ");
}