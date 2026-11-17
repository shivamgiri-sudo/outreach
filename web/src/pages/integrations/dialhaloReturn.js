// DialHalo's one-click connect sends the browser back to #integrations/dialhalo/connected?tab=calltracker&dh_state=…&dh_code=…
// (or dh_error=…). Integrations.jsx reads this on its first render, before it tidies the hash, and hands it to CallTracker.jsx,
// which finishes the connection through the signed-in API. Pure, so tests/dialhalo_return.mjs can run it under node.
export function readConnectReturn(hash) {
  const text = String(hash || "");
  const at = text.indexOf("?");
  const q = new URLSearchParams(at >= 0 ? text.slice(at + 1) : "");
  const state = (q.get("dh_state") || "").slice(0, 512);
  if (!state) return null;
  return { state, code: (q.get("dh_code") || "").slice(0, 512), error: (q.get("dh_error") || "").slice(0, 60) };
}
