// Hash routing for sub-views: "#page" or "#page?key=value" (for example #leads?lead=ab12, #campaigns?campaign=c1).
// The page part is read by App.jsx; pages read and write their own keys with useHashParam, so refresh, the back
// button and shared links keep the open drawer or detail view.
import { useCallback, useEffect, useState } from "react";

const PUSHED = "hash-param";   // marks history entries this module pushed, so closing can step back instead of adding one

export function hashParts() {
  const raw = window.location.hash.replace(/^#\/?/, "");
  const at = raw.indexOf("?");
  return { page: at < 0 ? raw : raw.slice(0, at), params: new URLSearchParams(at < 0 ? "" : raw.slice(at + 1)) };
}

export function hashParam(key) {
  return hashParts().params.get(key);
}

function notify() {
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

// Set (or with null/"" remove) keys in the current hash. Opening pushes a history entry; closing steps back when the
// entry being closed was pushed here, so Back after Close does not reopen it.
export function setHashParams(updates, { replace = false } = {}) {
  const { page, params } = hashParts();
  let removing = false, switching = false;
  for (const [k, v] of Object.entries(updates)) {
    if (v === null || v === undefined || v === "") { if (params.has(k)) removing = true; params.delete(k); } else {
      if (params.has(k)) switching = true;          // e.g. another lead while a lead is open: replace, do not stack
      params.set(k, String(v));
    }
  }
  const qs = params.toString();
  const next = `#${page || "dashboard"}${qs ? `?${qs}` : ""}`;
  if (next === window.location.hash) return;
  if (removing && !replace && window.history.state?.[PUSHED]) {
    window.history.back();
    return;
  }
  if (replace || removing || switching) window.history.replaceState(window.history.state, "", next);
  else window.history.pushState({ [PUSHED]: true }, "", next);
  notify();
}

// [value, setValue] for one hash key; the value follows Back/Forward and links.
export function useHashParam(key) {
  const [value, setValue] = useState(() => hashParam(key));
  useEffect(() => {
    const sync = () => setValue(hashParam(key));
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => { window.removeEventListener("hashchange", sync); window.removeEventListener("popstate", sync); };
  }, [key]);
  const set = useCallback((next, opts) => setHashParams({ [key]: next }, opts), [key]);
  return [value, set];
}
