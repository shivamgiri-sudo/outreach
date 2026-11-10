import { useEffect, useState } from "react";
import { api } from "../api.js";
import { istLabel } from "../time.js";

// What the LinkedIn agent will do next, in plain words, from GET /api/linkedin-agent. Used by the status strip at the
// top of the LinkedIn page and by every "Start LinkedIn" button, so a person always knows WHEN something will happen
// (the agent works one action at a time with a fixed gap, inside working hours, so nothing moves at once).

const hours = (cfg) => `${cfg.start_hour}:00–${cfg.end_hour}:00 IST${cfg.weekdays_only ? ", Mon–Fri" : ""}`;
const minutes = (s) => (s <= 60 ? "about a minute" : `about ${Math.ceil(s / 60)} minutes`);

// [tone, headline, detail]. tone: ok | warn | err | "" (neutral).
export function agentSummary(st) {
  if (!st) return ["", "Checking the LinkedIn agent…", ""];
  const c = st.control || {};
  const cfg = st.config || {};
  const queued = st.today?.queued ?? 0;
  const left = `left today: ${c.connections_left_today ?? 0} requests, ${c.messages_left_today ?? 0} messages`;
  const rhythm = `one action every ${c.gap_minutes ?? cfg.gap_minutes} min, ${hours(cfg)}`;
  if (st.kill) return ["err", "Every LinkedIn agent is stopped by the platform admin", "Nothing is sent until the platform admin lets agents run again."];
  if (!cfg.enabled) return ["warn", "The LinkedIn agent is switched off", "People you start wait in the list. Switch the agent on (Agent & accounts tab), or send by hand from the list below."];
  const online = st.today ? st.today.online : st.online;
  if (!online) {
    const last = st.today?.last_seen;
    const seen = last ? `last seen ${istLabel(last)}` : "never seen";
    return ["err", "The agent program is not running", `It is switched on, but no office computer or server is running it (${seen}). Nothing is sent until it runs.`];
  }
  if (c.why_not) return ["warn", `Waiting: ${c.why_not}`, `${rhythm} · ${left}.`];
  if ((c.connections_left_today ?? 0) === 0 && (c.messages_left_today ?? 0) === 0) return ["warn", "Today's limits are used up", `It carries on tomorrow (${hours(cfg)}).`];
  const next = c.wait_seconds > 0 ? `next action in ${minutes(c.wait_seconds)}` : queued ? "next action within a minute" : "nothing waiting";
  return ["ok", "The LinkedIn agent is working", `${next} · ${queued} waiting · ${rhythm} · ${left}.`];
}

// One sentence for after a start: when will the first step actually go out?
export function whenItSends(st) {
  if (!st) return "The LinkedIn agent sends it at its next turn.";
  const [tone, head, detail] = agentSummary(st);
  if (tone === "ok") {
    const c = st.control || {};
    const queued = st.today?.queued ?? 0;
    const wait = (c.wait_seconds || 0) + Math.max(0, queued - 1) * (c.gap_minutes || 6) * 60;
    return `The agent sends one action at a time; with ${queued} waiting, expect it in ${minutes(Math.max(60, wait))}. Watch it under "Today" on the LinkedIn page.`;
  }
  return `Nothing goes out yet: ${head.charAt(0).toLowerCase()}${head.slice(1)}. ${detail}`;
}

export function useAgentStatus(refreshMs = 20000) {
  const [st, setSt] = useState(null);
  const [error, setError] = useState("");
  const load = () => api.linkedinAgent().then((s) => { setSt(s); setError(""); }).catch((e) => setError(e.message));
  useEffect(() => { load(); const t = setInterval(load, refreshMs); return () => clearInterval(t); }, []);
  return [st, load, error];
}

// The strip at the top of the LinkedIn page's Today tab.
export default function LinkedInStatus({ st, error, onOpenAgent }) {
  if (error) return <div className="li-status err" role="status" data-testid="li-status">The agent status could not load: {error}</div>;
  const [tone, head, detail] = agentSummary(st);
  return (
    <div className={`li-status ${tone}`} role="status" data-testid="li-status">
      <div>
        <b data-testid="li-status-head">{head}</b>
        {detail && <div className="sub">{detail}</div>}
      </div>
      {onOpenAgent && tone !== "ok" && <button type="button" className="btn ghost" onClick={onOpenAgent}>Open agent settings</button>}
    </div>
  );
}
