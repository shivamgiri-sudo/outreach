// Sign-up, Google / Microsoft sign-in, magic links, the platform banners and the super-admin console
// (server: app/routes_signup.py, app/routes_console.py).
import { apiDownload, apiRequest as request, getToken, setToken } from "./api.js";

const post = (path, body) => request(path, { method: "POST", body: body || {} });

// POST /api/signup answers 400 with {detail: {message, challenge}} when it wants the small sum: keep the challenge.
async function signupPost(body) {
  const res = await fetch("/api/signup", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (res.ok) return data;
  const detail = data.detail;
  const err = new Error(typeof detail === "string" ? detail : detail?.message || `Request failed (${res.status})`);
  if (detail?.challenge) err.challenge = detail.challenge;
  throw err;
}

export const platform = {
  signupConfig: () => request("/api/signup/config"),
  signupForm: () => request("/api/signup/form"),
  signup: signupPost,
  verifySignup: (token) => post("/api/signup/verify", { token }),
  oidcSignup: (body) => post("/api/signup/oidc", body),
  oidcStart: (provider, intent) => request(`/api/oidc/${encodeURIComponent(provider)}/start?intent=${intent}`),
  oidcCallback: (code, state) => post("/api/oidc/callback", { code, state }),
  magicLink: (email) => post("/api/magic-link", { email }),
  magicVerify: (token) => post("/api/magic-link/verify", { token }),
  pending: () => request("/api/signup/pending"),
  status: () => request("/api/platform/status"),
  // console (super admins)
  console: (status = "") => request(`/api/console/accounts${status ? `?status=${encodeURIComponent(status)}` : ""}`),
  approve: (id) => post(`/api/console/accounts/${encodeURIComponent(id)}/approve`),
  reject: (id, reason) => post(`/api/console/accounts/${encodeURIComponent(id)}/reject`, { reason }),
  ask: (id, message) => post(`/api/console/accounts/${encodeURIComponent(id)}/ask`, { message }),
  suspend: (id, reason) => post(`/api/console/accounts/${encodeURIComponent(id)}/suspend`, { reason }),
  reactivate: (id) => post(`/api/console/accounts/${encodeURIComponent(id)}/reactivate`),
  remove: (id, confirm) => post(`/api/console/accounts/${encodeURIComponent(id)}/delete`, { confirm }),
  approveWorkspace: (id) => post(`/api/workspaces/${encodeURIComponent(id)}/approve`),
  rejectWorkspace: (id) => post(`/api/workspaces/${encodeURIComponent(id)}/reject`),
  setSignupOpen: (on) => request("/api/console/signup-open", { method: "PUT", body: { on, reason: "" } }),
  stopSending: (on, reason = "") => request("/api/console/stop-sending", { method: "PUT", body: { on, reason } }),
  accountAudit: () => request("/api/account/audit"),
  usage: (accountId = "") => request(`/api/account/usage${accountId ? `?account_id=${encodeURIComponent(accountId)}` : ""}`),
  whatsapp: () => request("/api/account/whatsapp"),
  saveWhatsapp: (body) => request("/api/account/whatsapp", { method: "PUT", body }),
  removeWhatsapp: () => request("/api/account/whatsapp", { method: "DELETE" }),
  saveAlerts: (body) => request("/api/account/alerts", { method: "PUT", body }),
  releaseHold: (id) => post(`/api/console/accounts/${encodeURIComponent(id)}/release-hold`),
  async exportAccount(id, name) {
    const { blob, filename } = await apiDownload(`/api/console/accounts/${encodeURIComponent(id)}/export`, { withName: true });
    const url = URL.createObjectURL(blob);
    const a = Object.assign(document.createElement("a"), { href: url, download: filename || `account-${name || id}.zip` });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
};

// View as: the super admin's own token is kept aside in this tab while the read-only token is in use.
const SUPER_KEY = "outreach-token-super";
export async function startViewAs(email) {
  const out = await post("/api/console/view-as", { email });
  try { sessionStorage.setItem(SUPER_KEY, getToken()); } catch { /* storage blocked */ }
  setToken(out.token);
  window.location.hash = "";
  window.location.reload();
}
export async function stopViewAs() {
  await post("/api/logout").catch(() => {});
  let back = "";
  try { back = sessionStorage.getItem(SUPER_KEY) || ""; sessionStorage.removeItem(SUPER_KEY); } catch { /* storage blocked */ }
  setToken(back);
  window.location.hash = "#setup/accounts";
  window.location.reload();
}
