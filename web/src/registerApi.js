// The Indian company register (MCA Company Master Data, GODL): import, progress, matching leads (app/routes_company_register.py).
import { api, apiRequest as request } from "./api.js";

export const register = {
  summary: () => request("/api/company-register"),
  upload: (file) => api.upload(`/api/company-register/upload?filename=${encodeURIComponent(file.name)}`, file),
  importState: () => request("/api/company-register/import"),
  pull: (maxPages = 50) => request("/api/company-register/pull", { method: "POST", body: { max_pages: maxPages } }),
  pullState: () => request("/api/company-register/pull"),
  batchStart: (limit = 500) => request("/api/company-register/batch", { method: "POST", body: { limit } }),
  batchState: () => request("/api/company-register/batch"),
  match: (leadId) => request(`/api/leads/${leadId}/company-register`, { method: "POST" }),
  confirm: (leadId, cin) => request(`/api/leads/${leadId}/company-register/confirm`, { method: "POST", body: { cin } }),
  reject: (leadId, cin) => request(`/api/leads/${leadId}/company-register/reject`, { method: "POST", body: { cin } }),
};

// Rupees in the Indian grouping (₹10,00,000); "" when unknown.
export const rupees = (n) => (n === null || n === undefined || n === "" ? "" : `₹${Number(n).toLocaleString("en-IN")}`);
export const megabytes = (bytes) => `${((bytes || 0) / (1024 * 1024)).toFixed(bytes > 100 * 1024 * 1024 ? 0 : 1)} MB`;
export const LICENCE_SHORT = "Source: Company Master Data, Ministry of Corporate Affairs, via data.gov.in (GODL).";
