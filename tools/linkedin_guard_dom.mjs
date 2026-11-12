// Page-reading guards for tools/linkedin_agent.mjs: WHO is on the page, WHICH chat is open, and DID a message arrive.
// Everything here only reads the page (plus closing chat windows and clearing a box); it never decides to send. The agent
// calls these before every click and stops with a reason when any of them cannot confirm the lead (fail closed).
// Shipped with the agent everywhere (office bundle, server, cloud supervisor): keep it next to tools/linkedin_agent.mjs.

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
export const norm = (t) => String(t || "").replace(/\s+/g, " ").trim();
const tap = async (loc, ms = 8000) => { try { await loc.click({ timeout: ms }); return true; } catch { return false; } };

// ---- names ---------------------------------------------------------------------------------------------------------
const HONORIFIC = /^(mr|mrs|ms|miss|dr|prof|shri|smt|er|ca|cs|cma|adv|capt|col|maj)\.?\s+/i;
// "Dr Rahul Sharma, MBA (He/Him)" -> "Rahul Sharma"
export const cleanName = (n) => String(n || "").split(/[,(|]/)[0].trim().replace(HONORIFIC, "");
// Usable words of a lead's name. One-letter words (initials) are dropped on purpose: a name that is left with fewer than two
// words cannot be told apart from other people and is never acted on.
// Accents on Latin letters are dropped ("José" = "Jose"); the vowel signs of other scripts (Devanagari ...) are part of the word.
const words = (text) => String(text || "").toLowerCase().normalize("NFKD").replace(/(?<=\p{Script=Latin})\p{M}+/gu, "").replace(/[^\p{L}\p{M}\s]/gu, " ").split(/\s+/).filter((w) => w.length > 1);
export const tokens = (name) => words(cleanName(name));
const labelWords = words;
export const nameProblem = (name) => tokens(name).length < 2
  ? "the lead's name has fewer than two usable words (one name, or initials), so the right person cannot be told apart from others; fix the name in the portal" : "";
// The label contains the lead's words next to each other, in the same order ("Other Kamal" is not "Kamal Other";
// "Anil Verma" is not "Anil Kumar Verma"; "Anil Kumar, Ravi Sharma" is a group, see chatWith).
export function namesLead(label, name) {
  const want = tokens(name), have = labelWords(label);
  if (want.length < 2) return false;
  return (" " + have.join(" ") + " ").includes(" " + want.join(" ") + " ");
}
const namesFirst = (label, name) => { const want = tokens(name), have = labelWords(label); return want.length > 1 && have.includes(want[0]); };
export const slugOf = (url) => { const m = String(url || "").match(/\/in\/([^/?#]+)/i); try { return m ? decodeURIComponent(m[1]).toLowerCase() : ""; } catch { return m ? m[1].toLowerCase() : ""; } };

// ---- the lead's own card -------------------------------------------------------------------------------------------
// The card is the section that holds the profile's <h1>. Everything further down (people you may know, "more profiles for
// you") has Connect / Message buttons for OTHER people.
export const topCard = (page) => page.locator("main section").filter({ has: page.locator("h1") }).first().or(page.locator("main:not(:has(section))")).first();

export async function readH1(page, timeout = 5000) {
  return norm(await page.locator("main h1").first().innerText({ timeout }).catch(() => ""));
}
// "" = this page IS the lead. Anything else = why it is not (wrong link, redirect, not found, private profile, still loading).
export async function identityProblem(page, lead) {
  const bad = nameProblem(lead.name);
  if (bad) return bad;
  const h1 = await readH1(page);
  if (!h1) return "the profile name did not load, so the page cannot be confirmed as this person";
  if (/^linkedin member$/i.test(h1)) return "the profile hides its name (LinkedIn Member), so it cannot be confirmed as this person";
  if (!namesLead(h1, lead.name)) return `the page shows "${h1.slice(0, 60)}", not ${lead.name} (wrong link or a redirect)`;
  const want = slugOf(lead.profile_url), got = slugOf(page.url());
  if (want && got && want !== got) return `the link opened another address (/in/${got}, expected /in/${want}); correct the lead's link in the portal`;
  return "";
}

// "· 1st" is the degree badge; a "1st" inside a name, headline or company ("Ex 1st Lieutenant", "1st Choice Staffing") is not.
export function degreeFromText(text) {
  for (const l of String(text || "").split("\n").map((s) => s.trim()).slice(0, 60)) {
    const m = l.match(/^[·•\s]*(1st|2nd|3rd)\+?(\s+degree connection)?$/i) || l.match(/\s[·•]\s*(1st|2nd|3rd)\+?\s*$/i);
    if (m) return m[1].toLowerCase();
  }
  return "";
}
export async function degreeOf(page) {
  return degreeFromText(await topCard(page).innerText({ timeout: 5000 }).catch(() => ""));
}

// Every visible element matching `selector` that belongs to THIS lead: inside their own card or an open menu (label names the
// first and last name, or at least the first name), or in the sticky header (full name only). Never a sidebar card.
export async function labelledAll(page, selector, name) {
  const all = page.locator(selector), found = [];
  const top = await topCard(page).elementHandle({ timeout: 2000 }).catch(() => null);
  for (let i = 0, n = await all.count(); i < n; i++) {
    const el = all.nth(i);
    const label = await el.getAttribute("aria-label");
    if (!(namesLead(label, name) || namesFirst(label, name)) || !(await el.isVisible().catch(() => false))) continue;
    const where = await el.evaluate((e, t) => (t && t.contains(e)) ? "card" : e.closest('[role="menu"]') ? "menu" : e.closest("header") && !e.closest("aside,[role=complementary]") ? "header" : "other", top).catch(() => "other");
    if (where === "other" || (where === "header" && !namesLead(label, name))) continue;
    const box = await el.boundingBox().catch(() => null);
    if (!box || box.width < 2 || box.height < 2) continue;
    found.push({ el, rank: where === "header" ? 0 : 1 });
  }
  return found.sort((x, y) => y.rank - x.rank).map((f) => f.el);
}

// ---- the chat window -----------------------------------------------------------------------------------------------
export const CHAT_BUBBLE = ".msg-overlay-conversation-bubble";
export async function closeChats(page) {
  for (let i = 0; i < 6 && (await page.locator(CHAT_BUBBLE).count()); i++) {
    const close = page.locator(`${CHAT_BUBBLE} button[aria-label*="Close your conversation" i], ${CHAT_BUBBLE} button[aria-label^="Close" i]`).first();
    if (!(await close.count()) || !(await tap(close, 2000))) break;
    await sleep(400);
  }
}
// The chat that opened: exactly one window, whose HEADER (not the message history) names the person, is not a group, and does
// not link to somebody else. { bubble, title } or { why }.
export async function chatWith(page, lead) {
  const bubbles = page.locator(CHAT_BUBBLE);
  const count = await bubbles.count();
  if (count !== 1) return { why: `${count} chat windows are open (expected only this person's)` };
  const bubble = bubbles.first();
  const head = await bubble.evaluate((root) => {
    const h = root.querySelector(".msg-overlay-bubble-header, header, h2");
    if (!h) return { title: "", links: [] };
    const t = h.querySelector(".msg-overlay-bubble-header__title, .msg-entity-lockup__entity-title") || h;
    const c = t.cloneNode(true);                                  // without the close / action buttons

    c.querySelectorAll("button, svg, [aria-hidden='true']").forEach((n) => n.remove());
    return { title: (c.textContent || "").replace(/\s+/g, " ").trim(), links: [...h.querySelectorAll('a[href*="/in/"]')].map((a) => a.getAttribute("href") || "") };
  }).catch(() => ({ title: "", links: [] }));
  const title = head.title;
  if (!title) return { why: "the chat window shows no name in its header, so it cannot be confirmed as this person's" };
  if (/[,&]|\band\b/i.test(title)) return { why: `the chat that opened is a group chat ("${title.slice(0, 60)}")` };
  if (!namesLead(title, lead.name)) return { why: `the chat that opened is "${title.slice(0, 60)}", not ${lead.name}` };
  const want = slugOf(lead.profile_url), other = head.links.map(slugOf).filter((s) => s && s !== want);
  if (other.length) return { why: `the chat header links to another profile (/in/${other[0]})` };
  return { bubble, title };
}

// ---- the conversation: history loaded, our text delivered ---------------------------------------------------------
// Compare a message by its start AND its end (a long text is checked at both ends; LinkedIn may add a link preview).
const probesOf = (text) => { const t = norm(text); return { head: t.slice(0, 40), tail: t.slice(-40) }; };
// How many messages from US carry this text, split into delivered and "not delivered / failed".
export async function ourCopies(bubble, text) {
  const p = probesOf(text);
  return bubble.evaluate((root, { head, tail }) => {
    let ok = 0, failed = 0;
    for (const it of root.querySelectorAll(".msg-s-event-listitem")) {
      if (it.className.includes("--other")) continue;
      const t = (it.textContent || "").replace(/\s+/g, " ");
      if (!(head && t.includes(head) && t.includes(tail))) continue;
      if (/--failed|--error|not delivered|couldn.?t send/i.test(it.className + " " + t)) failed++; else ok++;
    }
    return { ok, failed };
  }, p).catch(() => ({ ok: 0, failed: 0 }));
}
// The history must have finished loading before we decide "they have not answered": wait until nothing is loading and the
// number of messages stays the same twice. expectSome: a later step with an empty history means "not loaded yet".
export async function readHistory(bubble, expectSome) {
  let last = -1, stable = 0;
  for (let i = 0; i < 16 && stable < 2; i++) {
    const r = await bubble.evaluate((root) => ({ n: root.querySelectorAll(".msg-s-event-listitem").length,
      busy: !!root.querySelector('[aria-busy="true"], [class*="skeleton"], [class*="loading"]') })).catch(() => ({ n: 0, busy: false }));
    stable = !r.busy && r.n === last ? stable + 1 : 0; last = r.n;
    if (stable >= 2) return { unloaded: expectSome && r.n === 0 };
    await sleep(700);
  }
  return { unloaded: true };
}
// LinkedIn's chat window lists the conversation as groups (sender name + messages). Their messages are the ones marked
// "--other" or in a group under their name. Our own name (first AND last) is never "theirs", even when first names coincide.
export async function readThread(bubble, lead, meName) {
  const lt = tokens(lead.name), mt = tokens(meName);
  return bubble.evaluate((root, { first, last, me, meLast }) => {
    const out = [];
    let sender = "";
    for (const ev of root.querySelectorAll(".msg-s-message-list__event")) {
      const n = ev.querySelector(".msg-s-message-group__name");
      if (n) sender = n.textContent.trim().toLowerCase();
      for (const it of ev.querySelectorAll(".msg-s-event-listitem")) {
        const isMe = sender && me && sender.includes(me) && (!meLast || sender.includes(meLast));
        const isLead = sender && first && sender.includes(first) && (!last || sender.includes(last));
        const body = it.querySelector(".msg-s-event-listitem__body");
        if (body && (it.className.includes("msg-s-event-listitem--other") || (isLead && !isMe))) out.push(body.textContent.trim());
      }
    }
    return out.filter(Boolean).slice(-5);
  }, { first: lt[0] || "", last: lt.length > 1 ? lt[lt.length - 1] : "", me: mt[0] || "", meLast: mt.length > 1 ? mt[mt.length - 1] : "" }).catch(() => []);
}

// What the agent SEES right now, sent to the portal with every send so a wrong target can be caught and traced.
export async function observe(page, chatTitle = "") {
  return {
    observed_name: (await readH1(page, 2000)).slice(0, 200),
    observed_url: page.url().split("?")[0].slice(0, 500),
    page_title: norm(await page.title().catch(() => "")).slice(0, 300),
    ...(chatTitle ? { chat_title: chatTitle.slice(0, 200) } : {}),
  };
}
