import { useEffect, useState } from "react";
import { api } from "../api.js";
import { ErrorState } from "../states.jsx";
import LeadDrawer from "./LeadDrawer.jsx";
import { useHashParam } from "../route.js";

const LABEL_WORDS = { interested: "Interested", question: "Question", not_now: "Not now", wrong_person: "Wrong person", other: "Needs a look",
  not_interested: "Not interested", auto_reply: "Auto-reply", unsubscribe: "Unsubscribe", bounce: "Bounce" };
const LABEL_PILL = { interested: "ok", question: "ok", not_now: "", wrong_person: "warn", other: "warn", not_interested: "", auto_reply: "", unsubscribe: "warn", bounce: "warn" };
const CHANNEL_WORDS = { email: "Email", linkedin: "LinkedIn", whatsapp: "WhatsApp", sms: "SMS", call: "Call" };

function when(hours) {
  if (hours < 1) return "just now";
  if (hours < 24) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function Replies() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [label, setLabel] = useState("");
  const [channel, setChannel] = useState("");
  const [onlyAction, setOnlyAction] = useState(true);
  const [lead, setLead] = useHashParam("lead");
  const load = () => {
    setError("");
    api.replyInbox({ label, channel, only_action: onlyAction }).then(setData).catch((e) => setError(e.message));
  };
  useEffect(() => { load(); }, [label, channel, onlyAction]);

  return (
    <>
      <div className="page-head"><div><h2>Replies</h2>
      <p className="sub">Every reply from every channel in one list. The score (0–100) says how much a reply deserves a person's time now: a yes with a buying word is near the top; a refusal, bounce or auto-reply sits at the bottom.</p></div></div>
      {error && <ErrorState message="Replies could not load." detail={error} onRetry={load} />}
      {data && (
        <div className="kpi-row kpi-mini" style={{ marginBottom: 14 }}>
          <div className="kpi"><b>{data.needs_action}</b><span>need a person</span></div>
          <div className="kpi"><b>{data.total}</b><span>replies in all</span></div>
          <div className="kpi"><b>{data.counts.interested || 0}</b><span>said yes</span></div>
        </div>
      )}
      <div className="row" style={{ marginBottom: 10 }}>
        <label className="row" style={{ gap: 6 }}><input type="checkbox" checked={onlyAction} onChange={(e) => setOnlyAction(e.target.checked)} /> Only ones that need a person</label>
        <select aria-label="Kind of reply" value={label} onChange={(e) => setLabel(e.target.value)}>
          <option value="">All kinds</option>
          {(data?.labels || []).map((l) => <option key={l} value={l}>{LABEL_WORDS[l] || l}{data?.counts[l] ? ` (${data.counts[l]})` : ""}</option>)}
        </select>
        <select aria-label="Channel" value={channel} onChange={(e) => setChannel(e.target.value)}>
          <option value="">All channels</option>
          {(data?.channels || []).map((c) => <option key={c} value={c}>{CHANNEL_WORDS[c] || c}</option>)}
        </select>
      </div>
      {data && data.replies.length === 0 && (
        <p className="empty">{onlyAction ? "Nothing needs a person right now." : "No replies yet."} Replies appear here when they are read from your inbox, LinkedIn agent or pasted on a lead.</p>
      )}
      {data && data.replies.length > 0 && (
        <div className="table-wrap">
          <table className="stack-table">
            <thead><tr><th>Score</th><th>Who</th><th>Reply</th><th>Next step</th></tr></thead>
            <tbody>
              {data.replies.map((r, i) => (
                <tr key={`${r.lead_id}-${r.at}-${i}`}>
                  <td data-label="Score"><b>{r.score}</b></td>
                  <td data-label="Who">
                    <button className="link" onClick={() => setLead(r.lead_id)}>{r.company || r.contact || "Lead"}</button>
                    <div className="sub">{r.contact}{r.title ? ` · ${r.title}` : ""}</div>
                  </td>
                  <td data-label="Reply">
                    <span className={`pill ${LABEL_PILL[r.label] || ""}`}>{LABEL_WORDS[r.label] || r.label}</span>{" "}
                    <span className="sub">{CHANNEL_WORDS[r.channel] || r.channel} · {when(r.age_hours)}</span>
                    <div>{r.text.slice(0, 220)}{r.text.length > 220 ? "…" : ""}</div>
                  </td>
                  <td data-label="Next step">{r.next_step}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {lead && <LeadDrawer id={lead} onClose={() => { setLead(null); load(); }} />}
    </>
  );
}
