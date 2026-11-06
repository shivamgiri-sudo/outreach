// Setup guides (served by /api/guides): loaded once, shared by every "How do I get this?" button and the Help centre.
import { useEffect, useState } from "react";
import { api } from "./api.js";

let cache = null;
let pending = null;
const listeners = new Set();

export function loadGuides() {
  if (cache) return Promise.resolve(cache);
  if (!pending) {
    pending = api.guides().then((data) => {
      cache = { ...data, byId: Object.fromEntries(data.guides.map((g) => [g.id, g])) };
      listeners.forEach((fn) => fn(cache));
      return cache;
    }).catch((e) => { pending = null; throw e; });
  }
  return pending;
}

export function useGuides() {
  const [data, setData] = useState(cache);
  const [error, setError] = useState("");
  useEffect(() => {
    if (cache) return undefined;
    listeners.add(setData);
    loadGuides().catch((e) => setError(e.message));
    return () => listeners.delete(setData);
  }, []);
  return { data, error };
}

// Guide ids for one or more screen keys (e.g. "hub:email_send:resend"), best first, without repeats.
export function guideIds(data, keys) {
  if (!data) return [];
  const list = (Array.isArray(keys) ? keys : [keys]).flatMap((k) => data.attach[k] || []);
  return [...new Set(list)];
}

// Field help for a form field: the first attached guide that describes this field key.
export function fieldHelp(data, keys, fieldKey) {
  for (const id of guideIds(data, keys)) {
    const f = data.byId[id]?.fields?.[fieldKey];
    if (f) return f;
  }
  return null;
}

function test(pattern, value) {
  try { return new RegExp(pattern).test(value); } catch { return true; }
}

// A friendly message when a typed value does not look right, or "" when it looks fine (or the box is empty).
// Never blocks saving: the server decides what it accepts.
export function checkValue(help, value) {
  const v = typeof value === "string" ? value.trim() : "";
  if (!help || !v) return "";
  const warn = (help.warn || []).find((w) => test(w.pattern, v));
  if (warn) return warn.message;
  if (help.pattern && help.message && !test(help.pattern, v)) return help.message;
  return "";
}

export function searchGuides(guides, query) {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return guides;
  return guides.filter((g) => {
    const text = `${g.title} ${g.summary} ${g.category} ${g.keywords} ${g.steps.join(" ")}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}
