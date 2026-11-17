// Analytics, outbox paging, the Send-for-real switch, the audit trail and system health.
import { apiDownload, apiRequest as request } from "./api.js";

// {days: 30} or {from: "2026-11-01", to: "2026-11-07"} -> "from=…&to=…" (empty values left out)
export function rangeQuery(range = {}) {
  const params = new URLSearchParams();
  if (range.from || range.to) {
    if (range.from) params.set("from", range.from);
    if (range.to) params.set("to", range.to);
  } else if (range.days) params.set("days", String(range.days));
  return params.toString();
}

const query = (params) => {
  const q = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined)).toString();
  return q ? `?${q}` : "";
};

export const ops = {
  analytics: (range) => request(`/api/analytics?${rangeQuery(range)}`),
  overview: (range) => request(`/api/analytics/overview?${rangeQuery(range)}`),
  dailyReport: (range, who = "", kind = "") => request(`/api/reports/daily?${rangeQuery(range)}${query({ who, kind }).replace("?", "&")}`),
  emailReport: (range, who = "", kind = "", sendTo = []) => request("/api/reports/email", { method: "POST",
    body: { ...(range.from || range.to ? { from: range.from || "", to: range.to || "" } : { days: range.days || 30 }), who, kind, send_to: sendTo } }),
  async dailyReportCsv(range, who = "", kind = "") {
    const { blob, filename } = await apiDownload(`/api/reports/daily.csv?${rangeQuery(range)}${query({ who, kind }).replace("?", "&")}`, { withName: true });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename || "activity-log.csv" });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  pipeline: (range, added) => request(`/api/analytics/pipeline?${rangeQuery(range)}${added ? "&added=true" : ""}`),
  pipelineLeads: ({ step, owner = "", source = "", added }, range) => request(`/api/analytics/pipeline/leads?${new URLSearchParams({ step, owner, source, ...(added ? { added: "true" } : {}) })}&${rangeQuery(range)}`),
  intelligence: (range) => request(`/api/intelligence${range ? `?${rangeQuery(range)}` : ""}`),
  insights: (range) => request(`/api/insights${range ? `?${rangeQuery(range)}` : ""}`),
  outboxPage: (params) => request(`/api/outbox${query({ page: 1, ...params })}`),
  outboxMessage: (key) => request(`/api/outbox/message?key=${encodeURIComponent(key)}`),
  outboxInternal: (params = {}) => request(`/api/outbox/internal${query(params)}`),
  async outboxAuditCsv(params = {}) {
    const { blob, filename } = await apiDownload(`/api/outbox/export.csv${query(params)}`, { withName: true });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename || "outbox-audit.csv" });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  liveSending: () => request("/api/settings/live-sending"),
  setLiveSending: (on, confirm = "") => request("/api/settings/live-sending", { method: "POST", body: { on, confirm } }),
  audit: (params) => request(`/api/audit${query(params)}`),
  auditCsvPath: (params) => `/api/audit.csv${query(params)}`,
  auditVerify: () => request("/api/audit/verify"),
  auditPackPath: (params) => `/api/audit/pack${query(params)}`,
  health: () => request("/api/system/health"),
  // Bulk email checker (owners and workspace admins): start a run, follow it, CSV of every row, valid new addresses.
  emailCheckStart: (text, filename = "") => request("/api/email-checker/runs", { method: "POST", body: { text, filename } }),
  emailCheckRun: (id) => request(`/api/email-checker/runs/${id}`),
  emailCheckValid: (id) => request(`/api/email-checker/runs/${id}/valid`),
  async emailCheckCsv(id) {
    const { blob, filename } = await apiDownload(`/api/email-checker/runs/${id}/export.csv`, { withName: true });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename || "email-check.csv" });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  // Reverse lookup on the same page: who is behind each address (free steps only), CSV, rows for the smart import.
  reverseStart: (text, filename = "") => request("/api/email-checker/reverse", { method: "POST", body: { text, filename } }),
  reverseRun: (id) => request(`/api/email-checker/reverse/${id}`),
  reverseImport: (id) => request(`/api/email-checker/reverse/${id}/import`),
  async reverseCsv(id) {
    const { blob, filename } = await apiDownload(`/api/email-checker/reverse/${id}/export.csv`, { withName: true });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename || "reverse-lookup.csv" });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  // Hiring now + company facts from the lead's own website: one lead (callers too), a batch of up to 50 sites (owners).
  hiringSignalRefresh: (leadId) => request(`/api/leads/${leadId}/hiring-signal`, { method: "POST" }),
  hiringBatchStart: (limit = 50) => request("/api/hiring-signal/run", { method: "POST", body: { limit } }),
  hiringBatchState: () => request("/api/hiring-signal/run"),
  domainBatchStart: (limit = 500) => request("/api/domain-signals/run", { method: "POST", body: { limit } }),
  domainBatchState: () => request("/api/domain-signals/run"),
  recrawl: () => request("/api/recrawl"),
  setRecrawl: (enabled, days) => request("/api/recrawl", { method: "PUT", body: { enabled, days: Number(days) } }),
  campaignLeadsPage: (id, params) => request(`/api/campaigns/${id}/leads${query({ page: 1, ...params })}`),
};
