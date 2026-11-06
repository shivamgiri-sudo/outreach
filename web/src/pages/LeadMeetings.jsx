import { useEffect, useState } from "react";
import { api } from "../api.js";
import { copyText } from "../clipboard.js";
import { istDate, istLabel, outsideBusinessHours } from "../time.js";
import { getBrand } from "../brand.js";

const DURATIONS = [15, 30, 45, 60];
const OWN_INVITE = ["zoom", "custom_http", "booking_link"];      // these don't email the lead, so we send an .ics invite
const SENDS_OWN = ["google", "microsoft"];                          // these email the lead themselves

function defaultTitle(lead) {
  const company = (lead.company || lead.domain || "").replace(/\s*\(.*?\)/g, "").replace(/\s+(pvt|private|ltd|limited|llp|inc)\.?(?=\s|$)/gi, "").trim();
  const product = getBrand().public.product_name;
  return company ? `${product} demo — ${company}` : `${product} demo`;
}

// "Book a meeting" inside the lead panel: uses the active calendar provider; times are entered and shown in IST.
export default function LeadMeetings({ lead, onChanged }) {
  const [calendar, setCalendar] = useState(undefined);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [link, setLink] = useState("");
  const [cancelling, setCancelling] = useState("");

  useEffect(() => {
    api.channels().then((d) => setCalendar(d.channels.find((c) => c.id === "calendar") || null)).catch(() => setCalendar(null));
  }, []);
  const active = calendar?.providers.find((p) => p.active);
  const fixedTime = active?.id === "booking_link" && form?.fixed;    // a plain booking link used with a set time
  const linkBased = active?.link_based && !fixedTime;
  const meetings = [...(lead.meetings || [])].sort((a, b) => (a.start || "").localeCompare(b.start || ""));

  const openForm = () => setForm({ date: istDate(1), time: "11:00", duration: 30, title: defaultTitle(lead), attendee: lead.email || "", notes: "",
    fixed: false, sendInvite: false });
  const book = async (e) => {
    e.preventDefault();
    setBusy(true); setError(""); setLink("");
    try {
      const body = linkBased ? { attendee_email: form.attendee } : {
        start: `${form.date}T${form.time}:00+05:30`, duration_minutes: Number(form.duration), title: form.title,
        attendee_email: form.attendee, notes: form.notes, send_invite: !!form.sendInvite,
      };
      const r = await api.bookMeeting(lead.id, body);
      if (r.kind === "link") setLink(r.url);
      else setForm(null);
      if (r.invite?.status === "skipped") setError(`No calendar invite sent: ${r.invite.reason}.`);
      onChanged?.();
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const markDone = async (id) => {
    setBusy(true); setError("");
    try { await api.meetingDone(id); onChanged?.(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };
  const cancel = async (id) => {
    setBusy(true); setError("");
    try { await api.cancelMeeting(id); setCancelling(""); onChanged?.(); } catch (err) { setError(err.message); } finally { setBusy(false); }
  };

  return (
    <section className="lead-meetings" data-testid="lead-meetings">
      <h3>Meetings</h3>
      {error && <div className="tip error" role="alert">{error}</div>}
      {meetings.length > 0 && (
        <ul className="meet-mini">
          {meetings.map((m) => (
            <li key={m.id}>
              <div>
                <b>{istLabel(m.start)}</b> <span className={`pill ${m.status === "scheduled" ? "ok" : ""}`}>{m.status}</span>
                <br /><small>{m.title} · {m.provider}{m.invite ? ` · invite ${m.invite === "dry_run" ? "kept as practice" : m.invite}` : ""}</small>
              </div>
              <div className="row">
                {m.join_url && m.status === "scheduled" && <a className="btn ghost" href={m.join_url} target="_blank" rel="noreferrer">Join</a>}
                {m.status === "scheduled" && new Date(m.start) < new Date() && (
                  <button className="btn ghost" disabled={busy} onClick={() => markDone(m.id)}>Mark done</button>
                )}
                {m.status === "scheduled" && (cancelling === m.id ? (
                  <>
                    <button className="btn danger-solid" disabled={busy} onClick={() => cancel(m.id)}>Yes, cancel it</button>
                    <button className="btn ghost" onClick={() => setCancelling("")}>Keep</button>
                  </>
                ) : <button className="btn ghost danger" onClick={() => setCancelling(m.id)}>Cancel</button>)}
              </div>
            </li>
          ))}
        </ul>
      )}
      {calendar === undefined && <p className="sub">Checking your calendar…</p>}
      {calendar !== undefined && !active && (
        <p className="sub">No calendar connected yet. <a href="#integrations?tab=calendar">Connect one on the Integrations page</a> to book demos with a video link.</p>
      )}
      {active && !form && <button className="btn ghost" onClick={openForm} disabled={lead.stage === "unsubscribed"}>Book a meeting ({active.title})</button>}
      {active && form && (
        <form className="inline-form" onSubmit={book}>
          {active.id === "booking_link" && (
            <label className="row" style={{ fontWeight: 400 }}>
              <input type="checkbox" style={{ width: "auto" }} checked={!!form.fixed} onChange={(e) => setForm({ ...form, fixed: e.target.checked })} />
              Pick a fixed time and email them a calendar invite with this link
            </label>
          )}
          {linkBased ? (
            <p style={{ margin: 0 }}>
              You get a link to your booking page ({active.title}); the lead picks a slot.{" "}
              {active.webhook ? "Their booking then appears here by itself." : "This page does not report bookings back, so check your calendar."}
            </p>
          ) : (
            <>
              <div className="split-2">
                <label>Date (IST)<input type="date" required value={form.date} min={istDate(0)} onChange={(e) => setForm({ ...form, date: e.target.value })} /></label>
                <label>Time (IST)<input type="time" required value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} /></label>
              </div>
              {outsideBusinessHours(form.date, form.time) && <p className="field-help">Heads-up: that is outside business hours (Mon–Sat, 09:00–18:00 IST).</p>}
              <label>Duration
                <select value={form.duration} onChange={(e) => setForm({ ...form, duration: e.target.value })}>
                  {DURATIONS.map((d) => <option key={d} value={d}>{d} minutes</option>)}
                </select>
              </label>
              <label>Title<input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label>
              <label>Notes (optional)<input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} /></label>
              {OWN_INVITE.includes(active.id) ? (
                <p className="field-help">We email the lead a calendar invite (.ics) with the join link. With real sending off it is kept as practice.</p>
              ) : SENDS_OWN.includes(active.id) && (
                <label className="row" style={{ fontWeight: 400 }}>
                  <input type="checkbox" style={{ width: "auto" }} checked={!!form.sendInvite} onChange={(e) => setForm({ ...form, sendInvite: e.target.checked })} />
                  Send our own invite too ({active.title} already emails them one)
                </label>
              )}
            </>
          )}
          <label>Attendee email<input type="email" value={form.attendee} onChange={(e) => setForm({ ...form, attendee: e.target.value })} /></label>
          <div className="row">
            <button className="btn" type="submit" disabled={busy}>{busy ? "Working…" : linkBased ? "Get booking link" : "Book meeting"}</button>
            <button className="btn ghost" type="button" onClick={() => { setForm(null); setLink(""); }}>Close</button>
          </div>
          {link && (
            <div className="tip" role="status">
              Send this link to the lead: <code>{link}</code>
              <div><button type="button" className="btn ghost" style={{ marginTop: 6 }} onClick={() => copyText(link)}>Copy link</button></div>
            </div>
          )}
        </form>
      )}
    </section>
  );
}
