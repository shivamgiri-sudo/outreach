// The lead drawer's Product / Free trial tab.
import { useState } from "react";
import { api, canWrite } from "../api.js";
import { copyText } from "../clipboard.js";
import { InlineForm } from "./LeadDrawerParts.jsx";
import { Card, CardHeader, Button } from "../ui/index.js";

export default function LeadProductTab({ lead, act, setError, load }) {
  const id_ = lead.id;
  const [demoUrl, setDemoUrl] = useState("");
  const [form, setForm] = useState("");
  return (
    <Card flat className="dsec">
      <CardHeader title="Free experience" />
      {lead.activation ? (
        <>
          <p className="sub">
            Step: <b>{lead.activation.step}</b> · candidates screened {lead.activation.usage.candidates}, interviews booked {lead.activation.usage.bookings}
            {lead.bonus_candidates ? ` · +${lead.bonus_candidates} referral bonus` : ""}
          </p>
          {lead.nudge && <div className="tip">{lead.nudge.text}</div>}
        </>
      ) : <p className="sub">No demo link yet. Create one and send it, so they can try the product.</p>}
      {canWrite() && <>
        <div className="row u-mt-8">
          <Button variant="secondary" onClick={async () => {
            try {
              const l = await api.demoLink(id_);
              const ok = await copyText(l.url);
              setDemoUrl(ok ? l.url : `${l.url} (copy it from here)`); load();
            } catch (e) { setError(e.message); }
          }}>{lead.demo_token ? "Copy demo link" : "Create demo link"}</Button>
          <Button variant="secondary" onClick={() => setForm("pilot")}>Offer free pilot on their applicants</Button>
          <Button variant="secondary" onClick={() => setForm("referral")}>Log a referral</Button>
        </div>
        {form === "pilot" && (
          <InlineForm submitLabel="Log pilot" onCancel={() => setForm("")}
            fields={[{ key: "applicants", label: "How many applicants do they already have?", type: "number", initial: "100", required: true }]}
            onSubmit={(v) => act(async () => { const r = await api.pilot(id_, Number(v.applicants)); setForm(""); return r; })} />
        )}
        {form === "referral" && (
          <InlineForm submitLabel="Save referral" onCancel={() => setForm("")}
            fields={[{ key: "company", label: "Which company do they recommend?", required: true },
                     { key: "email", label: "Contact email (optional)", type: "email" }]}
            onSubmit={(v) => act(async () => { await api.referral(id_, v); setForm(""); return api.lead(id_); })} />
        )}
        {demoUrl && <p className="sub">Copied: {demoUrl}</p>}
        <label>Record what they did in the product
          <select defaultValue="" onChange={(e) => {
            if (!e.target.value) return;
            const [event, count] = e.target.value.split(":");
            act(async () => { await api.activationEvent(id_, event, Number(count)); return api.lead(id_); });
            e.target.value = "";
          }}>
            <option value="">Choose…</option>
            <option value="role_drafted:1">Drafted a role</option>
            <option value="role_published:1">Published the role</option>
            <option value="candidate_screened:1">1 candidate screened</option>
            <option value="candidate_screened:5">5 candidates screened</option>
            <option value="interview_booked:1">1 interview booked</option>
            <option value="converted:1">Became a paying customer</option>
          </select>
        </label>
      </>}
    </Card>
  );
}
