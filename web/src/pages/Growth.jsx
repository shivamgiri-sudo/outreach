import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { getBrand } from "../brand.js";

const STEP_LABELS = {
  invited: "Invited", opened: "Opened demo", drafted: "Drafted a role", published: "Published role",
  first_candidate: "First candidate screened", first_booking: "First interview booked", converted: "Became paying",
};
const OFFER_FIELDS = [
  ["candidate_cap", "Free candidates screened"], ["booking_cap", "Free interviews booked"],
  ["trial_days", "Free period (days)"], ["referral_bonus_candidates", "Referral bonus (extra candidates)"],
];
const PARTNER_STATUSES = ["to_contact", "contacted", "active", "paused"];

export default function Growth() {
  const [g, setG] = useState(null);
  const [offer, setOffer] = useState(null);
  const [partners, setPartners] = useState([]);
  const [newPartner, setNewPartner] = useState({ name: "", kind: "community", contact: "" });
  const [message, setMessage] = useState("");

  const [loadError, setLoadError] = useState("");
  const load = () => {
    setLoadError("");
    api.growth().then((d) => { setG(d); setOffer(d.offer); }).catch((e) => setLoadError(e.message));
    api.partners().then(setPartners).catch(() => {});
  };
  useEffect(() => { load(); }, []);

  if (loadError) return <ErrorState message="The free-experience page could not load." detail={loadError} onRetry={load} />;
  if (!g || !offer) return <p className="empty">Loading…</p>;
  const max = Math.max(1, ...Object.values(g.activation_funnel));
  const num = (k, v) => setOffer({ ...offer, [k]: Number(v) });

  // A refused save (callers and viewers cannot change the offer) shows the server's sentence instead of failing silently.
  const saveOffer = async () => {
    try { await api.saveOffer(offer); setMessage("Saved. New emails and demo pages use these numbers."); load(); } catch (e) { setMessage(e.message); }
  };
  const addPartner = async () => {
    try { await api.addPartner(newPartner); setNewPartner({ name: "", kind: "community", contact: "" }); setMessage(""); load(); } catch (e) { setMessage(e.message); }
  };

  return (
    <>
      <h2>Free experience</h2>
      <p className="sub">Let employers try it first. Give a real, capped taste, track whether they reach a result, and talk about paying only after.</p>
      {message && <div className="tip" role="status" style={{ marginBottom: 12 }}>{message}</div>}

      <div className="grid g2">
        <div className="card">
          <h3 style={{ marginTop: 0 }}>The free offer</h3>
          <fieldset className="ro-fieldset" disabled={!isOwner()}>
          {OFFER_FIELDS.map(([key, label]) => (
            <div key={key}><label>{label}</label><input type="number" min="0" value={offer[key]} onChange={(e) => num(key, e.target.value)} /></div>
          ))}
          <label>Where “Use this draft” sends them</label>
          <input value={offer.claim_url} onChange={(e) => setOffer({ ...offer, claim_url: e.target.value })} />
          </fieldset>
          {isOwner() && <button className="btn" style={{ marginTop: 12 }} onClick={saveOffer}>Save offer</button>}
          <p className="sub" style={{ marginTop: 10 }}>
            These numbers appear in emails and demo pages. The limits themselves must also be set inside {getBrand().public.product_name}, or free use has no ceiling.
          </p>
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Did they reach a result?</h3>
          {Object.entries(STEP_LABELS).map(([step, label]) => (
            <div className="funnel-row" key={step}>
              <span>{label}</span>
              <div className="bar" style={{ width: `${(g.activation_funnel[step] / max) * 100}%` }} />
              <b>{g.activation_funnel[step]}</b>
            </div>
          ))}
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Do today</h3>
          {g.nudges.length === 0 && <p className="empty">Nobody is waiting on a nudge.</p>}
          {g.nudges.map((n) => <p key={n.lead_id}><b>{n.company}</b> — {n.text}</p>)}
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Time for an honest heads-up</h3>
          {g.conversion_due.length === 0 && <p className="empty">No one is near the end of the free allowance.</p>}
          {g.conversion_due.map((c) => (
            <p key={c.lead_id}><b>{c.company}</b> <span className="pill warn">{c.reason}</span><br />{c.text}</p>
          ))}
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>How many emails can we safely send?</h3>
          <fieldset className="ro-fieldset row" disabled={!isOwner()}>
            <div style={{ flex: 1 }}><label>Mailboxes</label><input type="number" min="0" value={offer.mailboxes} onChange={(e) => num("mailboxes", e.target.value)} /></div>
            <div style={{ flex: 1 }}><label>Emails per mailbox per day</label><input type="number" min="0" value={offer.per_mailbox_per_day} onChange={(e) => num("per_mailbox_per_day", e.target.value)} /></div>
            <div style={{ flex: 1 }}><label>Leads to reach</label><input type="number" min="0" value={offer.target_leads} onChange={(e) => num("target_leads", e.target.value)} /></div>
          </fieldset>
          {isOwner() && <button className="btn ghost" style={{ marginTop: 10 }} onClick={saveOffer}>Recalculate</button>}
          <p><b>{g.capacity.daily}</b> a day, <b>{g.capacity.monthly}</b> a month.
            {g.capacity.days_needed && <> About <b>{g.capacity.days_needed}</b> working days to reach {offer.target_leads} leads.</>}</p>
          {g.capacity.warning && <p style={{ color: "var(--hot)" }}>{g.capacity.warning}</p>}
        </div>

        <div className="card">
          <h3 style={{ marginTop: 0 }}>Partners and communities ({partners.length})</h3>
          <p className="sub">Staffing agencies, HR groups, associations. Referrals so far: {g.referrals}.</p>
          {partners.map((p) => (
            <div className="row" key={p.id} style={{ marginBottom: 6 }}>
              <span style={{ flex: 1 }}><b>{p.name}</b> <small>{p.kind} · {p.contact}</small></span>
              {canWrite() ? <select style={{ width: "auto" }} value={p.status} onChange={(e) => api.partnerStatus(p.id, e.target.value).then(load).catch((err) => setMessage(err.message))}>
                {PARTNER_STATUSES.map((s) => <option key={s}>{s}</option>)}
              </select> : <span className="pill">{p.status}</span>}
            </div>
          ))}
          {canWrite() && <div className="row">
            <input style={{ flex: 2 }} placeholder="Name" value={newPartner.name} onChange={(e) => setNewPartner({ ...newPartner, name: e.target.value })} />
            <input style={{ flex: 2 }} placeholder="Contact" value={newPartner.contact} onChange={(e) => setNewPartner({ ...newPartner, contact: e.target.value })} />
            <button className="btn" disabled={!newPartner.name.trim()} onClick={addPartner}>Add</button>
          </div>}
        </div>
      </div>
    </>
  );
}
