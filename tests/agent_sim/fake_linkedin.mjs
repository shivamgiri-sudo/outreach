// A tiny stand-in for LinkedIn so tools/linkedin_agent.mjs can be driven through every case in a real browser without
// touching linkedin.com. The logged-in account is a cookie ("acct"); a lead profile's behaviour comes from its slug prefix:
//   connect-*  Connect + "Add a note" dialog      follow-*   Follow only (no Connect)       pending-*  already Pending
//   first-*    1st-degree, Message button         open-*     2nd-degree + Message (Open Profile)
//   checkpoint-* redirects to a security check    limit-*    weekly invitation limit after Connect
//   nonote-*   Connect dialog without "Add a note"
// GET /__state shows every invite and message; POST /__accept?slug=..&acct=.. makes a pending invite accepted.
import http from "node:http";

// argv: "8797:acct-a,8796:acct-b" — each port is one browser "logged in" as that account (cookies do not survive a
// headless profile restart on localhost). POST /__as?acct=x on a port switches it (empty = logged out).
const PORTS = Object.fromEntries(String(process.argv[2] || "8797:acct-a").split(",").map((p) => p.split(":")).map(([port, a]) => [Number(port), a || ""]));
const state = { invites: [], messages: [], accepted: {} };
const names = { "acct-a": "Asha Account", "acct-b": "Bala Account", "acct-c": "Chitra Account" };
const cookie = (req) => Object.fromEntries((req.headers.cookie || "").split(/;\s*/).filter(Boolean).map((c) => c.split("=")));
const page = (body, title = "LinkedIn") => `<!doctype html><html><head><title>${title}</title></head><body>${body}</body></html>`;
const send = (res, code, html, headers = {}) => { res.writeHead(code, { "Content-Type": "text/html", ...headers }); res.end(html); };
const pretty = (slug) => slug.replace(/-\d+$/, "").split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

function profile(acct, slug) {
  const kind = slug.split("-")[0];
  const name = pretty(slug) + " " + slug.split("-").pop();
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
  return page(`<main><section><h1>${name}</h1><span>Head of Talent · ${degree}</span><div>${buttons}</div></section></main>
<script>
const post = (path, body) => fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const c = document.getElementById("connect");
if (c) c.onclick = () => {
  const d = document.createElement("div"); d.setAttribute("role", "dialog");
  ${kind === "limit" ? `d.innerHTML = "<p>You've reached the weekly invitation limit</p>"; document.body.appendChild(d); return;` : ""}
  d.innerHTML = ${kind === "nonote" ? `"<button>Send without a note</button>"` : `"<button>Add a note</button><button>Send</button>"`};
  document.body.appendChild(d);
  const add = [...d.querySelectorAll("button")].find((b) => b.textContent === "Add a note");
  if (add) add.onclick = () => { const t = document.createElement("textarea"); d.insertBefore(t, d.lastChild); };
  [...d.querySelectorAll("button")].filter((b) => /^Send/.test(b.textContent)).forEach((b) => b.onclick = async () => {
    const t = d.querySelector("textarea"); await post("/__invite", { to: ${JSON.stringify(slug)}, note: t ? t.value : "" }); d.remove(); location.reload(); });
};
const m = document.getElementById("msg");
if (m) m.onclick = () => {
  const box = document.createElement("div"); box.setAttribute("role", "textbox"); box.contentEditable = "true"; box.style.minHeight = "40px"; box.style.border = "1px solid #999";
  const s = document.createElement("button"); s.textContent = "Send";
  s.onclick = async () => { await post("/__message", { to: ${JSON.stringify(slug)}, text: box.innerText }); box.innerText = ""; };
  document.body.append(box, s);
};
</script>`, name);
}

const handler = (PORT) => async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (url.pathname === "/__as") { PORTS[PORT] = url.searchParams.get("acct") || ""; return send(res, 200, "ok"); }
  const acct = PORTS[PORT];
  let body = "";
  for await (const chunk of req) body += chunk;
  const json = () => { try { return JSON.parse(body || "{}"); } catch { return {}; } };
  if (url.pathname === "/__state") { res.writeHead(200, { "Content-Type": "application/json" }); return res.end(JSON.stringify(state)); }
  if (url.pathname === "/__reset") { Object.assign(state, { invites: [], messages: [], accepted: {} }); return send(res, 200, "ok"); }
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
  if (url.pathname === "/feed/") return send(res, 200, page(`<main><h1>Feed</h1><p>Welcome ${names[acct] || acct}</p></main>`));
  if (url.pathname === "/in/me/" || url.pathname === "/in/me") return send(res, 302, "", { Location: `/in/${acct}/` });
  const m = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (m && m[1] === acct) return send(res, 200, page(`<main><h1>${names[acct] || acct}</h1></main>`));
  if (m && m[1].startsWith("checkpoint")) return send(res, 302, "", { Location: "/checkpoint/challenge/x" });
  if (m) return send(res, 200, profile(acct, m[1]));
  send(res, 404, page("<main>Not found</main>"));
};
for (const port of Object.keys(PORTS)) http.createServer(handler(Number(port))).listen(Number(port), () => console.log(`fake LinkedIn on ${port} as ${PORTS[port]}`));
