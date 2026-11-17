import { useState } from "react";
import { canWrite } from "../api.js";
import { LICENCE_SHORT, register, rupees } from "../registerApi.js";
import { istLabel } from "../time.js";
import { Badge, Button } from "../ui/index.js";
import "../register.css";

const CONFIDENCE = { high: ["success", "Sure match"], confirmed: ["success", "Confirmed by a person"], medium: ["warning", "Needs a check"],
  low: ["neutral", "No sure match"], none: ["neutral", "Not found"], rejected: ["neutral", "Rejected"] };
const isActive = (s) => /^active/i.test(s || "") && !/strike|dissolv|liquidat/i.test(s || "");

function Fact({ label, children, wide }) {
  if (children === undefined || children === null || children === "") return null;
  return <div className={wide ? "reg-wide" : undefined}><small>{label}</small>{children}</div>;
}

function Facts({ r }) {
  return (
    <div className="reg-grid">
      <Fact label="CIN">{r.cin}</Fact>
      <Fact label="Status">{r.status && <Badge tone={isActive(r.status) ? "success" : "warning"}>{r.status}</Badge>}</Fact>
      <Fact label="Incorporated">{r.incorporated}</Fact>
      <Fact label="Class / category">{[r.class, r.category, r.sub_category].filter(Boolean).join(" · ")}</Fact>
      <Fact label="Paid-up capital">{rupees(r.paid_up)}</Fact>
      <Fact label="Authorised capital">{rupees(r.authorised)}</Fact>
      <Fact label="Registered state / RoC">{[r.state, r.roc].filter(Boolean).join(" · ")}</Fact>
      <Fact label="Latest annual return">{r.last_return}</Fact>
      <Fact label="Principal business activity" wide>{[r.activity, r.nic && `NIC ${r.nic}`].filter(Boolean).join(" · ")}</Fact>
      <Fact label="Registered office address" wide>{r.address}</Fact>
      <Fact label="Registered email (from the register file)" wide>{r.email}</Fact>
    </div>
  );
}

function Candidate({ r, onPick, busy, label = "This one" }) {
  return (
    <div className="reg-cand">
      <span className="reg-cand-text">
        <b>{r.name}</b><br />
        <small className="sub">{[r.cin, r.state, r.status, r.incorporated && `since ${r.incorporated.slice(0, 4)}`].filter(Boolean).join(" · ")}</small>
      </span>
      {canWrite() && <Button size="sm" variant="secondary" disabled={busy} onClick={() => onPick(r.cin)}>{label}</Button>}
    </div>
  );
}

// The lead's company in the Indian company register (MCA Company Master Data): the facts when matched, else the
// "Is this the company?" check. Only empty lead fields are ever filled from it.
export default function CompanyRegisterPanel({ lead, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const reg = lead.registry;
  const match = !reg ? lead.registry_match : null;
  const run = async (fn, done) => {
    setBusy(true); setMsg("");
    try { const r = await fn(); setMsg(done ? done(r) : ""); onChanged?.(); }
    catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const find = () => run(() => register.match(lead.id), (r) => (r.attached ? (r.filled.length ? `Matched. Filled: ${r.filled.join(", ").replace(/_/g, " ")}.` : "Matched.")
    : r.confidence === "medium" ? "Found a likely match: please check it below." : "No sure match in the register."));
  const confirm = (cin) => run(() => register.confirm(lead.id, cin), () => "Saved as this lead's company.");
  const reject = (cin) => run(() => register.reject(lead.id, cin), () => "Noted: that company will not be suggested again for this lead.");
  const [tone, label] = CONFIDENCE[reg?.confidence || match?.confidence] || [];
  const others = match ? (match.alternatives || []) : [];
  return (
    <section className="card u-m-12-0" aria-label="Company register" data-testid="company-register">
      <div className="row u-between u-wrap">
        <b>Company register {label && <Badge tone={tone} className="u-ml-8">{label}</Badge>}</b>
        {canWrite() && <Button variant="secondary" size="sm" onClick={find} disabled={busy}>{busy ? "Looking…" : reg || match ? "Check again" : "Find in company register"}</Button>}
      </div>
      {!reg && !match && <p className="sub">Looks the company up in the Indian company register (Ministry of Corporate Affairs master data) kept on this server: CIN, status, incorporation date, capital, registered address and business activity.</p>}
      {msg && <p className="sub" role="status">{msg}</p>}
      {reg && (
        <>
          <p className="u-m-8-0-0"><b>{reg.name}</b></p>
          <Facts r={reg} />
          <p className="sub u-m0">Directors: not in the free register data.</p>
          <small className="sub">Matched {istLabel(reg.matched_at)}{reg.matched_by ? ` by ${reg.matched_by}` : ""}.
            {reg.filled?.length > 0 ? ` Filled empty fields: ${reg.filled.join(", ").replace(/_/g, " ")}.` : " No lead field was changed."}</small>
          {canWrite() && <div className="reg-actions"><Button size="sm" variant="ghost" disabled={busy} onClick={() => reject(reg.cin)}>Not this company</Button></div>}
        </>
      )}
      {match && match.best && match.confidence === "medium" && (
        <div className="reg-ask" role="group" aria-label="Is this the company?">
          <b>Is this the company?</b>
          <p className="u-m-4-0"><b>{match.best.name}</b></p>
          <Facts r={match.best} />
          {canWrite() && (
            <div className="reg-actions">
              <Button size="sm" disabled={busy} onClick={() => confirm(match.best.cin)}>Yes, this is it</Button>
              <Button size="sm" variant="secondary" disabled={busy} onClick={() => reject(match.best.cin)}>No</Button>
            </div>
          )}
        </div>
      )}
      {match && match.best && match.confidence !== "medium" && <Candidate r={match.best} onPick={confirm} busy={busy} />}
      {match && others.length > 0 && (
        <details className="u-mt-8" open={match.confidence !== "medium"}>
          <summary>{match.confidence === "medium" ? "Other possible companies" : "Possible companies (none is a sure match)"}</summary>
          {others.map((r) => <Candidate key={r.cin} r={r} onPick={confirm} busy={busy} />)}
        </details>
      )}
      {match && !match.best && others.length === 0 && <p className="sub">No company with a similar name is in the register. Check the spelling of the company name, or the register may not include its state yet.</p>}
      {(reg || match) && <p className="reg-note">{LICENCE_SHORT} The register has no directors or phone numbers.</p>}
    </section>
  );
}
