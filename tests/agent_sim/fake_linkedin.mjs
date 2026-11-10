// A tiny stand-in for LinkedIn so tools/linkedin_agent.mjs can be driven through every case in a real browser without
// touching linkedin.com. The logged-in account is a cookie ("acct"); a lead profile's behaviour comes from its slug prefix:
//   connect-*  Connect + "Add a note" dialog      follow-*   Follow only (no Connect)       pending-*  already Pending
//   first-*    1st-degree, Message button         open-*     2nd-degree + Message (Open Profile)
//   checkpoint-* redirects to a security check    limit-*    weekly invitation limit after Connect
//   nonote-*   Connect dialog without "Add a note"
//   find-*     the people search (/search/results/people/?keywords=..) answers by the name searched: "Find One" -> 1 card
//              (at the company), "Find Two" -> 2 cards with the same name, "Find Zero" -> none. Slugs end in -N so the
//              profile name is the slug without it.
//   short-*    free account: the note box takes at most 200 characters (maxlength)
//   upsell-*   "Add a note" + "Send without a note"; Add a note opens a Premium offer with no text box (note limit)
// GET /__state shows every invite and message; POST /__accept?slug=..&acct=.. makes a pending invite accepted;
// POST /__reply?slug=..&acct=..&text=.. is the person answering (seen in the chat window and on /messaging/).
import http from "node:http";

// argv: "8797:acct-a,8796:acct-b" — each port is one browser "logged in" as that account (cookies do not survive a
// headless profile restart on localhost). POST /__as?acct=x on a port switches it (empty = logged out).
const PORTS = Object.fromEntries(String(process.argv[2] || "8797:acct-a").split(",").map((p) => p.split(":")).map(([port, a]) => [Number(port), a || ""]));
const state = { invites: [], messages: [], accepted: {}, replies: [], searches: [] };
const names = { "acct-a": "Asha Account", "acct-b": "Bala Account", "acct-c": "Chitra Account" };
const cookie = (req) => Object.fromEntries((req.headers.cookie || "").split(/;\s*/).filter(Boolean).map((c) => c.split("=")));
const page = (body, title = "LinkedIn") => `<!doctype html><html><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`;
const send = (res, code, html, headers = {}) => { res.writeHead(code, { "Content-Type": "text/html", ...headers }); res.end(html); };
const pretty = (slug) => slug.replace(/-\d+$/, "").split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
function conversation(acct, slug) {
  return [...state.messages.filter((x) => x.from === acct && x.to === slug).map((x) => ({ ours: true, text: x.text, at: x.at })),
          ...state.replies.filter((x) => x.acct === acct && x.slug === slug).map((x) => ({ ours: false, text: x.text, at: x.at }))].sort((a, b) => a.at - b.at);
}
function thread(acct, slug, name) {
  return conversation(acct, slug).map((x) => `<li class="msg-s-message-list__event"><span class="msg-s-message-group__name">${x.ours ? names[acct] || acct : name}</span>
    <div class="msg-s-event-listitem${x.ours ? "" : " msg-s-event-listitem--other"}"><p class="msg-s-event-listitem__body">${esc(x.text)}</p></div></li>`).join("");
}
function inbox(acct) {
  const slugs = [...new Set([...state.messages.filter((x) => x.from === acct).map((x) => x.to), ...state.replies.filter((x) => x.acct === acct).map((x) => x.slug)])];
  const items = slugs.map((slug) => { const c = conversation(acct, slug); const last = c[c.length - 1]; const kind = slug.split("-")[0];
    const name = kind[0].toUpperCase() + kind.slice(1) + " Lead";
    return `<li class="msg-conversation-listitem"><h3 class="msg-conversation-listitem__participant-names">${name}</h3>
      <p class="msg-conversation-card__message-snippet">${last.ours ? "You: " : name.split(" ")[0] + ": "}${esc(last.text)}</p></li>`; });
  items.push(`<li class="msg-conversation-listitem"><h3 class="msg-conversation-listitem__participant-names">Kamal Other, Ravi Group</h3>
    <p class="msg-conversation-card__message-snippet">Kamal: hello all</p></li>`);
  return page(`<main><h1>Messaging</h1><ul>${items.join("")}</ul></main>`, "Messaging");
}

function profile(acct, slug) {
  const kind = slug.split("-")[0];
  const name = kind === "find" ? pretty(slug) : kind[0].toUpperCase() + kind.slice(1) + " Lead";      // the same name the driver gives the lead
  const accepted = state.accepted[`${acct}|${slug}`];
  const pending = kind === "pending" || state.invites.some((i) => i.from === acct && i.to === slug) && !accepted;
  const degree = kind === "first" || accepted ? "1st" : "2nd";
  let buttons = "";
  if (degree === "1st" || kind === "open") buttons += `<button aria-label="Message ${name}" id="msg">Message</button>`;
  if (degree !== "1st") {
    if (pending) buttons += `<button aria-label="Pending, click to withdraw invitation">Pending</button>`;
    else if (kind === "follow") buttons += `<button aria-label="Follow ${name}">Follow</button>`;
    else buttons += `<button aria-label="Invite ${name} to connect" id="connect">Connect</button>`;
  }
  // Below the person's card: a "people you may know" block with OTHER people's buttons. Their Connect sends at once,
  // with no dialog (as on LinkedIn). The agent must never press these.
  const others = `<section><h2>People you may know</h2><button aria-label="Invite Kamal Other to connect" id="other-connect">Connect</button>
    <button aria-label="Message Kamal Other">Message</button></section>`;
  // LinkedIn repeats the buttons in a sticky header outside <main>, often under a banner that takes the click.
  const sticky = degree !== "1st" && !pending && kind !== "follow" ? `<header style="position:fixed;top:0;right:0;width:200px;height:50px">
    <button aria-label="Invite ${name} to connect" style="width:100px;height:40px">Connect</button></header>
    <div style="position:fixed;top:0;right:0;width:260px;height:60px;z-index:20;background:rgba(0,0,0,.05)"></div>` : "";
  return page(`${sticky}<main style="margin-top:80px"><section><h1>${name}</h1><span>Head of Talent · ${degree}</span><div>${buttons}</div></section>${others}</main>
<script>
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const c = document.getElementById("connect");
if (c) c.onclick = () => {
  const d = document.createElement("div"); d.setAttribute("role", "dialog");
  ${kind === "limit" ? `d.innerHTML = "<p>You've reached the weekly invitation limit</p>"; document.body.appendChild(d); return;` : ""}
  d.innerHTML = ${kind === "nonote" ? `"<button>Send without a note</button>"` : kind === "upsell" ? `"<button>Add a note</button><button>Send without a note</button>"` : `"<button>Add a note</button><button>Send</button>"`};
  document.body.appendChild(d);
  const add = [...d.querySelectorAll("button")].find((b) => b.textContent === "Add a note");
  if (add) add.onclick = ${kind === "upsell" ? `() => { const u = document.createElement("div"); u.setAttribute("role", "dialog");
    u.innerHTML = "<h2>Personalize your invitation with Premium</h2><button>Try Premium for free</button>"; document.body.appendChild(u);
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll("[role=dialog]").forEach((x) => x.remove()); }); }`
    : `() => { const t = document.createElement("textarea"); ${kind === "short" ? `t.maxLength = 200;` : ""} d.insertBefore(t, d.lastChild); }`};
  [...d.querySelectorAll("button")].filter((b) => /^Send/.test(b.textContent)).forEach((b) => b.onclick = async () => {
    const t = d.querySelector("textarea"); await post("/__invite", { to: ${JSON.stringify(slug)}, note: t ? t.value : "" }); d.remove(); location.reload(); });
};
const oc = document.getElementById("other-connect");
if (oc) oc.onclick = async () => { await post("/__invite", { to: "kamal-other", note: "" }); oc.textContent = "Pending"; };
const m = document.getElementById("msg");
if (m) m.onclick = () => {
  const list = document.createElement("ul"); list.className = "msg-s-message-list-content";
  list.innerHTML = ${JSON.stringify(thread(acct, slug, name))};
  document.body.append(list);
  const box = document.createElement("div"); box.setAttribute("role", "textbox"); box.contentEditable = "true"; box.style.minHeight = "40px"; box.style.border = "1px solid #999";
  const s = document.createElement("button"); s.textContent = "Send";
  s.onclick = async () => { await post("/__message", { to: ${JSON.stringify(slug)}, text: box.innerText }); box.innerText = ""; };
  document.body.append(box, s);
};
</script>`, name);
}

function search(url) {
  const q = (url.searchParams.get("keywords") || "").toLowerCase();
  const card = (slug, name, headline) => `<li><a href="/in/${slug}?miniProfileUrn=x"><span aria-hidden="true">${name}</span><span class="visually-hidden">${name}</span></a>
    <div>${name}</div><div>• 2nd</div><div>${headline}</div><div>Pune, India</div><button>Connect</button></li>`;
  const co = q.split(" ").slice(2).join(" ");
  const rows = q.startsWith("find one") ? [card("find-one-1", "Find One", `Head of Talent at ${co}`)]
    : q.startsWith("find two") ? [card("find-two-1", "Find Two", `Head of Talent at ${co}`), card("find-two-2", "Find Two", "Analyst at Somewhere Else")] : [];
  return page(`<main><h1>Search</h1><ul>${rows.join("")}</ul><aside><ul><li><a href="/in/kamal-other">Kamal Other</a></li></ul></aside></main>`, "Search");
}

const handler = (PORT) => async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (url.pathname === "/__as") { PORTS[PORT] = url.searchParams.get("acct") || ""; return send(res, 200, "ok"); }
  const acct = PORTS[PORT];
  let body = "";
  for await (const chunk of req) body += chunk;
  const json = () => { try { return JSON.parse(body || "{}"); } catch { return {}; } };
  if (url.pathname === "/__state") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(JSON.stringify(state)); }
  if (url.pathname === "/__reset") { Object.assign(state, { invites: [], messages: [], accepted: {}, replies: [], searches: [] }); return send(res, 200, "ok"); }
  if (url.pathname === "/__reply") { state.replies.push({ acct: url.searchParams.get("acct"), slug: url.searchParams.get("slug"), text: url.searchParams.get("text") || "", at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__accept") { state.accepted[`${url.searchParams.get("acct")}|${url.searchParams.get("slug")}`] = true; return send(res, 200, "ok"); }
  if (url.pathname === "/__invite") { state.invites.push({ from: acct, ...json(), at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__message") { state.messages.push({ from: acct, ...json(), at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/login" && url.searchParams.get("as")) {
    return send(res, 302, "", { Location: "/feed/", "Set-Cookie": `acct=${url.searchParams.get("as")}; Path=/; Max-Age=86400` });
  }
  if (url.pathname === "/logout") return send(res, 302, "", { Location: "/login", "Set-Cookie": "acct=; Path=/; Max-Age=0" });
  if (url.pathname === "/login") return send(res, 200, page("<main><h1>Sign in</h1></main>"));
  if (!acct) return send(res, 302, "", { Location: "/login" });
  if (url.pathname.startsWith("/checkpoint")) return send(res, 200, page("<main><h1>Let's do a quick security check</h1></main>"));
  if (url.pathname.startsWith("/search/results/people")) { state.searches.push(url.searchParams.get("keywords")); return send(res, 200, search(url)); }
  if (url.pathname === "/messaging/" || url.pathname === "/messaging") return send(res, 200, inbox(acct));
  if (url.pathname === "/feed/") return send(res, 200, page(`<main><h1>Feed</h1><p>Welcome ${names[acct] || acct}</p></main>`));
  if (url.pathname === "/in/me/" || url.pathname === "/in/me") return send(res, 302, "", { Location: `/in/${acct}/` });
  const m = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (m && m[1] === acct) return send(res, 200, page(`<main><h1>${names[acct] || acct}</h1></main>`));
  if (m && m[1].startsWith("checkpoint")) return send(res, 302, "", { Location: "/checkpoint/challenge/x" });
  if (m) return send(res, 200, profile(acct, m[1]));
  send(res, 404, page("<main>Not found</main>"));
};
for (const port of Object.keys(PORTS)) http.createServer(handler(Number(port))).listen(Number(port), () => console.log(`fake LinkedIn on ${port} as ${PORTS[port]}`));
