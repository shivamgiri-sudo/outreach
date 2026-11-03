const TOKEN_KEY = "outreach-token";
export const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
export const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } };

// The signed-in person's role (owner / caller / viewer), set by App from /api/me; pages hide what the role can't do.
let role = "owner";
export const getRole = () => role;
export const setRole = (r) => { role = r || "owner"; };
export const isOwner = () => role === "owner";

// The server re-issues the token once half its 12 hours have passed; keep the newest one.
const keepRenewed = (res) => { const t = res.headers.get("X-Renewed-Token"); if (t && getToken()) setToken(t); };

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  keepRenewed(res);
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

// The file name the server chose (Content-Disposition), or "".
function servedName(res) {
  const header = res.headers.get("content-disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(header);
  return match ? decodeURIComponent(match[1]) : "";
}

async function fileRequest(path, options = {}) {
  const token = getToken();
  const res = await fetch(path, {
    method: options.method || (options.body !== undefined ? "POST" : "GET"),
    headers: { ...(options.contentType ? { "Content-Type": options.contentType } : {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: options.body,
  });
  keepRenewed(res);
  if (res.status === 401) {
    setToken("");
    window.dispatchEvent(new Event("auth-expired"));
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `${options.what || "Download"} failed (${res.status})`);
  }
  return res;
}

// A plain link can't send the login token, so files are fetched with it. `body` (an object) sends a POST with JSON;
// `withName` also returns the file name the server chose.
async function download(path, options = {}) {
  const json = options.body !== undefined;
  const res = await fileRequest(path, { body: json ? JSON.stringify(options.body) : undefined, contentType: json ? "application/json" : undefined });
  const blob = await res.blob();
  return options.withName ? { blob, filename: servedName(res) } : blob;
}

// Sends a file as the raw request body (used for restoring a backup) and returns the JSON answer.
async function upload(path, file) {
  const res = await fileRequest(path, { method: "POST", body: file, contentType: "application/octet-stream", what: "Upload" });
  return res.json();
}

export const api = {
  download,
  upload,
  restoreBackup: (file, { confirm = false, keepSecrets = false } = {}) =>
    upload(`/api/backup/restore?${confirm ? "confirm=1&dry_run=0" : "dry_run=1"}${keepSecrets ? "&keep_secrets=1" : ""}`, file),
  dashboard: () => request("/api/dashboard"),
  today: () => request("/api/today"),
  taskDone: (taskIndex) => request("/api/tasks/done", { method: "POST", body: { task_index: taskIndex } }),
  leads: () => request("/api/leads"),
  lead: (id) => request(`/api/leads/${id}`),
  importCsv: (csv, source) => request("/api/leads/import", { method: "POST", body: { csv, source } }),
  importPreview: (body) => request("/api/import/preview", { method: "POST", body }),
  importCommit: (body) => request("/api/import/commit", { method: "POST", body }),
  importHistory: () => request("/api/import/history"),
  importUndo: (id) => request(`/api/import/${id}/undo`, { method: "POST" }),
  hygieneReport: () => request("/api/hygiene/report"),
  hygieneMerge: (keepId, mergeIds) => request("/api/hygiene/merge", { method: "POST", body: { keep_id: keepId, merge_ids: mergeIds } }),
  hygieneStandardise: (dryRun) => request("/api/hygiene/standardise", { method: "POST", body: { dry_run: dryRun } }),
  resolveConflict: (leadId, conflictId, choice) =>
    request("/api/hygiene/resolve-conflict", { method: "POST", body: { lead_id: leadId, conflict_id: conflictId, choice } }),
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
  leadActivity: (id) => request(`/api/leads/${id}/activity`),
  logActivity: (id, body) => request(`/api/leads/${id}/activity`, { method: "POST", body }),
  taskBoard: (owner = "") => request(`/api/tasks/board${owner ? `?owner=${encodeURIComponent(owner)}` : ""}`),
  createTask: (body) => request("/api/tasks", { method: "POST", body }),
  updateTask: (id, body) => request(`/api/tasks/${id}`, { method: "PUT", body }),
  taskDoneById: (id, note = "") => request(`/api/tasks/${id}/done`, { method: "POST", body: { note } }),
  notifications: (unread = false) => request(`/api/notifications${unread ? "?unread=true" : ""}`),
  markNotificationsRead: () => request("/api/notifications/read", { method: "POST" }),
  findEstimate: (body) => request("/api/find-leads/estimate", { method: "POST", body }),
  findStart: (body) => request("/api/find-leads", { method: "POST", body }),
  findRun: (id) => request(`/api/find-leads/${id}`),
  findRecent: () => request("/api/find-leads"),
  linkedinResults: (csv) => request("/api/linkedin/results", { method: "POST", body: { csv } }),
  linkedinQueue: () => request("/api/linkedin/queue"),
  linkedinSettings: () => request("/api/linkedin/settings"),
  saveLinkedinSettings: (body) => request("/api/linkedin/settings", { method: "PUT", body }),
  linkedinStart: (id) => request(`/api/leads/${id}/linkedin/start`, { method: "POST" }),
  linkedinSent: (id, step) => request(`/api/leads/${id}/linkedin/sent`, { method: "POST", body: { step } }),
  linkedinAccepted: (id) => request(`/api/leads/${id}/linkedin/accepted`, { method: "POST" }),
  linkedinReplied: (id, text) => request(`/api/leads/${id}/linkedin/replied`, { method: "POST", body: { text } }),
  linkedinStop: (id) => request(`/api/leads/${id}/linkedin/stop`, { method: "POST" }),
  linkedinBranch: (id, body) => request(`/api/leads/${id}/linkedin/branch`, { method: "POST", body }),
  team: () => request("/api/team"),
  saveTeam: (body) => request("/api/team", { method: "PUT", body }),
  settings: () => request("/api/settings"),
  saveSettings: (s) => request("/api/settings", { method: "PUT", body: s }),
  scrape: (body) => request("/api/scrape/extract", { method: "POST", body }),
  ingestJobs: (body) => request("/api/jobs/ingest", { method: "POST", body }),
  importSheet: (csv, tab) => request("/api/leads/import-sheet", { method: "POST", body: { csv, tab } }),
  enrich: (limit) => request("/api/enrich/run", { method: "POST", body: { limit } }),
  quality: () => request("/api/quality"),
  // Campaigns: one-click find, enrich, check and (optionally) start the sequence
  campaigns: () => request("/api/campaigns"),
  campaign: (id) => request(`/api/campaigns/${id}`),
  campaignSources: () => request("/api/campaign-sources"),
  createCampaign: (body) => request("/api/campaigns", { method: "POST", body }),
  saveCampaign: (id, body) => request(`/api/campaigns/${id}`, { method: "PUT", body }),
  deleteCampaign: (id) => request(`/api/campaigns/${id}`, { method: "DELETE" }),
  runCampaign: (id, dryRun) => request(`/api/campaigns/${id}/run`, { method: "POST", body: { dry_run: dryRun } }),
  pauseCampaign: (id) => request(`/api/campaigns/${id}/pause`, { method: "POST" }),
  resumeCampaign: (id) => request(`/api/campaigns/${id}/resume`, { method: "POST" }),
  campaignRuns: (id) => request(`/api/campaigns/${id}/runs`),
  campaignLeads: (id) => request(`/api/campaigns/${id}/leads`),
  readiness: () => request("/api/readiness"),
  testIntegration: (id, to = "") => request(`/api/integrations/${id}/test`, { method: "POST", body: { to } }),
  authStatus: () => request("/api/auth/status"),
  login: (password, email = "") => request("/api/login", { method: "POST", body: { password, email } }),
  logout: () => request("/api/logout", { method: "POST" }),
  me: () => request("/api/me"),
  users: () => request("/api/users"),
  addUser: (body) => request("/api/users", { method: "POST", body }),
  changeUser: (email, body) => request(`/api/users/${encodeURIComponent(email)}`, { method: "PUT", body }),
  removeUser: (email) => request(`/api/users/${encodeURIComponent(email)}`, { method: "DELETE" }),
  eraseLead: (id) => request(`/api/leads/${id}/erase`, { method: "POST", body: { confirm: true } }),
  retention: () => request("/api/compliance/retention"),
  workflows: () => request("/api/workflows"),
  addWorkflow: (rule) => request("/api/workflows", { method: "POST", body: rule }),
  toggleWorkflow: (id, enabled) => request(`/api/workflows/${id}`, { method: "PUT", body: { enabled } }),
  deleteWorkflow: (id) => request(`/api/workflows/${id}`, { method: "DELETE" }),
  starterWorkflows: () => request("/api/workflows/starter", { method: "POST" }),
  runTimeRules: () => request("/api/workflows/run-time-rules", { method: "POST" }),
  // Flow builder
  flows: () => request("/api/flows"),
  flow: (id) => request(`/api/flows/${id}`),
  flowCatalog: () => request("/api/flows/catalog"),
  flowTemplates: () => request("/api/flows/templates"),
  flowFromTemplate: (key) => request(`/api/flows/templates/${key}`, { method: "POST" }),
  createFlow: (body) => request("/api/flows", { method: "POST", body }),
  saveFlow: (id, body) => request(`/api/flows/${id}`, { method: "PUT", body }),
  deleteFlow: (id) => request(`/api/flows/${id}`, { method: "DELETE" }),
  validateFlow: (id, graph) => request(`/api/flows/${id}/validate`, { method: "POST", body: graph }),
  activateFlow: (id) => request(`/api/flows/${id}/activate`, { method: "POST" }),
  pauseFlow: (id) => request(`/api/flows/${id}/pause`, { method: "POST" }),
  enrollFlow: (id, leadIds) => request(`/api/flows/${id}/enroll`, { method: "POST", body: { lead_ids: leadIds } }),
  simulateFlow: (id, leadId, assumeReply = "timeout") => request(`/api/flows/${id}/simulate`, { method: "POST", body: { lead_id: leadId, assume_reply: assumeReply } }),
  flowRuns: (id) => request(`/api/flows/${id}/runs`),
  leadFlows: (id) => request(`/api/leads/${id}/flows`),
  stopFlowRun: (id) => request(`/api/flow-runs/${id}/stop`, { method: "POST" }),
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
  meetingDone: (meetingId) => request(`/api/meetings/${meetingId}/done`, { method: "POST" }),
};
