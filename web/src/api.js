const TOKEN_KEY = "outreach-token";
export const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
export const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* storage blocked */ } };

// The signed-in person's role (owner / caller / viewer), set by App from /api/me; pages hide what the role can't do.
let role = "owner";
export const getRole = () => role;
export const setRole = (r) => { role = r || "owner"; };
export const isOwner = () => role === "owner";
let canEdit = true;
export const canEditContacts = () => canEdit;
export const setCanEditContacts = (v) => { canEdit = v !== false; };

// The chosen workspace (a client's own leads); sent on every request, remembered per browser. "" = all workspaces.
const WS_KEY = "outreach-workspace";
export const getWorkspace = () => { try { return localStorage.getItem(WS_KEY) || ""; } catch { return ""; } };
export const setWorkspace = (w) => { try { w ? localStorage.setItem(WS_KEY, w) : localStorage.removeItem(WS_KEY); } catch { /* storage blocked */ } };

// The server re-issues the token once half its 12 hours have passed; keep the newest one.
const keepRenewed = (res) => { const t = res.headers.get("X-Renewed-Token"); if (t && getToken()) setToken(t); };

async function request(path, options = {}) {
  const token = getToken();
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(getWorkspace() ? { "X-Workspace": getWorkspace() } : {}) },
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
  editLead: (id, changes, reason = "") => request(`/api/leads/${id}`, { method: "PATCH", body: { ...changes, reason } }),
  leadChanges: (id) => request(`/api/leads/${id}/changes`),
  changeLog: (limit = 300) => request(`/api/change-log?limit=${limit}`),
  remindStale: () => request("/api/intelligence/remind-stale", { method: "POST" }),
  async downloadChangeLog() {
    const token = getToken();
    const res = await fetch("/api/change-log.csv", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new Error(`Could not export (${res.status})`);
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: "change-log.csv" });
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url);
  },
  lists: () => request("/api/lists"),
  list: (id) => request(`/api/lists/${id}`),
  listCompanies: () => request("/api/lists/companies"),
  enrichedSummary: () => request("/api/lists/enriched-summary"),
  createList: (body) => request("/api/lists", { method: "POST", body }),
  listMembers: (id, body) => request(`/api/lists/${id}/members`, { method: "POST", body }),
  deleteList: (id) => request(`/api/lists/${id}`, { method: "DELETE" }),
  listStartEmail: (id, leadIds) => request(`/api/lists/${id}/start-email`, { method: "POST", body: { lead_ids: leadIds || null } }),
  listStartLinkedIn: (id, leadIds) => request(`/api/lists/${id}/start-linkedin`, { method: "POST", body: { lead_ids: leadIds || null } }),
  intelligence: () => request("/api/intelligence"),
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
  replyInbox: (q = {}) => request(`/api/reply-inbox?${new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v !== undefined).map(([k, v]) => [k, String(v)]))}`),
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
  workspaces: () => request("/api/workspaces"),
  sequencePreview: (index) => request(`/api/sequence/${index}/preview`),
  sendSequencePreview: (index, to) => request(`/api/sequence/${index}/send-preview`, { method: "POST", body: { to } }),
  companyHealth: (leadId) => request(`/api/leads/${leadId}/company-health`),
  refreshCompanyHealth: (leadId) => request(`/api/leads/${leadId}/company-health`, { method: "POST" }),
  historySummary: (leadId) => request(`/api/leads/${leadId}/history-summary`),
  emailCompose: (leadId, body) => request(`/api/leads/${leadId}/email-compose`, { method: "POST", body }),
  emailNow: (leadId) => request(`/api/leads/${leadId}/email-now`, { method: "POST" }),
  createWorkspace: (name) => request("/api/workspaces", { method: "POST", body: { name } }),
  deleteWorkspace: (id) => request(`/api/workspaces/${encodeURIComponent(id)}`, { method: "DELETE" }),
  moveToWorkspace: (id, leadIds) => request(`/api/workspaces/${encodeURIComponent(id)}/move`, { method: "POST", body: { lead_ids: leadIds } }),
  linkedinVariants: () => request("/api/linkedin/variants"),
  saveLinkedinVariants: (body) => request("/api/linkedin/variants", { method: "PUT", body }),
  scoreLinkedinNote: (text) => request("/api/linkedin/variants/score", { method: "POST", body: { text } }),
  generateLinkedinNotes: (context = "") => request("/api/linkedin/variants/generate", { method: "POST", body: { context, n: 4 } }),
  saveLinkedinSettings: (body) => request("/api/linkedin/settings", { method: "PUT", body }),
  linkedinStart: (id) => request(`/api/leads/${id}/linkedin/start`, { method: "POST" }),
  linkedinSent: (id, step) => request(`/api/leads/${id}/linkedin/sent`, { method: "POST", body: { step } }),
  linkedinAccepted: (id) => request(`/api/leads/${id}/linkedin/accepted`, { method: "POST" }),
  linkedinReplied: (id, text) => request(`/api/leads/${id}/linkedin/replied`, { method: "POST", body: { text } }),
  linkedinStop: (id) => request(`/api/leads/${id}/linkedin/stop`, { method: "POST" }),
  linkedinBranch: (id, body) => request(`/api/leads/${id}/linkedin/branch`, { method: "POST", body }),
  linkedinAgent: () => request("/api/linkedin-agent"),
  saveLinkedinAgent: (body) => request("/api/linkedin-agent", { method: "PUT", body }),
  extensionPair: (label) => request("/api/extension/pair", { method: "POST", body: { label } }),
  extensionTokens: () => request("/api/extension/tokens"),
  extensionRevoke: (id) => request(`/api/extension/tokens/${id}`, { method: "DELETE" }),
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
  sources: () => request("/api/sources"),
  testSource: (id) => request(`/api/sources/${id}/test`, { method: "POST" }),
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
  hiringSummary: () => request("/api/hiring-engine/summary"),
  hiringRun: (body) => request("/api/hiring-engine/run", { method: "POST", body }),
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
  icp: () => request("/api/icp"),
  saveIcp: (body) => request("/api/icp", { method: "PUT", body }),
  scoreIcp: (body) => request("/api/icp/score", { method: "POST", body }),
  signals: () => request("/api/signals"),
  addWatch: (kind, text) => request("/api/signals/watch", { method: "POST", body: { kind, text } }),
  dropWatch: (id) => request(`/api/signals/watch/${id}`, { method: "DELETE" }),
  runSignals: () => request("/api/signals/run", { method: "POST" }),
  signalStatus: (id, status) => request(`/api/signals/items/${id}/status`, { method: "POST", body: { status } }),
  signalToLead: (id, company) => request(`/api/signals/items/${id}/lead`, { method: "POST", body: { company } }),
  exportFlow: (id) => request(`/api/flows/${id}/export`),
  importFlow: (body) => request("/api/flows/import", { method: "POST", body }),
  flowWebhook: (id) => request(`/api/flows/${id}/webhook`),
  flowRuns: (id) => request(`/api/flows/${id}/runs`),
  leadFlows: (id) => request(`/api/leads/${id}/flows`),
  stopFlowRun: (id) => request(`/api/flow-runs/${id}/stop`, { method: "POST" }),
  draftReply: (id, incoming) => request(`/api/leads/${id}/draft-reply`, { method: "POST", body: { incoming } }),
  enrichApollo: (limit) => request("/api/enrich/apollo", { method: "POST", body: { limit } }),
  insights: () => request("/api/insights"),
  saveSourceSpend: (spend) => request("/api/insights/source-spend", { method: "PUT", body: { spend } }),
  leadSources: () => request("/api/lead-sources"),
  // Lead capture connectors (owner only)
  captureCatalogue: () => request("/api/capture/catalogue"),
  captureConnectors: () => request("/api/capture/connectors"),
  addCaptureConnector: (body) => request("/api/capture/connectors", { method: "POST", body }),
  saveCaptureConnector: (id, body) => request(`/api/capture/connectors/${id}`, { method: "PUT", body }),
  deleteCaptureConnector: (id) => request(`/api/capture/connectors/${id}`, { method: "DELETE" }),
  rotateCaptureToken: (id, which = "capture") => request(`/api/capture/connectors/${id}/rotate-token`, { method: "POST", body: { which } }),
  removeTestLeads: (id) => request(`/api/capture/connectors/${id}/test-leads`, { method: "DELETE" }),
  captureTest: (id) => request(`/api/inbound/${id}/test`, { method: "POST" }),
  captureSync: (id) => request(`/api/inbound/${id}/sync`, { method: "POST" }),
  captureLog: (connectorId = "") => request(`/api/inbound/log${connectorId ? `?connector_id=${connectorId}` : ""}`),
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
  // Lead owner, tags, bulk actions, saved views, scoring settings
  leadOwners: () => request("/api/lead-owners"),
  setLeadOwner: (id, owner) => request(`/api/leads/${id}/owner`, { method: "POST", body: { owner } }),
  changeLeadTags: (id, add = [], remove = []) => request(`/api/leads/${id}/tags`, { method: "POST", body: { add, remove } }),
  leadTags: () => request("/api/lead-tags"),
  bulkLeads: (leadIds, action, value = null, force = false) => request("/api/leads/bulk", { method: "POST", body: { lead_ids: leadIds, action, value, force } }),
  leadViews: () => request("/api/lead-views"),
  saveLeadView: (name, filters) => request("/api/lead-views", { method: "POST", body: { name, filters } }),
  deleteLeadView: (id) => request(`/api/lead-views/${id}`, { method: "DELETE" }),
  scoring: () => request("/api/scoring"),
  saveScoring: (body) => request("/api/scoring", { method: "PUT", body }),
};
