"use strict";

const $ = (id) => document.getElementById(id);

function api(method, path, query, body) {
  return chrome.runtime.sendMessage({ type: "api", method, path, query, body });
}

function safeHttp(url) { return /^https?:\/\/[^\s<>"']+$/i.test(url || "") ? url : ""; }

function serverOrigin(raw) {
  let u;
  try { u = new URL(raw.trim()); } catch (_) { return null; }
  const local = u.hostname === "localhost" || u.hostname === "127.0.0.1";
  if (u.protocol !== "https:" && !(u.protocol === "http:" && local)) return null;
  return u.origin;
}

function describeError(res) {
  if (res.error === "not_paired") return "Not paired yet. Open Settings and paste the pairing token.";
  if (res.error === "token_rejected") return "The server rejected the token (revoked or expired). Make a new one in the app.";
  if (res.error === "unreachable") return "Cannot reach the server. Check the address and your connection.";
  return typeof res.error === "string" ? res.error : "Something went wrong.";
}

function line(parent, text, cls) {
  const d = document.createElement("div");
  d.textContent = text;
  if (cls) d.className = cls;
  parent.appendChild(d);
}

async function refresh() {
  const status = $("status");
  const list = $("queue");
  list.replaceChildren();
  $("queue-hint").textContent = "";
  const res = await api("GET", "/api/extension/queue");
  $("open-panel").hidden = !res.ok;
  if (!res.ok) {
    status.textContent = describeError(res);
    status.className = "status bad";
    $("settings").open = true;
    return;
  }
  status.textContent = "Connected. Queue for " + (res.data.owner || "today") + ".";
  status.className = "status good";
  const due = res.data.due || [];
  $("queue-hint").textContent = due.length ? due.length + " to send today. Open a profile, then use the panel." : "Nothing due today.";
  for (const d of due) {
    const li = document.createElement("li");
    line(li, (d.name || "Contact") + " · " + (d.company || ""), "name");
    line(li, "Step " + d.step + (d.kind === "connect" ? " · connection request" : " · message"), "sub");
    const url = safeHttp(d.profile_url);
    if (url) {
      const a = document.createElement("a");
      a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer"; a.textContent = "Open profile";
      li.appendChild(a);
    } else {
      line(li, "No profile link on this lead.", "sub");
    }
    list.appendChild(li);
  }
  const waiting = res.data.waiting_for_acceptance || [];
  if (waiting.length) {
    const li = document.createElement("li");
    line(li, waiting.length + " waiting for a connection to be accepted", "sub");
    list.appendChild(li);
  }
}

$("save").addEventListener("click", async () => {
  const origin = serverOrigin($("server").value);
  const token = $("token").value.trim();
  const status = $("status");
  if (!origin) { status.textContent = "Use an https:// address (http:// only for localhost)."; status.className = "status bad"; return; }
  if (!token && !(await chrome.storage.local.get("token")).token) { status.textContent = "Paste the pairing token."; status.className = "status bad"; return; }
  // must be called directly from the click so Chrome shows its permission prompt for this one server
  const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
  if (!granted) { status.textContent = "Permission for your server was not granted."; status.className = "status bad"; return; }
  const update = { server: origin };
  if (token) update.token = token;
  await chrome.storage.local.set(update);
  $("token").value = "";
  await refresh();
});

$("forget").addEventListener("click", async () => {
  await chrome.storage.local.remove(["token", "server"]);
  $("server").value = "";
  await refresh();
});

$("open-panel").addEventListener("click", async () => {
  const win = await chrome.windows.getCurrent();
  await chrome.sidePanel.open({ windowId: win.id });
  window.close();
});

(async () => {
  const { server = "" } = await chrome.storage.local.get("server");
  $("server").value = server;
  await refresh();
})();
