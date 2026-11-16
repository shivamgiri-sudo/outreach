// Lead harvest (app/routes_harvest.py): keyword x location jobs, their results, CSV export and DND numbers.
import { apiDownload, apiRequest as request } from "./api.js";

async function save(path, fallback) {
  const { blob, filename } = await apiDownload(path, { withName: true });
  const url = URL.createObjectURL(blob);
  const a = Object.assign(document.createElement("a"), { href: url, download: filename || fallback });
  document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
}

export const harvestApi = {
  overview: () => request("/api/harvest"),
  estimate: (body) => request("/api/harvest/estimate", { method: "POST", body }),
  create: (body) => request("/api/harvest/jobs", { method: "POST", body }),
  jobs: () => request("/api/harvest/jobs"),
  job: (id) => request(`/api/harvest/jobs/${encodeURIComponent(id)}`),
  act: (id, action) => request(`/api/harvest/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" }),
  results: (id, outcome = "") => request(`/api/harvest/jobs/${encodeURIComponent(id)}/results${outcome ? `?outcome=${encodeURIComponent(outcome)}` : ""}`),
  exportCsv: (id) => save(`/api/harvest/jobs/${encodeURIComponent(id)}/export.csv`, `harvest-${id}.csv`),
  template: () => save("/api/harvest/template.csv", "harvest-template.csv"),
  uploadDnd: (text) => request("/api/harvest/dnd", { method: "POST", body: { text } }),
};
