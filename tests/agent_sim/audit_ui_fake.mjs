// HOSTILE stand-in for LinkedIn (audit of tools/linkedin_agent.mjs). Same idea as fake_linkedin.mjs but every profile is built
// in the browser from a config, and the config is picked by the slug prefix (kind-N). Nothing here touches linkedin.com.
// argv: "8922:acct-a,8923:acct-hi"  (acct-hi = the account whose LinkedIn is set to Hindi: html lang=hi, Hindi labels, "आप:" in the inbox)
//
// Kinds (slug prefix): see KINDS below. Per-slug override at run time: POST /__cfg?slug=x  body {json}  (merged over the kind).
// State: GET /__state ; POST /__reset ; /__accept?slug&acct ; /__reply?slug&acct&text ; /__as?acct (switch/clear login) ;
// /__expire (the next request is logged out).
import http from "node:http";

const PORTS = Object.fromEntries(String(process.argv[2] || "8922:acct-a").split(",").map((p) => p.split(":")).map(([port, a]) => [Number(port), a || ""]));
const state = { invites: [], messages: [], accepted: {}, replies: [], searches: [], drafts: {}, views: {}, opens: {}, over: {}, clicks: [] };
const NAMES = { "acct-a": "Asha Account", "acct-hi": "Hari Account" };
const page = (body, title = "LinkedIn", lang = "en") => `<!doctype html><html lang="${lang}"><head><meta charset="utf-8"><title>${title}</title></head><body>${body}</body></html>`;
const send = (res, code, html, headers = {}) => { res.writeHead(code, { "Content-Type": "text/html; charset=utf-8", ...headers }); res.end(html); };
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const kindOf = (slug) => slug.split("-")[0];
const pretty = (slug) => slug.replace(/-\d+$/, "").split("-").map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");

// ---- one profile config per kind. Defaults first, a kind only lists what is different.
const DEFAULT = { name: "", h1: "", lang: "en", degree: "2nd", badge: true, headline: "Head of Talent", company: "", primary: "connect", unlabelled: false,
  firstOnly: false, pending: false, msgButton: "auto", hydr: 0, skeleton: false, promo: false, pymk: "Kamal Other", pymkSlug: "kamal-other", pymkInstant: true,
  dialog: "normal", msg: "overlay", send: "button", deliver: "ok", threadDelay: 0, banner: "", softwall: false, covered: false, expireOnConnect: false,
  closable: true, staleChat: false, sendDisabledFirst: false };
const KINDS = {
  connect: {}, first: { degree: "1st" }, pending: { pending: true }, follow: { primary: "follow" },
  // ---- timing
  slowhydr: { hydr: 4000 },                                   // top card, badge and buttons appear 4 s after domcontentloaded
  slowfirst: { degree: "1st", hydr: 4000 },                   // a 1st-degree person, same delay (acceptance check)
  skel: { skeleton: true },                                   // a skeleton/promo section is the first <section> of <main>
  promo: { promo: true },
  // ---- names and labels
  credname: { h1: "Rahul Sharma, MBA (He/Him) \u{1F680}", name: "Rahul Sharma" },
  honorific: { name: "Rahul Sharma" },                       // the lead is "Dr Rahul Sharma" in the portal
  nonlatin: { name: "राहुल शर्मा" },
  single: { name: "Priya", pymk: "Priya Nair", pymkSlug: "priya-nair-9", pymkInstant: false },
  namesake: { name: "Amit Sharma", primary: "follow-more", pymk: "Amit Sharma", pymkSlug: "amit-sharma-77", pymkInstant: false },
  firstonly: { name: "Rahul Sharma", firstOnly: true },      // aria-labels carry the first name only ("Invite Rahul to connect")
  hindi: { name: "Rahul Sharma", lang: "hi" },
  member: { name: "LinkedIn Member", h1: "LinkedIn Member" },
  wrongperson: { name: "Sunita Rao", unlabelled: true },     // the link in the portal points to someone else; buttons have no name in the label
  redirect: {},                                              // 302 to /in/wrongperson-9 (handled below)
  nobadge: { badge: false, company: "1st Choice Staffing" }, // out-of-network look: no degree badge, a company chip that starts with "1st"
  // ---- the invitation box
  instant: { dialog: "instant" }, emailreq: { dialog: "emailreq" }, fillro: { dialog: "fillro" }, drop: { dialog: "drop" },
  modal: { covered: true }, softwall: { softwall: true }, wallmid: { expireOnConnect: true, dialog: "wallmid" },
  restricted: { banner: "Your account has been temporarily restricted" },
  falsepos: { headline: "Head of Talent | background and security verification for staffing" },
  // ---- the chat
  fullmsg: { degree: "1st", msg: "full" }, inmail: { msg: "upsell", degree: "2nd" }, enterkey: { degree: "1st", send: "enter" },
  keyonly: { degree: "1st", send: "keyonly" }, draftdup: { degree: "1st", sendDisabledFirst: true },
  nodeliver: { degree: "1st", deliver: "fail" }, slowsend: { degree: "1st", deliver: "slow" }, slowthread: { degree: "1st", threadDelay: 3500 },
  samefirst: { degree: "1st", name: "Asha Mehta" }, stale: { degree: "1st", staleChat: true },
  http999: {}, hang: {},
};
const cfgFor = (slug, acct) => {
  const kind = kindOf(slug);
  const accepted = state.accepted[`${acct}|${slug}`];
  const c = { ...DEFAULT, ...(KINDS[kind] || {}), ...(state.over[slug] || {}) };
  c.slug = slug; c.kind = kind;
  if (!c.name) c.name = kind === "find" ? pretty(slug) : kind[0].toUpperCase() + kind.slice(1) + " Lead";
  if (!c.h1) c.h1 = c.name;
  if (acct === "acct-hi") c.lang = "hi";
  if (accepted) c.degree = "1st";
  c.invited = state.invites.some((i) => i.from === acct && i.to === slug) && !accepted;
  c.draft = state.drafts[`${acct}|${slug}`] || "";
  c.acct = acct; c.acctName = NAMES[acct] || acct;
  c.opens = state.opens[`${acct}|${slug}`] || 0;
  return c;
};

function conversation(acct, slug) {
  return [...state.messages.filter((x) => x.from === acct && x.to === slug).map((x) => ({ ours: true, text: x.text, at: x.at })),
    ...state.replies.filter((x) => x.acct === acct && x.slug === slug).map((x) => ({ ours: false, text: x.text, at: x.at }))].sort((a, b) => a.at - b.at);
}
const threadHtml = (c) => conversation(c.acct, c.slug).map((x) => `<li class="msg-s-message-list__event"><span class="msg-s-message-group__name">${esc(x.ours ? c.acctName : c.name)}</span>
  <div class="msg-s-event-listitem${x.ours ? "" : " msg-s-event-listitem--other"}"><p class="msg-s-event-listitem__body">${esc(x.text)}</p></div></li>`).join("");

function inbox(acct, hindi) {
  const slugs = [...new Set([...state.messages.filter((x) => x.from === acct).map((x) => x.to), ...state.replies.filter((x) => x.acct === acct).map((x) => x.slug)])];
  const items = slugs.map((slug) => { const c = cfgFor(slug, acct); const k = conversation(acct, slug); const last = k[k.length - 1];
    return `<li class="msg-conversation-listitem"><h3 class="msg-conversation-listitem__participant-names">${esc(c.name)}</h3>
      <p class="msg-conversation-card__message-snippet">${last.ours ? (hindi ? "आप: " : "You: ") : c.name.split(" ")[0] + ": "}${esc(last.text)}</p></li>`; });
  for (const [n, t] of state.over.__inbox || []) items.push(`<li class="msg-conversation-listitem"><h3 class="msg-conversation-listitem__participant-names">${esc(n)}</h3><p class="msg-conversation-card__message-snippet">${esc(t)}</p></li>`);
  return page(`<main><h1>Messaging</h1><ul>${items.join("")}</ul></main>`, "Messaging", hindi ? "hi" : "en");
}

// The client: builds the page from CFG after cfg.hydr ms. LinkedIn-like on purpose: buttons with aria-labels, the person's own card first,
// "people you may know" below, a sticky copy of the buttons, dialogs with role=dialog, chat bubbles with msg-overlay classes.
const CLIENT = String.raw`
const C = CFG;
const T = { en: { c: (n) => "Invite " + n + " to connect", ct: "Connect", m: (n) => "Message " + n, mt: "Message", pend: "Pending, click to withdraw invitation", pt: "Pending", add: "Add a note", bare: "Send without a note", send: "Send", more: "More", fol: (n) => "Follow " + n, ft: "Follow", d1: "1st", d2: "2nd" },
  hi: { c: (n) => n + " को कनेक्ट करने के लिए आमंत्रित करें", ct: "कनेक्ट करें", m: (n) => n + " को संदेश भेजें", mt: "संदेश", pend: "लंबित, आमंत्रण वापस लेने के लिए क्लिक करें", pt: "लंबित", add: "नोट जोड़ें", bare: "बिना नोट के भेजें", send: "भेजें", more: "अधिक", fol: (n) => n + " को फ़ॉलो करें", ft: "फ़ॉलो करें", d1: "पहला", d2: "दूसरा" } }[C.lang];
const post = (p, b) => fetch(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) });
const el = (tag, props = {}, ...kids) => { const e = document.createElement(tag); for (const [k, v] of Object.entries(props)) { if (k === "on") for (const [ev, f] of Object.entries(v)) e.addEventListener(ev, f); else if (k === "text") e.textContent = v; else e.setAttribute(k, v); } e.append(...kids); return e; };
const labelName = C.firstOnly ? C.name.split(" ")[0] : C.name;
const btn = (aria, text, onclick, id) => { const b = el("button", { text, ...(C.unlabelled || !aria ? {} : { "aria-label": aria }), ...(id ? { id } : {}) }); b.onclick = onclick; return b; };
const dlg = (...kids) => { const d = el("div", { role: "dialog" }, ...kids); document.body.appendChild(d); return d; };
const clk = (what) => post("/__click", { what, slug: C.slug });
const invite = async (to, note) => { await post("/__invite", { to, note }); };

function openInvite(toSlug, toName) {
  clk("connect:" + toSlug);
  if (C.expireOnConnect && toSlug === C.slug) { post("/__expire", {}); dlg(el("p", { text: "Sign in to continue" }), el("button", { text: "Sign in" })); return; }
  if (C.dialog === "instant" && toSlug === C.slug) { invite(toSlug, "").then(() => location.reload()); return; }
  if (C.dialog === "weekly") { dlg(el("p", { text: "You've reached the weekly invitation limit" })); return; }
  if (C.dialog === "emailreq" && toSlug === C.slug) {
    const s = el("button", { text: "Send invitation", disabled: "" });
    dlg(el("h2", { text: "Add their email to connect" }), el("input", { type: "email", placeholder: "Email" }), s, el("button", { text: "Cancel" })); return; }
  const d = dlg(el("h2", { text: "Add a note to your invitation?" }));
  const sendNow = async (note) => { if (C.dialog === "drop" && toSlug === C.slug) { d.remove(); document.body.append(el("div", { class: "toast", text: "Something went wrong. Try again later." })); return; }
    await invite(toSlug, note); d.remove(); location.reload(); };
  const addB = el("button", { text: T.add }), bareB = el("button", { text: T.bare });
  bareB.onclick = () => sendNow("");
  addB.onclick = () => { addB.remove(); bareB.remove(); const t = el("textarea", { placeholder: "Ex: We know each other from..." });
    if (C.dialog === "fillro" && toSlug === C.slug) { t.disabled = true; setTimeout(() => { t.disabled = false; }, 6500); }
    const s = el("button", { text: T.send }); s.onclick = () => sendNow(t.value); d.append(t, s); };
  d.append(addB, bareB);
}

const bubbles = () => document.querySelectorAll(".msg-overlay-conversation-bubble").length;
function openChat() {
  clk("message:" + C.slug);
  post("/__open", { slug: C.slug });
  if (C.msg === "full") { location.href = "/messaging/thread/" + C.slug + "/"; return; }
  if (C.msg === "upsell") { dlg(el("h2", { text: "Message anyone with Premium" }), el("button", { text: "Try Premium for free" })); return; }
  makeBubble(C.name, C.slug, true);
}
function makeBubble(who, slug, mine) {
  const b = el("div", { class: "msg-overlay-conversation-bubble" });
  const h = el("header", { class: "msg-overlay-bubble-header" }); h.append(el("a", { href: "/in/" + slug + "/", text: who }));
  const x = el("button", { "aria-label": "Close your conversation with " + who, text: "\u00d7" }); x.onclick = () => b.remove(); if (C.closable || mine) h.append(x);
  const list = el("ul", { class: "msg-s-message-list-content" });
  if (mine) { list.setAttribute("aria-busy", "true"); setTimeout(() => { list.innerHTML = C.threadHtml; list.removeAttribute("aria-busy"); }, C.threadDelay); }
  const box = el("div", { role: "textbox", contenteditable: "true", "aria-label": "Write a message…", style: "min-height:40px;border:1px solid #999" });
  if (mine && C.draft) box.innerText = C.draft;
  let keyed = false;
  box.addEventListener("keydown", () => { keyed = true; if (sb && C.send === "keyonly") sb.disabled = false; });
  box.addEventListener("input", () => { if (mine) post("/__draft", { slug: C.slug, text: box.innerText }); });
  const doSend = async () => {
    const text = box.innerText.trim(); if (!text) return;
    if (C.deliver === "fail") { box.innerText = ""; post("/__draft", { slug: C.slug, text: "" }); list.append(el("li", { class: "msg-s-event-listitem msg-s-event-listitem--failed", text: "Not delivered. Retry" })); return; }
    await post("/__message" + (C.deliver === "slow" ? "?slow=1" : ""), { to: slug, text }); box.innerText = "";
    const li = document.createElement("li"); li.className = "msg-s-message-list__event"; li.innerHTML = '<div class="msg-s-event-listitem"><p class="msg-s-event-listitem__body"></p></div>'; li.querySelector("p").textContent = text; list.append(li); post("/__draft", { slug: C.slug, text: "" });
  };
  box.addEventListener("keydown", (e) => { if (C.send === "enter" && e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); } });
  b.append(h, list, box);
  let sb = null;
  if (C.send !== "enter") {
    sb = el("button", { text: T.send }); sb.onclick = doSend; b.append(sb);
    if (C.send === "keyonly") sb.disabled = true;
    if (C.sendDisabledFirst && C.opens <= 0) sb.disabled = true;          // the first time the box opens, Send stays disabled (still loading)
  } else b.append(el("label", {}, el("input", { type: "checkbox", checked: "" }), " Press Enter to send"));
  document.body.append(b);
}

function build() {
  const main = document.querySelector("main"); main.innerHTML = "";
  if (C.softwall) { const w = dlg(el("h2", { text: "Sign in to see who you already know" }), el("a", { href: "/login", text: "Sign in" }), el("button", { text: "Join now" })); w.setAttribute("style", "position:fixed;inset:0;z-index:60;background:#fff"); }
  if (C.covered) document.body.append(el("div", { id: "cover", style: "position:fixed;inset:0;z-index:50;background:rgba(0,0,0,.4)" }, el("div", { role: "dialog", style: "background:#fff;margin:80px auto;width:400px;padding:20px" }, el("h2", { text: "Try Premium for free" }), el("p", { text: "Get InMail credits" }))));
  if (C.skeleton) main.append(el("section", { "aria-hidden": "true" }, el("div", { style: "height:60px;background:#eee" })));
  if (C.promo) main.append(el("section", {}, el("h2", { text: "Try Premium for ₹0" }), el("button", { text: "Try now" })));
  if (C.banner) main.append(el("div", { class: "banner", text: C.banner }));
  const card = el("section", {});
  card.append(el("h1", { text: C.h1 }));
  if (C.badge) card.append(el("span", { text: "· " + (C.degree === "1st" ? T.d1 : T.d2) }));
  card.append(el("p", { text: C.headline }));
  if (C.company) card.append(el("button", { text: C.company }));
  const row = el("div", {});
  const first = C.degree === "1st";
  if (C.msgButton === "auto" ? (first || C.msg === "upsell" || C.kind === "open") : C.msgButton === "yes") {
    const mb = btn(T.m(labelName), T.mt, openChat, "msg"); if (C.msg === "upsell") mb.insertAdjacentHTML("beforeend", '<svg data-test-icon="lock-small" width="8" height="8"></svg>'); row.append(mb); }
  if (!first) {
    if (C.pending || C.invited) row.append(btn(T.pend, T.pt, () => {}));
    else if (C.primary === "connect") row.append(btn(T.c(labelName), T.ct, () => openInvite(C.slug, C.name), "connect"));
    else if (C.primary === "follow") row.append(btn(T.fol(labelName), T.ft, () => {}));
    else if (C.primary === "follow-more") {
      row.append(btn(T.fol(labelName), T.ft, () => {}));
      const more = btn("", T.more, () => { if (document.getElementById("menu")) return; const m = el("div", { role: "menu", id: "menu" });
        const it = el("div", { role: "menuitem", "aria-label": T.c(labelName), text: T.ct }); it.onclick = () => { m.remove(); openInvite(C.slug, C.name); }; m.append(it); card.append(m); });
      row.append(more);
    }
  }
  card.append(row); main.append(card);
  const others = el("section", {}, el("h2", { text: "People you may know" }));
  others.append(btn(T.c(C.pymk), T.ct, () => { if (C.pymkInstant) { invite(C.pymkSlug, ""); } else openInvite(C.pymkSlug, C.pymk); }, "pymk"));
  others.append(btn(T.m(C.pymk), T.mt, () => makeBubble(C.pymk, C.pymkSlug, false)));
  main.append(others);
  if (C.staleChat) makeBubble("Kamal Other", "kamal-other", false);
}
document.body.insertAdjacentHTML("afterbegin", "<main></main>");
if (C.hydr > 0) setTimeout(build, C.hydr); else build();
`;

function profile(c) {
  const withThread = { ...c, threadHtml: threadHtml(c) };
  return page(`<script>const CFG = ${JSON.stringify(withThread).replace(/</g, "\\u003c")};</script><script>${CLIENT}</script>`, c.h1, c.lang);
}

function search(url, acct) {
  const q = (url.searchParams.get("keywords") || "").toLowerCase();
  const card = (slug, name, headline) => `<li><a href="/in/${slug}?miniProfileUrn=x"><span aria-hidden="true">${name}</span><span class="visually-hidden">${name}</span></a>
    <div>${name}</div><div>• 2nd</div><div>${headline}</div><div>Pune, India</div><button>Connect</button></li>`;
  const co = q.split(" ").slice(2).join(" ");
  if (q.startsWith("find limit")) return page(`<main><h1>You've reached the commercial use limit</h1><p>Upgrade to Premium to keep searching for people.</p></main>`, "Search");
  if (q.startsWith("find slow")) return page(`<main><h1>Search</h1><ul id="r"></ul></main><script>setTimeout(()=>{document.getElementById("r").innerHTML=${JSON.stringify(card("find-slow-1", "Find Slow", `Head of Talent at ${co}`))}},4000)</script>`, "Search");
  if (q.startsWith("find fp")) return page(`<main><h1>Search</h1><ul>${card("find-fp-1", "Find Fp", "Fraud analyst: unusual activity detection at " + co)}</ul></main>`, "Search");
  const rows = q.startsWith("find one") ? [card("find-one-1", "Find One", `Head of Talent at ${co}`)] : [];
  return page(`<main><h1>Search</h1><ul>${rows.join("")}</ul>${rows.length ? "" : "<p>No results found</p>"}</main>`, "Search");
}

const handler = (PORT) => async (req, res) => {
  const url = new URL(req.url, `http://localhost:${PORT}`);
  if (url.pathname === "/__as") { PORTS[PORT] = url.searchParams.get("acct") || ""; return send(res, 200, "ok"); }
  const acct = PORTS[PORT];
  let body = ""; for await (const chunk of req) body += chunk;
  const json = () => { try { return JSON.parse(body || "{}"); } catch { return {}; } };
  const J = (o) => { res.writeHead(200, { "Content-Type": "application/json" }); res.end(JSON.stringify(o)); };
  if (url.pathname === "/__state") return J(state);
  if (url.pathname === "/__reset") { Object.assign(state, { invites: [], messages: [], accepted: {}, replies: [], searches: [], drafts: {}, views: {}, opens: {}, over: {}, clicks: [] }); return send(res, 200, "ok"); }
  if (url.pathname === "/__cfg") { state.over[url.searchParams.get("slug")] = { ...(state.over[url.searchParams.get("slug")] || {}), ...json() }; return send(res, 200, "ok"); }
  if (url.pathname === "/__inbox") { state.over.__inbox = json().items || []; return send(res, 200, "ok"); }
  if (url.pathname === "/__reply") { state.replies.push({ acct: url.searchParams.get("acct"), slug: url.searchParams.get("slug"), text: url.searchParams.get("text") || "", at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__accept") { state.accepted[`${url.searchParams.get("acct")}|${url.searchParams.get("slug")}`] = true; return send(res, 200, "ok"); }
  if (url.pathname === "/__expire") { PORTS[PORT] = ""; return send(res, 200, "ok"); }
  if (url.pathname === "/__invite") { state.invites.push({ from: acct, ...json(), at: Date.now() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__message") { state.messages.push({ from: acct, ...json(), at: Date.now() }); if (url.searchParams.get("slow")) await new Promise((r) => setTimeout(r, 2500)); return send(res, 200, "ok"); }
  if (url.pathname === "/__click") { state.clicks.push({ acct, ...json() }); return send(res, 200, "ok"); }
  if (url.pathname === "/__open") { const k = `${acct}|${json().slug}`; state.opens[k] = (state.opens[k] || 0) + 1; return send(res, 200, "ok"); }
  if (url.pathname === "/__draft") { const b = json(); state.drafts[`${acct}|${b.slug}`] = b.text; return send(res, 200, "ok"); }
  if (url.pathname === "/login" && url.searchParams.get("as")) return send(res, 302, "", { Location: "/feed/" });
  if (url.pathname === "/login") return send(res, 200, page("<main><h1>Sign in</h1></main>"));
  if (!acct) return send(res, 302, "", { Location: "/login" });
  const hindi = acct === "acct-hi";
  if (url.pathname.startsWith("/checkpoint")) return send(res, 200, page("<main><h1>Let's do a quick security check</h1></main>"));
  if (url.pathname.startsWith("/search/results/people")) { state.searches.push(url.searchParams.get("keywords")); return send(res, 200, search(url, acct)); }
  if (url.pathname === "/messaging/" || url.pathname === "/messaging") return send(res, 200, inbox(acct, hindi));
  if (url.pathname.startsWith("/messaging/thread/")) return send(res, 200, page(`<main><h1>Messaging</h1><div role="textbox" contenteditable="true"></div><button>Send</button></main>`));
  if (url.pathname === "/feed/") return send(res, 200, page(`<main><h1>Feed</h1><p>Welcome ${NAMES[acct] || acct}</p></main>`, "Feed", hindi ? "hi" : "en"));
  if (url.pathname === "/in/me/" || url.pathname === "/in/me") return send(res, 302, "", { Location: `/in/${acct}/` });
  const m = url.pathname.match(/^\/in\/([^/]+)\/?$/);
  if (m && m[1] === acct) return send(res, 200, page(`<main><h1>${NAMES[acct] || acct}</h1></main>`, NAMES[acct] || acct, hindi ? "hi" : "en"));
  if (m) {
    const slug = m[1]; state.views[slug] = (state.views[slug] || 0) + 1;
    const kind = kindOf(slug);
    if (kind === "checkpoint") return send(res, 302, "", { Location: "/checkpoint/challenge/x" });
    if (kind === "http999") return send(res, 999, "");
    if (kind === "hang") { await new Promise((r) => setTimeout(r, 45000)); return send(res, 200, page("<main>late</main>")); }
    if (kind === "redirect") return send(res, 302, "", { Location: "/in/wrongperson-9" });
    return send(res, 200, profile(cfgFor(slug, acct)));
  }
  send(res, 404, page("<main>Not found</main>"));
};
for (const port of Object.keys(PORTS)) http.createServer(handler(Number(port))).listen(Number(port), () => console.log(`audit fake LinkedIn on ${port} as ${PORTS[port]}`));
