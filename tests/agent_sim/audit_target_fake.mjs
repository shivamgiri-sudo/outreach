// HOSTILE stand-in for LinkedIn, used by tests/agent_sim/audit_target.py (audit of the agent's send paths).
// A lead's behaviour comes from the first segment of its slug. Names default to "<Kind> Lead".
//   redirect-*     302 to /in/stranger-9 (another person, 2nd degree, plain unlabelled Connect button in the top card)
//   redirectacc-*  302 to /in/stranger-1st (another person who IS a 1st-degree connection)
//   redirectpend-* 302 to /in/stranger-pend (another person with a Pending button)
//   gone-*         "This page doesn't exist" with a suggestions block (plain Connect buttons for Kamal Other) as first section
//   late-*         for 1.5 s a skeleton whose first section holds a stranger's plain Connect; then the real profile renders
//   namesake-*     Follow-only profile; a "More profiles for you" block has "Invite Namesake Person to connect" for ANOTHER person
//   initials-*     lead "A K Sharma" (Follow only); "Invite Rahul Sharma to connect" sits in a sidebar
//   fillfail-*     Connect + Add a note, but the note box is read-only (fill fails)
//   draft-*        1st degree; the chat box opens with an old draft already in it
//   lostsend-*     1st degree; Send empties the box but nothing is delivered
//   slowsend-*     1st degree; Send delivers, the box empties only 4 s later
//   linkedlink-*   1st degree; Message opens somebody else's chat (Bea Bee) whose history links to this lead's profile
//   first-*/sentfail-*/racemsg-*  ordinary 1st degree with Message
//   degfp-*        2nd degree, but the degree badge is not in the top card and the headline says "Ex 1st Lieutenant"
//   inmail-*       2nd degree with a Message button (InMail style) that opens a working chat
import http from "node:http";

const PORTS = Object.fromEntries(String(process.argv[2] || "8902:acct-a").split(",").map((p) => p.split(":")).map(([port, a]) => [Number(port), a || ""]));
const state = { invites: [], messages: [], hits: [] };
const NAMES = { namesake: "Namesake Person", initials: "A K Sharma", stranger: "Stranger Nine", kamal: "Kamal Other" };
const cap = (w) => w[0].toUpperCase() + w.slice(1);
const nameOf = (slug) => slug === "stranger-1st" ? "Stranger First" : slug === "stranger-pend" ? "Stranger Pending" : NAMES[slug.split("-")[0]] || cap(slug.split("-")[0]) + " Lead";
const page = (body, title = "LinkedIn") => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`;
const send = (res, code, html, headers = {}) => { res.writeHead(code, { "Content-Type": "text/html", ...headers }); res.end(html); };
const J = JSON.stringify;

const SCRIPT = (slug, kind) => `
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
window.openInvite = (to) => {
  const d = document.createElement("div"); d.setAttribute("role", "dialog");
  d.innerHTML = "<button>Add a note</button><button>Send</button>"; document.body.appendChild(d);
  const add = [...d.querySelectorAll("button")].find((b) => b.textContent === "Add a note");
  add.onclick = () => { const t = document.createElement("textarea"); ${kind === "fillfail" ? "t.readOnly = true;" : ""} d.insertBefore(t, d.lastChild); };
  [...d.querySelectorAll("button")].filter((b) => /^Send/.test(b.textContent)).forEach((b) => b.onclick = async () => {
    const t = d.querySelector("textarea"); await post("/__invite", { to, note: t ? t.value : "" }); d.remove(); location.reload(); });
};
function wire(root) { root.querySelectorAll("[data-connect]").forEach((b) => b.onclick = () => openInvite(b.dataset.connect)); }
wire(document);
const bubble = (title, to, listHtml, prefill, mode) => {
  const b = document.createElement("div"); b.className = "msg-overlay-conversation-bubble";
  const h = document.createElement("header"); h.className = "msg-overlay-bubble-header"; h.textContent = title;
  const x = document.createElement("button"); x.setAttribute("aria-label", "Close your conversation with " + title); x.textContent = "\u00d7"; x.onclick = () => b.remove(); h.append(x);
  const list = document.createElement("ul"); list.innerHTML = listHtml;
  const box = document.createElement("div"); box.setAttribute("role", "textbox"); box.contentEditable = "true"; box.style.minHeight = "40px"; box.style.border = "1px solid #999"; box.innerText = prefill || "";
  const s = document.createElement("button"); s.textContent = "Send"; s.disabled = !(prefill || "").trim();
  box.addEventListener("input", () => { s.disabled = !box.innerText.trim(); });
  s.onclick = async () => {
    const t = box.innerText;
    if (mode === "lost") { box.innerText = ""; s.disabled = true; return; }
    await post("/__message", { to, text: t });
    const li = document.createElement("li"); li.className = "msg-s-message-list__event";
    li.innerHTML = '<div class="msg-s-event-listitem"><p class="msg-s-event-listitem__body"></p></div>'; li.querySelector("p").textContent = t; list.append(li);
    if (mode === "slow") setTimeout(() => { box.innerText = ""; s.disabled = true; }, 4000); else { box.innerText = ""; s.disabled = true; }
  };
  b.append(h, list, box, s); document.body.append(b); return b;
};
const m = document.getElementById("msg");
if (m) m.onclick = () => {
  ${kind === "linkedlink" ? `bubble("Bea Bee", "bea-bee", '<li class="msg-s-message-list__event"><a href="/in/' + ${J(slug)} + '">look at this profile</a></li>', "", "normal");`
    : kind === "draft" ? `bubble(${J(nameOf(slug))}, ${J(slug)}, "", "Hi, quick q (my own half-written draft)", "normal");`
    : `bubble(${J(nameOf(slug))}, ${J(slug)}, "", "", ${J(kind === "lostsend" ? "lost" : kind === "slowsend" ? "slow" : "normal")});`}
};`;

function profile(acct, slug) {
  const kind = slug.split("-")[0], nm = nameOf(slug);
  const invited = state.invites.some((i) => i.from === acct && i.to === slug);
  const lead1st = ["first", "sentfail", "racemsg", "draft", "lostsend", "slowsend", "linkedlink"].includes(kind) || slug === "stranger-1st";
  const msgBtn = `<button aria-label="Message ${nm}" id="msg">Message</button>`;
  const connLabelled = `<button aria-label="Invite ${nm} to connect" data-connect="${slug}">Connect</button>`;
  const plainConnect = (to) => `<button data-connect="${to}">Connect</button>`;
  let body;
  if (slug === "stranger-9") body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div>${plainConnect("stranger-9")}</div></section></main>`;
  else if (slug === "stranger-pend") body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div><button>Pending</button></div></section></main>`;
  else if (kind === "gone") body = `<main><section><h1>This page doesn't exist</h1><p>Suggested for you</p><div>Kamal Other ${plainConnect("kamal-other")}</div></section></main>`;
  else if (kind === "late") body = `<main id="m"><section><h2>Loading</h2><div>Kamal Other ${plainConnect("kamal-other")}</div></section></main>
    <script>setTimeout(() => { document.getElementById("m").innerHTML = '<section><h1>${nm}</h1><span>· 2nd</span><div>${connLabelled.replace(/'/g, "\\'")}</div></section>'; wire(document); }, 1500);</script>`;
  else if (kind === "namesake") body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div><button aria-label="Follow ${nm}">Follow</button></div></section>
    <section><h2>More profiles for you</h2><button aria-label="Invite ${nm} to connect" data-connect="namesake-other">Connect</button></section></main>`;
  else if (kind === "initials") body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div><button aria-label="Follow ${nm}">Follow</button></div></section>
    <section><h2>People also viewed</h2><button aria-label="Invite Rahul Sharma to connect" data-connect="rahul-sharma">Connect</button></section></main>`;
  else if (kind === "degfp") body = `<main><section><h1>${nm}</h1><p>Ex 1st Lieutenant, Director</p><div><button aria-label="Follow ${nm}">Follow</button></div></section></main>`;
  else if (kind === "inmail") body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div>${msgBtn}</div></section></main>`;
  else if (lead1st) body = `<main><section><h1>${nm}</h1><span>· 1st</span><div>${msgBtn}</div></section></main>`;
  else if (invited) body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div><button aria-label="Pending, click to withdraw invitation sent to ${nm}">Pending</button></div></section></main>`;
  else body = `<main><section><h1>${nm}</h1><span>· 2nd</span><div>${connLabelled}</div></section></main>`;
  return page(`${body}<script>${SCRIPT(slug, kind)}</script>`, nm);
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
  if (!acct) return send(res, 302, "", { Location: "/login" });
  if (url.pathname === "/login") return send(res, 200, page("<main><h1>Sign in</h1></main>"));
  if (url.pathname === "/feed/") return send(res, 200, page(`<main><h1>Feed</h1></main>`));
  if (url.pathname === "/messaging/") return send(res, 200, page(`<main><h1>Messaging</h1></main>`));
  if (url.pathname === "/in/me/" || url.pathname === "/in/me") return send(res, 302, "", { Location: `/in/${acct}/` });
  const m = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (m && m[1] === acct) return send(res, 200, page(`<main><section><h1>Asha Account</h1></section></main>`));
  if (m) {
    state.hits.push(m[1]);
    const kind = m[1].split("-")[0];
    if (kind === "redirect") return send(res, 302, "", { Location: "/in/stranger-9" });
    if (kind === "redirectacc") return send(res, 302, "", { Location: "/in/stranger-1st" });
    if (kind === "redirectpend") return send(res, 302, "", { Location: "/in/stranger-pend" });
    return send(res, 200, profile(acct, m[1]));
  }
  send(res, 404, page("<main>Not found</main>"));
};
for (const port of Object.keys(PORTS)) http.createServer(handler(Number(port))).listen(Number(port), () => console.log(`audit fake on ${port} as ${PORTS[port]}`));
