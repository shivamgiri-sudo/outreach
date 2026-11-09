// Lead drawer: the lead's Sales stage (separate from the outreach stage above it). The server enforces the rules and
// answers with the reason in plain words; every change is kept in the history below.
import { useEffect, useState } from "react";
import { canWrite } from "../api.js";
import { sales } from "../salesApi.js";
import { inr, statusShort } from "../salesFormat.js";
import { istLabel } from "../time.js";
import "../funnel.css";

const blankForm = (d) => ({ status: d.status, est: d.est_value || "", closed: d.closed_value || "", tenure: d.tenure || "", reason: d.lost_reason || "" });

export default function LeadSales({ lead, onChanged }) {
  const [d, setD] = useState(null);
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState({ text: "", ok: false });
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    setD(null); setMsg({ text: "", ok: false });
    sales.lead(lead.id).then((r) => { setD(r); setForm(blankForm(r)); }).catch((e) => setMsg({ text: e.message, ok: false }));
  }, [lead.id]);
  if (!d || !form) return msg.text ? <div className="ls-panel tip error" role="alert">{msg.text}</div> : null;
  const set = (k, v) => setForm({ ...form, [k]: v });
  const needsEst = ["Sales Qualified Lead (SQL)", "Discovery Meeting Held", "Proposal Submitted", "Negotiation / Commercials", "Closed Won"].includes(form.status);
  const save = async (e) => {
    e.preventDefault();
    setBusy(true); setMsg({ text: "", ok: false });
    const body = { est_value: Number(form.est) || 0, closed_value: Number(form.closed) || 0, lost_reason: form.reason };
    if (form.tenure !== "") body.tenure = Number(form.tenure);
    if (form.status !== d.status || !d.has_record) body.status = form.status;
    try {
      const r = await sales.saveLead(lead.id, body);
      setD(r); setForm(blankForm(r));
      setMsg({ text: r.changed ? `Saved. Sales stage: ${statusShort(r.status)}.` : "Nothing to change.", ok: true });
      if (r.changed) onChanged?.();
    } catch (err) { setMsg({ text: err.message, ok: false }); }
    setBusy(false);
  };
  const writable = canWrite();
  return (
    <section className="ls-panel" aria-labelledby="ls-title" data-testid="lead-sales">
      <h3 id="ls-title">Sales stage <span className="pill rec" data-testid="lead-sales-status">{statusShort(d.status)}</span></h3>
      <p className="sub" style={{ margin: "0 0 8px" }}>Where this deal stands in the sales funnel; the outreach stage above is unchanged. Probability {d.prob}%{d.tcv ? ` · Total contract value ${inr(d.tcv)}` : ""}.</p>
      <form onSubmit={save} aria-label="Sales stage">
        <div className="ls-grid">
          <label>Status
            <select value={form.status} disabled={!writable} onChange={(e) => set("status", e.target.value)} data-testid="lead-sales-select">
              {d.statuses.map((s) => <option key={s} value={s}>{s}</option>)}
            </select></label>
          <label>Est. monthly value (₹){needsEst ? " *" : ""}
            <input type="number" min="0" value={form.est} disabled={!writable} onChange={(e) => set("est", e.target.value)} data-testid="lead-sales-est" /></label>
          {(form.status === "Closed Won" || d.closed_value > 0) && <>
            <label>Closed monthly value (₹) *
              <input type="number" min="0" value={form.closed} disabled={!writable} onChange={(e) => set("closed", e.target.value)} data-testid="lead-sales-closed" /></label>
            <label>Contract tenure (months)
              <input type="number" min="1" placeholder="12" value={form.tenure} disabled={!writable} onChange={(e) => set("tenure", e.target.value)} /></label>
          </>}
          {(form.status === "Closed Lost" || d.lost_reason) && (
            <label>Lost reason *
              <select value={form.reason} disabled={!writable} onChange={(e) => set("reason", e.target.value)} data-testid="lead-sales-reason">
                <option value="">Pick a reason</option>{d.lost_reasons.map((r) => <option key={r} value={r}>{r}</option>)}
              </select></label>
          )}
        </div>
        {msg.text && <div className={`tip ${msg.ok ? "" : "error"}`} role={msg.ok ? "status" : "alert"} style={{ marginTop: 8 }} data-testid="lead-sales-msg">{msg.text}</div>}
        {writable && <button className="btn" type="submit" disabled={busy} style={{ marginTop: 8 }} data-testid="lead-sales-save">{busy ? "Saving…" : "Save sales stage"}</button>}
      </form>
      {d.backfilled.length > 0 && <p className="sub" style={{ margin: "8px 0 0" }}>Back-filled stages (no real date): {d.backfilled.join(", ")}.</p>}
      {d.history.length > 0 && (
        <details style={{ marginTop: 8 }}>
          <summary>History ({d.history.length})</summary>
          <ul className="ls-history" data-testid="lead-sales-history">{d.history.map((h, i) => <li key={i}>{istLabel(h.at)}: {statusShort(h.from)} → <b>{statusShort(h.to)}</b> by {h.by || "—"}</li>)}</ul>
        </details>
      )}
    </section>
  );
}
