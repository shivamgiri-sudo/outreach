// Domain trust signals on a lead (lead.domain_signals, from app/domain_signals.py). Pure functions, no imports, so the
// server test can run them under node: the same rules as app/domain_signals.is_young / no_mail_auth.
// An "unknown" result (the lookup failed) never counts.
export const YOUNG_DAYS = 365;
export const isYoungDomain = (lead) => { const a = lead?.domain_signals?.age_days; return Number.isInteger(a) && a < YOUNG_DAYS; };
export const noMailAuth = (lead) => { const s = lead?.domain_signals || {}; return s.spf === "missing" || s.dmarc === "missing"; };

const AGE = (days) => (days < 60 ? `${days} days old` : days < 730 ? `${Math.round(days / 30.4)} months old` : `${Math.floor(days / 365.25)} years old`);

// Short parts for the drawer row: [{ label, text, warn }].
export function domainParts(s) {
  if (!s) return [];
  const mx = s.mx === "unknown" ? "not known" : s.mx === "none" ? "no mail server" : s.mx;
  const dmarc = { none: "yes (monitor only)", quarantine: "yes (quarantine)", reject: "yes (reject)", missing: "no", unknown: "not known" }[s.dmarc] || "not known";
  return [
    { label: "Mail", text: mx, warn: s.mx === "none" },
    { label: "SPF", text: { present: "yes", missing: "no", unknown: "not known" }[s.spf] || "not known", warn: s.spf === "missing" },
    { label: "DMARC", text: dmarc, warn: s.dmarc === "missing" },
    { label: "Age", text: Number.isInteger(s.age_days) ? `${AGE(s.age_days)} (since ${s.registered_at})` : "not published", warn: isYoungDomain({ domain_signals: s }) },
  ];
}
