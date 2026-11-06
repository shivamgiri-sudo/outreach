/*
 * MCN LinkedIn Assistant - background service worker.
 *
 * Its only job: talk to YOUR OWN MCN Outreach server with the pairing token and return the answer.
 * It never touches LinkedIn pages. The token is never logged and never sent anywhere but the server you paired.
 */
"use strict";

const ALLOWED = [
  ["GET", /^\/api\/extension\/match$/],
  ["GET", /^\/api\/extension\/queue$/],
  ["POST", /^\/api\/extension\/leads\/[A-Za-z0-9_-]+\/(sent|accepted|replied)$/],
  ["POST", /^\/api\/extension\/save-profile$/],                  // the person pressed Save in the confirm box
];

async function getConfig() {
  const { server = "", token = "" } = await chrome.storage.local.get(["server", "token"]);
  return { server, token };
}

async function callServer({ method, path, query, body }) {
  const { server, token } = await getConfig();
  if (!server || !token) return { ok: false, status: 0, error: "not_paired" };
  if (!ALLOWED.some(([m, rx]) => m === method && rx.test(path))) return { ok: false, status: 0, error: "path_not_allowed" };
  const url = new URL(server.replace(/\/+$/, "") + path);
  for (const [k, v] of Object.entries(query || {})) if (v) url.searchParams.set(k, String(v));
  try {
    const res = await fetch(url, {
      method,
      headers: { Authorization: "Bearer " + token, ...(body ? { "Content-Type": "application/json" } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(15000),
      credentials: "omit",
    });
    let data = null;
    try { data = await res.json(); } catch (_) { /* not JSON */ }
    if (res.status === 401) return { ok: false, status: 401, error: "token_rejected" };
    if (!res.ok) return { ok: false, status: res.status, error: (data && data.detail) || "server_error", data };
    return { ok: true, status: res.status, data };
  } catch (err) {
    return { ok: false, status: 0, error: "unreachable" };   // message deliberately not logged: it can contain the URL
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !msg || msg.type !== "api") return false;
  callServer(msg).then(sendResponse);
  return true;                                                // answer asynchronously
});
