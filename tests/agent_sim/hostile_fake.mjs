// Second hostile stand-in for LinkedIn (tests/agent_sim/hostile.py): behaviours the audits did not cover. One slug prefix per behaviour,
// names are "<Kind> Lead". Unlike the other fakes the conversation is PERSISTENT (built from what was sent), like the real thing.
//   conn-*       2nd degree, Connect + "Add a note"; the invitation is kept and the profile then shows Pending
//   popup-*      like conn-*, but pressing Connect also opens a second browser tab (an upsell)
//   msg-*        1st degree with Message; a normal chat that lists the sent text as ours
//   group-*      1st degree; Message opens a GROUP chat titled "<Name>, Bea Bee"
//   owndraft-*   1st degree; the chat box opens with the start of our own earlier text (set with POST /__draft?slug=..&text=..)
//   humandraft-* 1st degree; the chat box opens with somebody's own half-written text
//   newtab-*     1st degree; Send also opens a second tab
// GET /__state lists every invite and message.
import http from "node:http";

const PORTS = Object.fromEntries(String(process.argv[2] || "8942:acct-a").split(",").map((p) => p.split(":")).map(([port, a]) => [Number(port), a || ""]));
const state = { invites: [], messages: [], drafts: {}, tabs: 0 };
const cap = (w) => w[0].toUpperCase() + w.slice(1);
const nameOf = (slug) => cap(slug.split("-")[0]) + " Lead";
const page = (body, title = "LinkedIn") => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`;
const send = (res, code, html, headers = {}) => { res.writeHead(code, { "Content-Type": "text/html", ...headers }); res.end(html); };
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const J = JSON.stringify;

function profile(acct, slug) {
  const kind = slug.split("-")[0], nm = nameOf(slug);
  const invited = state.invites.some((i) => i.from === acct && i.to === slug);
  const first = !["conn", "popup"].includes(kind);
  const history = state.messages.filter((m) => m.from === acct && m.to === slug).map((m) => `<li class="msg-s-message-list__event"><span class="msg-s-message-group__name">Asha Account</span><div class="msg-s-event-listitem"><p class="msg-s-event-listitem__body">${esc(m.text)}</p></div></li>`).join("");
  const buttons = first ? `<button aria-label="Message ${nm}" id="msg">Message</button>`
    : invited ? `<button aria-label="Pending, click to withdraw invitation sent to ${nm}">Pending</button>`
    : `<button aria-label="Invite ${nm} to connect" id="connect">Connect</button>`;
  const title = kind === "group" ? `${nm}, Bea Bee` : nm;
  const draft = kind === "owndraft" ? (state.drafts[slug] || "") : kind === "humandraft" ? "Hi, quick q (my own half-written draft)" : "";
  return page(`<main><section><h1>${nm}</h1><span>· ${first ? "1st" : "2nd"}</span><div>${buttons}</div></section></main>
<script>
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const c = document.getElementById("connect");
if (c) c.onclick = () => {
  ${kind === "popup" ? `window.open("/in/popup-upsell/", "_blank");` : ""}
  const d = document.createElement("div"); d.setAttribute("role", "dialog");
  d.innerHTML = "<button>Add a note</button><button>Send</button>"; document.body.appendChild(d);
  const add = [...d.querySelectorAll("button")].find((b) => b.textContent === "Add a note");
  add.onclick = () => { const t = document.createElement("textarea"); d.insertBefore(t, d.lastChild); };
  [...d.querySelectorAll("button")].filter((b) => /^Send/.test(b.textContent)).forEach((b) => b.onclick = async () => {
    const t = d.querySelector("textarea"); await post("/__invite", { to: ${J(slug)}, note: t ? t.value : "" }); d.remove(); location.reload(); });
};
const m = document.getElementById("msg");
if (m) m.onclick = () => {
  const b = document.createElement("div"); b.className = "msg-overlay-conversation-bubble";
  const h = document.createElement("header"); h.className = "msg-overlay-bubble-header"; h.textContent = ${J(title)};
  const x = document.createElement("button"); x.setAttribute("aria-label", "Close your conversation with " + ${J(title)}); x.textContent = "\\u00d7"; x.onclick = () => b.remove(); h.append(x);
  const list = document.createElement("ul"); list.innerHTML = ${J(history)};
  const box = document.createElement("div"); box.setAttribute("role", "textbox"); box.contentEditable = "true"; box.style.minHeight = "40px"; box.style.border = "1px solid #999"; box.innerText = ${J(draft)};
  const s = document.createElement("button"); s.textContent = "Send"; s.disabled = !box.innerText.trim();
  box.addEventListener("input", () => { s.disabled = !box.innerText.trim(); });
  s.onclick = async () => {
    const t = box.innerText; await post("/__message", { to: ${J(slug)}, text: t }); ${kind === "newtab" ? `window.open("/in/newtab-upsell/", "_blank");` : ""}
    const li = document.createElement("li"); li.className = "msg-s-message-list__event";
    li.innerHTML = '<div class="msg-s-event-listitem"><p class="msg-s-event-listitem__body"></p></div>'; li.querySelector("p").textContent = t; list.append(li);
    box.innerText = ""; s.disabled = true;
  };
  b.append(h, list, box, s); document.body.append(b);
};
</script>`, nm);
}

const handler = (PORT) => async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  const acct = PORTS[PORT];
  let body = ""; for await (const c of req) body += c;
  const json = () => { try { return JSON.parse(body || "{}"); } catch { return {}; } };
  if (url.pathname === "/__as") { PORTS[PORT] = url.searchParams.get("acct") || ""; return send(res, 200, "ok"); }
  if (url.pathname === "/__state") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(J(state)); }
  if (url.pathname === "/__invite") { state.invites.push({ from: acct, ...json(), at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__message") { state.messages.push({ from: acct, ...json(), at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__draft") { state.drafts[url.searchParams.get("slug")] = url.searchParams.get("text") || ""; return send(res, 200, "ok"); }
  if (!acct) return send(res, 302, "", { Location: "/login" });
  if (url.pathname === "/login") return send(res, 200, page("<main><h1>Sign in</h1></main>"));
  if (url.pathname === "/feed/") return send(res, 200, page(`<main><h1>Feed</h1></main>`));
  if (url.pathname === "/messaging/") return send(res, 200, page(`<main><h1>Messaging</h1></main>`));
  if (url.pathname === "/in/me/" || url.pathname === "/in/me") return send(res, 302, "", { Location: `/in/${acct}/` });
  const m = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (m && m[1] === acct) return send(res, 200, page(`<main><section><h1>Asha Account</h1></section></main>`));
  if (m && /upsell$/.test(m[1])) { state.tabs++; return send(res, 200, page(`<main><section><h1>Try Premium</h1></section></main>`)); }
  if (m) return send(res, 200, profile(acct, m[1]));
  send(res, 404, page("<main>Not found</main>"));
};
for (const port of Object.keys(PORTS)) http.createServer(handler(Number(port))).listen(Number(port), () => console.log(`hostile fake on ${port} as ${PORTS[port]}`));
