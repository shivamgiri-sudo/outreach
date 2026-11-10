// Every chapter is one small file in ./chapters/ (default export). Adding a file adds a chapter; no list to edit.
//   id        short and URL-safe: the page is #help/<id>.         kind   "start" | "page" | "setup" | "recipe" | "faq"
//   group     one of GROUPS (meta.js)                              order  number: sort order inside the group
//   menu      for kind page/setup: the exact menu or Setup label (a test checks it exists and that every one has a chapter)
//   roles     which roles can use the page: any of owner, admin, caller, viewer (default: all four)
//   masOnly   true: hidden from client company logins        clientOnly   true: only for client company logins (owners see it too)
//   summary   one sentence    blocks   see markup.jsx
import { GROUPS } from "./meta.js";

const modules = import.meta.glob("./chapters/*.js", { eager: true });
const ALL_ROLES = ["owner", "admin", "caller", "viewer"];

export const CHAPTERS = Object.values(modules).map((m) => ({ roles: ALL_ROLES, masOnly: false, clientOnly: false, order: 50, ...m.default }))
  .sort((a, b) => GROUPS.indexOf(a.group) - GROUPS.indexOf(b.group) || a.order - b.order || a.title.localeCompare(b.title));
export const chapterById = (id) => CHAPTERS.find((c) => c.id === id) || null;

// ctx = { role, client } as the app knows them (api.js getRole / isClient). Mas Callnet owners see everything.
export function canSee(chapter, ctx) {
  const role = ctx.role || "owner";
  if (role === "owner" && !ctx.client) return true;
  if (chapter.masOnly && ctx.client) return false;
  if (chapter.clientOnly && !ctx.client) return false;
  return chapter.roles.includes(role);
}
export const visibleChapters = (ctx) => CHAPTERS.filter((c) => canSee(c, ctx));

// The small badges beside a chapter title.
export function badgesFor(c) {
  const out = [];
  if (ALL_ROLES.every((r) => c.roles.includes(r))) out.push("Everyone");
  else out.push(...c.roles.map((r) => ({ owner: "Owner", admin: "Admin", caller: "Caller", viewer: "Viewer" }[r])));
  if (c.masOnly) out.push("Mas Callnet only");
  if (c.clientOnly) out.push("Client companies");
  return out;
}

// "Sales stage" -> "sales-stage": heading ids for #help/<chapter>/<heading>.
export const slug = (t) => String(t).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
