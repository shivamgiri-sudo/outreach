// The LinkedIn messaging list, read by tools/linkedin_agent.mjs (kept in its own file: ships in the same bundle).
// Each conversation row gives a name and the last-message preview ("You:" = ours, anything else = theirs). Group conversations
// (several names) are left out. When the row links to the person's profile the link goes along, so the portal can tell two
// people of the same name apart. A row WITHOUT a link is opened only when it is from them AND its name matches exactly one
// person we are waiting on (`watching`): the link in the open thread's header is then read (at most MAX_OPEN threads per read).
// Everything else is left for the portal, which raises a "check this" task. Nothing is typed or sent here.
import { namesLead, slugOf } from "./linkedin_guard_dom.mjs";

export const MAX_OPEN = 3;
const ROWS = ".msg-conversation-listitem, .msg-conversation-card";

export async function readInbox(page, { linkedin, settle, log, checkTrouble, watching = [] }) {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  try {
    await page.goto(linkedin + "/messaging/", { waitUntil: "domcontentloaded" }); await sleep(settle);
  } catch (e) {                         // the list did not load: try again next time, never stop the agent over a read
    log("could not open the messaging list:", String(e.message || e).split("\n")[0]);
    return [];
  }
  await checkTrouble(page);             // a login wall or security check here still stops it, as anywhere else
  const items = await page.evaluate((rows) => [...document.querySelectorAll(rows)].slice(0, 40).map((li, idx) => {
    const name = (li.querySelector(".msg-conversation-listitem__participant-names, .msg-conversation-card__participant-names")?.textContent || "").trim();
    const snip = (li.querySelector(".msg-conversation-card__message-snippet, .msg-conversation-listitem__message-snippet")?.textContent || "").trim();
    const ours = /^you\s*:/i.test(snip);
    const link = li.querySelector('a[href*="/in/"]');
    return { idx, name, text: snip.replace(/^[^:]{1,60}:\s*/, ""), from_them: !!name && !ours, profile_url: link ? "https://www.linkedin.com" + new URL(link.href).pathname : "" };
  }).filter((i) => i.name && !i.name.includes(",")), ROWS).catch(() => []);
  let opened = 0;
  for (const item of items) {
    if (item.profile_url || !item.from_them || opened >= MAX_OPEN) continue;
    const who = watching.filter((w) => namesLead(item.name, w.name));
    if (who.length !== 1) continue;                                  // no one, or several of that name: leave it to the portal
    opened++;
    item.profile_url = await threadLink(page, item, who[0], settle).catch(() => "");
  }
  return items.map(({ idx, ...rest }) => rest);
}

// Open one conversation and read the profile link in its header. "" unless the header names exactly this person.
async function threadLink(page, item, lead, settle) {
  const row = page.locator(ROWS).nth(item.idx);
  const target = row.locator("a, [role=button]").first();
  await (await target.count() ? target : row).click({ timeout: 6000 });
  await new Promise((r) => setTimeout(r, settle));
  const head = await page.evaluate(() => {
    const h = document.querySelector(".msg-thread__link-to-profile, .msg-thread header, .msg-title-bar, .msg-entity-lockup");
    if (!h) return { title: "", links: [] };
    const own = h.matches("a") ? [h.getAttribute("href") || ""] : [];
    return { title: (h.textContent || "").replace(/\s+/g, " ").trim(), links: [...own, ...[...h.querySelectorAll('a[href*="/in/"]')].map((a) => a.getAttribute("href") || "")] };
  }).catch(() => ({ title: "", links: [] }));
  const slugs = [...new Set(head.links.map(slugOf).filter(Boolean))];
  if (slugs.length !== 1 || !namesLead(head.title, lead.name)) return "";         // a group, no link or another person: say nothing
  return "https://www.linkedin.com/in/" + slugs[0];
}
