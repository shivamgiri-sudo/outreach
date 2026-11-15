import { useEffect, useState } from "react";
import { api, canWrite, isOwner } from "../api.js";
import { ErrorState } from "../states.jsx";
import { getBrand } from "../brand.js";
import "../data-pages.css";
import { Badge, Button, Card, CardHeader, EmptyState, Field, Input, PageHeader, Select, Skeleton } from "../ui/index.js";

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
  if (!g || !offer) return <div className="dp-skel" aria-busy="true"><Skeleton height={36} width="40%" /><Skeleton height={220} /><Skeleton height={220} /></div>;
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
      <PageHeader title="Free experience"
        description="Let employers try it first. Give a real, capped taste, track whether they reach a result, and talk about paying only after." />
      {message && <div className="tip u-mb-12" role="status">{message}</div>}

      <div className="dp-two">
        <Card>
          <CardHeader title="The free offer" />
          <fieldset className="ro-fieldset dp-fields" disabled={!isOwner()}>
            {OFFER_FIELDS.map(([key, label]) => (
              <Field key={key} label={label}><Input type="number" min="0" value={offer[key]} onChange={(e) => num(key, e.target.value)} /></Field>
            ))}
            <Field label="Where “Use this draft” sends them"><Input value={offer.claim_url} onChange={(e) => setOffer({ ...offer, claim_url: e.target.value })} /></Field>
          </fieldset>
          {isOwner() && <Button className="u-mt-12" onClick={saveOffer}>Save offer</Button>}
          <p className="sub u-mt-12 u-mb-0">
            These numbers appear in emails and demo pages. The limits themselves must also be set inside {getBrand().public.product_name}, or free use has no ceiling.
          </p>
        </Card>

        <Card>
          <CardHeader title="Did they reach a result?" />
          {Object.entries(STEP_LABELS).map(([step, label]) => (
            <div className="funnel-row" key={step}>
              <span>{label}</span>
              <div className="bar" style={{ width: `${(g.activation_funnel[step] / max) * 100}%` }} />
              <b>{g.activation_funnel[step]}</b>
            </div>
          ))}
        </Card>

        <Card>
          <CardHeader title="Do today" />
          {g.nudges.length === 0 && <EmptyState icon="check" title="Nobody is waiting on a nudge">When an employer stalls, a suggested message appears here.</EmptyState>}
          <ul className="dp-list">{g.nudges.map((n) => <li key={n.lead_id}><b>{n.company}</b> — {n.text}</li>)}</ul>
        </Card>

        <Card>
          <CardHeader title="Time for an honest heads-up" />
          {g.conversion_due.length === 0 && <EmptyState icon="gift" title="No one is near the end of the free allowance">Employers close to their free limit are listed here with what to say.</EmptyState>}
          <ul className="dp-list">{g.conversion_due.map((c) => (
            <li key={c.lead_id}><b>{c.company}</b> <Badge tone="warning">{c.reason}</Badge><br />{c.text}</li>
          ))}</ul>
        </Card>

        <Card>
          <CardHeader title="How many emails can we safely send?" />
          <fieldset className="ro-fieldset dp-inline-3" disabled={!isOwner()}>
            <Field label="Mailboxes"><Input type="number" min="0" value={offer.mailboxes} onChange={(e) => num("mailboxes", e.target.value)} /></Field>
            <Field label="Emails per mailbox per day"><Input type="number" min="0" value={offer.per_mailbox_per_day} onChange={(e) => num("per_mailbox_per_day", e.target.value)} /></Field>
            <Field label="Leads to reach"><Input type="number" min="0" value={offer.target_leads} onChange={(e) => num("target_leads", e.target.value)} /></Field>
          </fieldset>
          {isOwner() && <Button variant="secondary" className="u-mt-12" onClick={saveOffer}>Recalculate</Button>}
          <p><b>{g.capacity.daily}</b> a day, <b>{g.capacity.monthly}</b> a month.
            {g.capacity.days_needed && <> About <b>{g.capacity.days_needed}</b> working days to reach {offer.target_leads} leads.</>}</p>
          {g.capacity.warning && <p className="dp-warn">{g.capacity.warning}</p>}
        </Card>

        <Card>
          <CardHeader title={`Partners and communities (${partners.length})`} description={`Staffing agencies, HR groups, associations. Referrals so far: ${g.referrals}.`} />
          {partners.length === 0 && <EmptyState icon="users" title="No partners yet">{canWrite() ? "Add the first agency, HR group or association below." : "Partners your team adds will show here."}</EmptyState>}
          {partners.map((p) => (
            <div className="dp-partner" key={p.id}>
              <span className="u-flex1"><b>{p.name}</b> <small>{p.kind} · {p.contact}</small></span>
              {canWrite() ? <Select value={p.status} onChange={(e) => api.partnerStatus(p.id, e.target.value).then(load).catch((err) => setMessage(err.message))}>
                {PARTNER_STATUSES.map((s) => <option key={s}>{s}</option>)}
              </Select> : <Badge>{p.status}</Badge>}
            </div>
          ))}
          {canWrite() && <div className="dp-add-partner">
            <Input placeholder="Name" value={newPartner.name} onChange={(e) => setNewPartner({ ...newPartner, name: e.target.value })} />
            <Input placeholder="Contact" value={newPartner.contact} onChange={(e) => setNewPartner({ ...newPartner, contact: e.target.value })} />
            <Button disabled={!newPartner.name.trim()} onClick={addPartner}>Add</Button>
          </div>}
        </Card>
      </div>
    </>
  );
}
