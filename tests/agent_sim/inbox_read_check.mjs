// Checks tools/linkedin_agent_inbox.mjs against a tiny stand-in messaging page: prints one JSON line.
// Rows: Ann (link on the row), Bob (no link, from them, one waiting lead), Cat (no link, from them, NOT on the watch list),
// Dan (no link, from us), Eve (no link, from them, two leads of that name), Fay (header shows a group): thread opens must be <= 3.
import http from "node:http";
import { pathToFileURL } from "node:url";
const { chromium } = await import(process.env.PLAYWRIGHT_ENTRY || "playwright");
const { readInbox } = await import(pathToFileURL(process.argv[2]).href);
const rows = [["Ann Aa", "Ann: hi", "ann-aa"], ["Bob Bb", "Bob: hello", ""], ["Cat Cc", "Cat: hey", ""], ["Dan Dd", "You: yo", ""],
  ["Eve Ee", "Eve: ok", ""], ["Fay Ff", "Fay: ok", ""], ["Gus Gg", "Gus: ok", ""], ["Hal Hh", "Hal: ok", ""], ["Ivy Ii", "Ivy: ok", ""]];
const opened = [];
const html = `<main><ul>${rows.map(([n, s, slug], i) => `<li class="msg-conversation-listitem" data-i="${i}"><a href="javascript:void 0" ${slug ? `data-x=1` : ""}>
 ${slug ? `<a href="/in/${slug}">p</a>` : ""}<h3 class="msg-conversation-listitem__participant-names">${n}</h3><p class="msg-conversation-card__message-snippet">${s}</p></a></li>`).join("")}</ul>
 <div class="msg-thread"><header class="msg-title-bar"></header></div></main>
<script>
const names = ${JSON.stringify(rows.map((r) => r[0]))};
for (const li of document.querySelectorAll("li")) li.addEventListener("click", () => {
  const n = names[li.dataset.i]; fetch("/__open?n=" + encodeURIComponent(n));
  const slug = n.toLowerCase().replace(" ", "-");
  document.querySelector("header").innerHTML = n === "Fay Ff" ? '<a href="/in/fay-ff">Fay Ff</a>, <a href="/in/zed-zz">Zed Zz</a>' : '<a href="/in/' + slug + '-real">' + n + '</a>';
});
</script>`;
const server = http.createServer((req, res) => {
  if (req.url.startsWith("/__open")) { opened.push(decodeURIComponent(req.url.split("n=")[1])); res.end("ok"); return; }
  res.writeHead(200, { "Content-Type": "text/html" }); res.end(`<!doctype html><html><body>${html}</body></html>`);
}).listen(0);
const base = `http://localhost:${server.address().port}`;
const browser = await chromium.launch();
const page = await browser.newPage();
const watching = [{ lead_id: "1", name: "Bob Bb" }, { lead_id: "2", name: "Eve Ee" }, { lead_id: "3", name: "Eve Ee" }, { lead_id: "4", name: "Fay Ff" },
  { lead_id: "5", name: "Gus Gg" }, { lead_id: "6", name: "Hal Hh" }, { lead_id: "7", name: "Ivy Ii" }];
const items = await readInbox(page, { linkedin: base, settle: 100, log: () => {}, checkTrouble: async () => {}, watching });
console.log(JSON.stringify({ items: items.map((i) => [i.name, i.profile_url]), opened }));
await browser.close(); server.close();
