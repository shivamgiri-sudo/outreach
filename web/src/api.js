const TOKEN_KEY = "outreach-token";
export const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
export const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } };

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (res.status === 401 && !path.startsWith("/api/login")) {
    setToken("");
    window.dispatchEvent(new Event("auth-expired"));
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

async function download(path) {
  const token = getToken();
  const res = await fetch(path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  if (!res.ok) throw new Error(`Download failed (${res.status})`);
  return res.blob();
}

export const api = {
  download,
  dashboard: () => request("/api/dashboard"),
  today: () => request("/api/today"),
  taskDone: (taskIndex) => request("/api/tasks/done", { method: "POST", body: { task_index: taskIndex } }),
  leads: () => request("/api/leads"),
  lead: (id) => request(`/api/leads/${id}`),
  importCsv: (csv, source) => request("/api/leads/import", { method: "POST", body: { csv, source } }),
  moveStage: (id, stage) => request(`/api/leads/${id}/stage`, { method: "POST", body: { stage } }),
  consent: (id, text, channel = "any") => request(`/api/leads/${id}/consent`, { method: "POST", body: { text, channel, source: "recorded by a person" } }),
  note: (id, text, kind = "note") => request(`/api/leads/${id}/note`, { method: "POST", body: { text, kind } }),
  enroll: (id) => request(`/api/leads/${id}/enroll`, { method: "POST" }),
  reply: (id, text) => request(`/api/leads/${id}/reply`, { method: "POST", body: { text } }),
  runSequences: () => request("/api/sequences/run", { method: "POST", body: {} }),
  outbox: () => request("/api/outbox"),
  sendingStatus: () => request("/api/sending/status"),
  resumeSending: () => request("/api/sending/resume", { method: "POST" }),
  resumeFailed: () => request("/api/sequences/resume-failed", { method: "POST" }),
  resumeLead: (id) => request(`/api/leads/${id}/sequence/resume`, { method: "POST" }),
  clearStuck: (id) => request(`/api/leads/${id}/sequence/clear-stuck`, { method: "POST" }),
  clearPractice: () => request("/api/practice/clear", { method: "POST" }),
  inbox: () => request("/api/inbox"),
  settings: () => request("/api/settings"),
  saveSettings: (s) => request("/api/settings", { method: "PUT", body: s }),
  scrape: (body) => request("/api/scrape/extract", { method: "POST", body }),
  ingestJobs: (body) => request("/api/jobs/ingest", { method: "POST", body }),
  importSheet: (csv, tab) => request("/api/leads/import-sheet", { method: "POST", body: { csv, tab } }),
  enrich: (limit) => request("/api/enrich/run", { method: "POST", body: { limit } }),
  quality: () => request("/api/quality"),
  readiness: () => request("/api/readiness"),
  testIntegration: (id, to = "") => request(`/api/integrations/${id}/test`, { method: "POST", body: { to } }),
  authStatus: () => request("/api/auth/status"),
  login: (password) => request("/api/login", { method: "POST", body: { password } }),
  workflows: () => request("/api/workflows"),
  addWorkflow: (rule) => request("/api/workflows", { method: "POST", body: rule }),
  toggleWorkflow: (id, enabled) => request(`/api/workflows/${id}`, { method: "PUT", body: { enabled } }),
  deleteWorkflow: (id) => request(`/api/workflows/${id}`, { method: "DELETE" }),
  starterWorkflows: () => request("/api/workflows/starter", { method: "POST" }),
  runTimeRules: () => request("/api/workflows/run-time-rules", { method: "POST" }),
  draftReply: (id, incoming) => request(`/api/leads/${id}/draft-reply`, { method: "POST", body: { incoming } }),
  enrichApollo: (limit) => request("/api/enrich/apollo", { method: "POST", body: { limit } }),
  insights: () => request("/api/insights"),
  sendWhatsapp: (id, template, text = "") => request(`/api/leads/${id}/whatsapp`, { method: "POST", body: { template, text } }),
  whatsappOutbox: () => request("/api/whatsapp/outbox"),
  whatsappTemplates: () => request("/api/whatsapp/templates"),
  growth: () => request("/api/growth"),
  saveOffer: (offer) => request("/api/offer", { method: "PUT", body: offer }),
  sequence: () => request("/api/sequence"),
  demoLink: (id) => request(`/api/leads/${id}/demo-link`, { method: "POST" }),
  activationEvent: (id, event, count = 1) => request("/api/activation/event", { method: "POST", body: { lead_id: id, event, count } }),
  referral: (id, body) => request(`/api/leads/${id}/referral`, { method: "POST", body }),
  pilot: (id, applicants) => request(`/api/leads/${id}/pilot`, { method: "POST", body: { applicants } }),
  partners: () => request("/api/partners"),
  addPartner: (p) => request("/api/partners", { method: "POST", body: p }),
  partnerStatus: (id, status) => request(`/api/partners/${id}/status`, { method: "POST", body: { status } }),
  integrations: () => request("/api/integrations"),
  saveIntegration: (id, mode, values) => request(`/api/integrations/${id}`, { method: "PUT", body: { mode, values } }),
  stories: () => request("/api/stories"),
  addStory: (story) => request("/api/stories", { method: "POST", body: story }),
  generatePrompt: (spec) => request("/api/prompts/generate", { method: "POST", body: spec }),
  // Integrations hub
  channels: () => request("/api/channels"),
  saveChannel: (channel, provider, values, makeActive = false) =>
    request(`/api/channels/${channel}/${provider}`, { method: "PUT", body: { values, make_active: makeActive } }),
  deleteChannel: (channel, provider) => request(`/api/channels/${channel}/${provider}`, { method: "DELETE" }),
  testChannel: (channel, provider, body = {}) => request(`/api/channels/${channel}/${provider}/test`, { method: "POST", body }),
  setActiveChannel: (channel, provider) => request(`/api/channels/${channel}/active`, { method: "POST", body: { provider } }),
  channelWebhooks: (channel = "", provider = "") =>
    request(`/api/channels/webhooks${channel ? `?channel=${channel}&provider=${provider}` : ""}`),
  rotateWebhook: (channel, provider) => request(`/api/channels/${channel}/${provider}/rotate-secret`, { method: "POST" }),
  setLive: (flags) => request("/api/channels/live", { method: "PUT", body: flags }),
  pollImap: () => request("/api/channels/inbound_email/imap/poll", { method: "POST" }),
  oauthStart: (provider, returnTo) => request(`/api/oauth/${provider}/start?return_to=${encodeURIComponent(returnTo)}`),
  meetings: (from = "", to = "") => request(`/api/meetings${from || to ? `?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}` : ""}`),
  leadMeetings: (id) => request(`/api/leads/${id}/meetings`),
  bookMeeting: (id, body) => request(`/api/leads/${id}/meetings`, { method: "POST", body }),
  cancelMeeting: (meetingId) => request(`/api/meetings/${meetingId}/cancel`, { method: "POST" }),
};
