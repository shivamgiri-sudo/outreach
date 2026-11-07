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
  liveSending: () => request("/api/settings/live-sending"),
  setLiveSending: (on, confirm = "") => request("/api/settings/live-sending", { method: "POST", body: { on, confirm } }),
  audit: (params) => request(`/api/audit${query(params)}`),
  auditCsvPath: (params) => `/api/audit.csv${query(params)}`,
  health: () => request("/api/system/health"),
  campaignLeadsPage: (id, params) => request(`/api/campaigns/${id}/leads${query({ page: 1, ...params })}`),
};
