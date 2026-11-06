/*
 * MCN LinkedIn Assistant - side panel. Every button here is pressed by the person. The panel never clicks
 * anything on LinkedIn; "Insert message" only asks the content script to fill the open box once.
 */
"use strict";

const view = document.getElementById("view");
let current = null;      // { tabId, profile, match }
let suggestion = "";     // read-only hint, e.g. "looks sent": only ever asks the person to confirm

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === "class") e.className = v; else if (k === "onclick") e.addEventListener("click", v); else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}
const api = (method, path, query, body) => chrome.runtime.sendMessage({ type: "api", method, path, query, body });
function show(...nodes) { view.replaceChildren(...nodes); }
function banner(text, cls) { return el("div", { class: "banner " + (cls || "") }, text); }

function errorText(res) {
  if (res.error === "not_paired") return "Not paired yet. Click the extension icon, open Settings and paste the pairing token.";
  if (res.error === "token_rejected") return "The server rejected the pairing token (revoked or expired). Make a new one in the app.";
  if (res.error === "unreachable") return "Cannot reach your server.";
  return typeof res.error === "string" ? res.error : "Something went wrong.";
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const self = chrome.tabs.getCurrent ? await chrome.tabs.getCurrent() : undefined;   // undefined in the real side panel
  if (!self || !tab || tab.id !== self.id) return tab || null;
  // Only when this panel page is itself open as a tab (e.g. in tests): use the active LinkedIn tab of another window.
  const others = await chrome.tabs.query({ active: true });
  return others.find((t) => t.id !== self.id && /^https:\/\/www\.linkedin\.com\//.test(t.url || "")) || null;
}

async function askPage(tabId, message) {
  try { return await chrome.tabs.sendMessage(tabId, message); } catch (_) { return null; }
}

async function load() {
  suggestion = "";
  show(el("p", { class: "hint" }, "Looking at this page…"));
  const tab = await activeTab();
  if (!tab || !/^https:\/\/www\.linkedin\.com\//.test(tab.url || "")) {
    return show(banner("Open a LinkedIn profile (a page starting linkedin.com/in/…) in this tab."));
  }
  const profile = await askPage(tab.id, { type: "read-profile" });
  if (!profile) return show(banner("Reload the LinkedIn tab once so the assistant can read the page, then press “Check this page again”."));
  if (!profile.onProfile) return show(banner("This is not a profile page. Open one person’s profile to see their message."));
  const res = await api("GET", "/api/extension/match", { profile_url: profile.url, name: profile.name, company: profile.company });
  if (!res.ok) return show(banner(errorText(res), "warn"));
  current = { tabId: tab.id, profile, match: res.data };
  if ((res.data.linkedin_seq || {}).status === "waiting_for_acceptance" && profile.degree === "1st") {
    suggestion = "LinkedIn now shows them as a 1st-degree connection. If they accepted, press “They accepted”.";
  }
  render();
}

function render() {
  const { profile, match } = current;
  const nodes = [];
  if (!match.matched) {
    nodes.push(el("div", { class: "card" }, el("b", {}, profile.name || "This person"), el("div", { class: "sub" }, profile.headline || "")));
    if (profile.singleProfile) {
      nodes.push(banner("This profile is not one of your leads yet, so there is nothing to send.", ""));
      nodes.push(el("div", { class: "row" }, el("button", { class: "primary", id: "save-profile", onclick: () => confirmSave("Save to portal") }, "Save to portal")));
      nodes.push(el("div", { id: "save-area" }));
    } else {
      nodes.push(banner("This profile is not one of your leads. Open the person’s main profile page (linkedin.com/in/…) to save them to the portal.", ""));
    }
    return show(...nodes);
  }
  const seq = match.linkedin_seq || {};
  nodes.push(el("div", { class: "card" }, el("b", {}, match.name || profile.name), el("div", {}, [match.title, match.company].filter(Boolean).join(" · ")),
    el("div", { class: "sub" }, "Sequence: " + (seq.status || "not started") + (seq.next_step ? " · next step " + seq.next_step : ""))));
  if (suggestion) nodes.push(banner(suggestion, "ok"));

  if (match.can_send && match.message) {
    const m = match.message;
    nodes.push(el("h2", {}, "Step " + m.step + (m.kind === "connect" ? ": connection request" : ": message")));
    if (m.purpose) nodes.push(el("div", { class: "sub" }, m.purpose));
    nodes.push(el("div", { class: "card msg", id: "message-text" }, m.text));
    nodes.push(el("div", { class: "row" },
      el("button", { onclick: copy }, "Copy"),
      el("button", { class: "primary", onclick: () => insert(false) }, "Insert message")));
    nodes.push(el("p", { class: "hint" }, m.kind === "connect"
      ? "On LinkedIn: click Connect, then “Add a note”, then press Insert message. Read it, then click Send yourself."
      : "On LinkedIn: click Message so the box is open, then press Insert message. Read it, then click Send yourself."));
    nodes.push(el("div", { id: "notice" }));
    nodes.push(el("h2", {}, "After you have sent it"));
    nodes.push(el("div", { class: "row" }, el("button", { class: "primary", onclick: () => confirmSent(m.step) }, "I sent it")));
  } else {
    nodes.push(banner(match.reason || "Nothing to send right now.", match.stage === "unsubscribed" || match.stage === "disqualified" ? "warn" : ""));
  }
  if (profile.singleProfile && missingFields().length) {
    nodes.push(el("div", { class: "row" }, el("button", { id: "save-profile", onclick: () => confirmSave("Update lead") }, "Update lead")));
    nodes.push(el("p", { class: "hint" }, "The lead is missing: " + missingFields().join(", ") + ". Update fills only empty fields."));
    nodes.push(el("div", { id: "save-area" }));
  }
  const actions = match.actions || [];
  if (actions.includes("accepted")) nodes.push(el("div", { class: "row" }, el("button", { class: "primary", onclick: confirmAccepted }, "They accepted")));
  if (actions.includes("replied")) {
    nodes.push(el("div", { class: "row" }, el("button", { onclick: showReply }, "They replied")));
    nodes.push(el("div", { id: "reply-area" }));
  }
  show(...nodes);
}

function notice(text, cls) {
  const n = document.getElementById("notice");
  if (n) n.replaceChildren(banner(text, cls));
}

async function copy() {
  try { await navigator.clipboard.writeText(current.match.message.text); notice("Copied. Paste it into LinkedIn yourself.", "ok"); }
  catch (_) { notice("Could not copy. Select the text above and copy it.", "warn"); }
}

async function insert(replace) {
  const r = await askPage(current.tabId, { type: "insert", text: current.match.message.text, replace });
  if (!r) return notice("Reload the LinkedIn tab, then try again.", "warn");
  if (r.ok) return notice("Inserted. Read it, edit if you like, then click LinkedIn’s Send button yourself.", "ok");
  if (r.reason === "no_box") return notice("No message box is open. Click LinkedIn’s Connect (then “Add a note”) or Message button first, then press Insert message again.", "warn");
  if (r.reason === "not_empty") {
    const n = document.getElementById("notice");
    return n.replaceChildren(banner("The box already has text. I will not overwrite it unless you say so.", "warn"),
      el("button", { onclick: () => insert(true) }, "Replace what is in the box"));
  }
  notice("Could not insert the message.", "warn");
}

async function record(path, body, okText) {
  const res = await api("POST", path, null, body);
  suggestion = "";
  if (!res.ok) { render(); return view.prepend(banner(errorText(res), "warn")); }
  current.match = res.data.match;
  render();
  view.prepend(banner(okText, "ok"));
}

function confirmSent(step) {
  const id = current.match.lead_id;
  record(`/api/extension/leads/${encodeURIComponent(id)}/sent`, { step }, "Recorded: step " + step + " sent.");
}
function confirmAccepted() {
  record(`/api/extension/leads/${encodeURIComponent(current.match.lead_id)}/accepted`, null, "Recorded: they accepted.");
}
function showReply() {
  const area = document.getElementById("reply-area");
  const box = el("textarea", { "aria-label": "What they wrote", placeholder: "Paste what they replied (optional)" });
  area.replaceChildren(el("label", {}, "What did they say?", box),
    el("button", { class: "primary", onclick: () => record(`/api/extension/leads/${encodeURIComponent(current.match.lead_id)}/replied`, { text: box.value.trim() }, "Recorded: they replied. The sequence is stopped.") }, "Save reply"));
  box.focus();
}

// ----- Save to portal: one profile per click, values checked and edited by the person before anything is sent -----
function missingFields() {
  const { profile, match } = current;
  const out = [];
  if (!match.name && profile.name) out.push("name");
  if (!match.title && profile.headline) out.push("title");
  if (!match.company && profile.company) out.push("company");
  if (match.has_profile_link === false) out.push("profile link");
  return out;
}

function confirmSave(label) {
  const area = document.getElementById("save-area");
  const { profile, match } = current;
  const field = (id, text, value, max) => {
    const input = el("input", { id, maxlength: String(max), "aria-label": text });
    input.value = value || "";
    return [el("label", { for: id }, text, input), input];
  };
  const [nameRow, name] = field("save-name", "Name", (match && match.name) || profile.name, 120);
  const [titleRow, title] = field("save-title", "Headline / title", profile.headline, 200);
  const [companyRow, company] = field("save-company", "Current company", profile.company, 200);
  const [placeRow, place] = field("save-location", "Location (optional)", profile.location, 200);
  const send = async () => {
    const body = { profile_url: profile.profileUrl || profile.url, name: name.value.trim(), title: title.value.trim(),
                   company: company.value.trim(), location: place.value.trim() };
    if (!body.name) return area.prepend(banner("Type the person’s name first.", "warn"));
    saveBtn.disabled = true;
    const res = await api("POST", "/api/extension/save-profile", null, body);
    if (!res.ok) { saveBtn.disabled = false; return area.prepend(banner(errorText(res), "warn")); }
    current.match = res.data.match;
    render();
    const what = res.data.result === "created" ? "Saved to the portal as a new lead."
      : res.data.result === "updated" ? "Lead updated: added " + res.data.changed.join(", ") + "."
      : "The lead already has all of this. Nothing changed.";
    view.prepend(banner(what, "ok"));
  };
  const saveBtn = el("button", { class: "primary", id: "save-confirm", onclick: send }, label === "Update lead" ? "Update lead" : "Save");
  area.replaceChildren(el("div", { class: "card", id: "save-confirm-box" },
    el("b", {}, "Check before saving"),
    el("div", { class: "sub" }, "Only this one profile is saved: " + (profile.profileUrl || profile.url)),
    nameRow, titleRow, companyRow, placeRow,
    el("div", { class: "row" }, saveBtn, el("button", { class: "ghost", onclick: () => area.replaceChildren() }, "Cancel"))));
  name.focus();
}

// Read-only hints. Each one only ASKS the person to confirm with a click; nothing is reported by itself.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (sender.id !== chrome.runtime.id || !msg || !current) return;
  if (msg.type === "box-closed" && current.match.can_send) {
    suggestion = "The message box closed. If you sent the message, press “I sent it” below.";
    render();
  }
});

document.getElementById("refresh").addEventListener("click", load);
chrome.tabs.onActivated.addListener(load);
chrome.tabs.onUpdated.addListener((id, info, tab) => { if (tab.active && (info.status === "complete" || info.url)) load(); });
load();
