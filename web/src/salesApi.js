// Sales funnel calls (app/routes_funnel.py).
import { apiRequest as request } from "./api.js";

const query = (params) => {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (Array.isArray(v)) v.forEach((x) => q.append(k, x));
    else if (v !== "" && v !== null && v !== undefined) q.set(k, v);
  });
  const text = q.toString();
  return text ? `?${text}` : "";
};

// filters: {from, to, source: [], industry: [], service: [], owner: []}
export const sales = {
  funnel: (filters, since = "") => request(`/api/funnel${query({ ...filters, since })}`),
  drill: (key, filters) => request(`/api/funnel/drill${query({ ...filters, key })}`),
  settings: () => request("/api/funnel/settings"),
  saveSettings: (body) => request("/api/funnel/settings", { method: "PUT", body }),
  recalculate: () => request("/api/funnel/recalculate", { method: "POST" }),
  lead: (id) => request(`/api/leads/${id}/sales`),
  saveLead: (id, body) => request(`/api/leads/${id}/sales`, { method: "PUT", body }),
};
