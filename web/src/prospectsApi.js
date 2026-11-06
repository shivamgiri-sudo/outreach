// Prospect database calls (Prospect search page). Lists go out as repeated query params.
import { apiDownload, apiRequest as request } from "./api.js";

export function filterQuery(filters = {}, extra = {}) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries({ ...filters, ...extra })) {
    if (v === undefined || v === null || v === "" || v === false) continue;
    if (Array.isArray(v)) v.forEach((x) => x !== "" && q.append(k, String(x)));
    else q.append(k, v === true ? "1" : String(v));
  }
  return q.toString();
}

export const prospects = {
  stats: () => request("/api/prospects/stats"),
  facets: () => request("/api/prospects/facets"),
  people: (filters, sort, page, size = 25) => request(`/api/prospects/people?${filterQuery(filters, { sort, page, size })}`),
  companies: (filters, sort, page, size = 25) => request(`/api/prospects/companies?${filterQuery(filters, { sort, page, size })}`),
  person: (id) => request(`/api/prospects/people/${encodeURIComponent(id)}`),
  company: (key) => request(`/api/prospects/companies/${encodeURIComponent(key)}`),
  addToLeads: (body) => request("/api/prospects/add-to-leads", { method: "POST", body }),
  saved: () => request("/api/prospects/saved-searches"),
  saveSearch: (body) => request("/api/prospects/saved-searches", { method: "POST", body }),
  deleteSearch: (id) => request(`/api/prospects/saved-searches/${id}`, { method: "DELETE" }),
  exportCsv: (body) => apiDownload("/api/prospects/export", { body, withName: true }),
  importCsv: (csv, source) => request("/api/prospects/import", { method: "POST", body: { csv, source } }),
  suppress: (value, reason) => request("/api/prospects/suppress", { method: "POST", body: { value, reason } }),
  erase: (id) => request(`/api/prospects/people/${encodeURIComponent(id)}`, { method: "DELETE" }),
  syncCrm: (force = false) => request("/api/prospects/sync-crm", { method: "POST", body: { force } }),
  settings: () => request("/api/prospects/settings"),
  saveSettings: (body) => request("/api/prospects/settings", { method: "PUT", body }),
  estimate: (personIds) => request("/api/prospects/enrich/estimate", { method: "POST", body: { person_ids: personIds } }),
  enrich: (personIds) => request("/api/prospects/enrich", { method: "POST", body: { person_ids: personIds } }),
  spend: () => request("/api/prospects/spend"),
  saveSpend: (body) => request("/api/prospects/spend", { method: "PUT", body }),
};
