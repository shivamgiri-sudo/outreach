import { useEffect, useState } from "react";
import { api, getWorkspace } from "../../api.js";
import { Button, Card, CardHeader, Field, Select } from "../../ui";
import { ConnectCard, LinkCard, linkName } from "./CallTrackerLinks.jsx";
import { ManualConnect, MappingEditor } from "./CallTrackerManual.jsx";
import { readConnectReturn } from "./dialhaloReturn.js";
import "./dialhalo.css";

// Integrations → DialHalo: one Outreach workspace can be linked to several DialHalo workspaces (owners: any workspace;
// a workspace admin: its own). Each link has its own lead sending; calls, outcomes and opt-outs come back by webhook.
// DialHalo's one-click connect sends the browser back to #integrations/dialhalo/connected?dh_state&dh_code (or dh_error).
// Integrations.jsx reads that on its first render (it tidies the hash on mount, before this page loads) and passes it
// as connectReturn; this page then finishes it through the signed-in API (app/calltracker_connect.py).
const handled = new Set();                // a return is finished once, even when React runs the effect twice
function takeReturn(ret) {
  if (!ret?.state || handled.has(ret.state)) return null;
  handled.add(ret.state);
  try { if (window.location.hash.includes("dh_state=")) window.history.replaceState(null, "", "#integrations?tab=calltracker"); } catch { /* old browser */ }
  return ret;
}

export default function CallTracker({ connectReturn } = {}) {
  const [workspaces, setWorkspaces] = useState([]);
  const [ws, setWs] = useState(getWorkspace() || "main");
  const [card, setCard] = useState(null);
  const [msg, setMsg] = useState(null);
  const [tested, setTested] = useState(null);   // { link, owners } from the last Test
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rulesFor, setRulesFor] = useState("");

  const load = () => api.calltracker(ws).then(setCard).catch((e) => setMsg({ ok: false, text: e.message }));
  useEffect(() => { api.workspaces().then((w) => setWorkspaces(Array.isArray(w) ? w : [])).catch(() => {}); }, []);
  useEffect(() => { setTested(null); load(); }, [ws]);
  useEffect(() => {
    const ret = takeReturn(connectReturn || readConnectReturn(window.location.hash));
    if (!ret) return;
    setBusy(true);
    setMsg({ ok: true, text: "Finishing the connection with DialHalo…" });
    api.finishCalltrackerConnect(ret)
      .then((out) => { if (out.card) { setWs(out.card.workspace); setCard(out.card); } setMsg({ ok: true, text: out.message }); })
      .catch((e) => setMsg({ ok: false, text: e.message }))
      .finally(() => setBusy(false));
  }, []);

  const run = async (fn, okText) => {
    setBusy(true);
    try {
      const out = await fn();
      if (out?.card) setCard(out.card); else if (out?.workspace) setCard(out);
      if (out?.owners) setTested({ link: out.link, owners: out.owners });
      setMsg(out?.message ? { ok: out.ok !== false, text: out.message } : okText ? { ok: true, text: okText } : null);
    } catch (e) {
      setMsg({ ok: false, text: e.message });
    } finally {
      setBusy(false);
    }
  };

  if (!card) return msg ? <div className="tip error">{msg.text}</div> : <p className="empty">Loading…</p>;
  const links = card.links || [];
  const q = card.queue;
  const rules = links.find((l) => l.id === rulesFor) || links.find((l) => l.primary) || links[0];
  const unmatched = (tested?.owners || []).filter((o) => !o.matched);
  const testedName = linkName(links.find((l) => l.id === tested?.link));
  return (
    <div className="dh-stack" data-testid="ct-card">
      <p className="sub">
        Link this workspace to one or more DialHalo workspaces. Leads go to a caller's phone dial queue in DialHalo; calls, outcomes and
        AI summaries come back to the lead's timeline; opt-outs work both ways. Recordings and transcripts stay in DialHalo.
      </p>
      {workspaces.length > 1 && (
        <Field label="Workspace">
          <Select value={ws} onChange={(e) => setWs(e.target.value)} data-testid="ct-workspace">
            {workspaces.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </Select>
        </Field>
      )}
      {msg && <div className={`tip ${msg.ok ? "" : "error"}`} role="status" data-testid="ct-msg">{msg.text}</div>}

      <ConnectCard ws={ws} card={card} busy={busy} run={run} />

      <Card data-testid="ct-links">
        <CardHeader title={links.length ? `Linked DialHalo workspaces (${links.length})` : "No DialHalo workspace linked yet"}
          description={links.length > 1 ? "Each lead goes to one DialHalo workspace: the one it was already sent to, else the primary (★), else the oldest. Erasing a person or a do-not-call reaches all of them." : undefined} />
        <div className="dh-links">
          {links.map((l) => <LinkCard key={l.id} ws={ws} link={l} card={card} busy={busy} run={run} onRefresh={load} many={links.length > 1} />)}
        </div>
      </Card>

      {links.length > 0 && (
        <Card data-testid="ct-queue">
          <b>Sending to DialHalo</b>
          <p className="sub">{q.pending} waiting · {q.failed} failed · {q.done} sent recently. Failed sends are retried with back-off, then wait here.</p>
          {q.failed_rows.length > 0 && (
            <>
              <ul>{q.failed_rows.map((j) => (
                <li key={j.id}>{j.kind === "match" ? `Outreach match (${j.numbers} numbers)` : `${j.company || j.lead_id} (${j.kind === "dnc" ? "do-not-call" : j.kind === "erase" ? "erase" : "lead"})`}
                  {links.length > 1 && j.link ? ` → ${linkName(links.find((l) => l.id === j.link))}` : ""}: {j.error}</li>
              ))}</ul>
              <Button disabled={busy} onClick={() => run(() => api.retryCalltracker(ws))} data-testid="ct-retry">Retry failed</Button>
            </>
          )}
        </Card>
      )}

      {unmatched.length > 0 && (
        <Card className="dh-warn" data-testid="ct-unmatched">
          <b>{unmatched.length} {unmatched.length === 1 ? "person has" : "people have"} no matching member in {testedName}</b>
          <p className="sub">Leads owned by these people are not put in their own dial queue; DialHalo shares them out by its distribution instead. Add each email below as a member in DialHalo (Members → Add), then press Test again.</p>
          <ul className="dh-list">
            {unmatched.map((o) => <li key={o.email}><b>{o.name}</b> · {o.email} <span className="sub-inline">({o.reason || "no DialHalo member has this email"})</span></li>)}
          </ul>
          <Button variant="secondary" data-testid="ct-copy-unmatched"
            onClick={() => navigator.clipboard?.writeText(unmatched.map((o) => o.email).join("\n")).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2500); }).catch(() => setMsg({ ok: false, text: "Copy was blocked; select the emails above and copy them." }))}>
            {copied ? "Copied" : "Copy the emails"}
          </Button>
        </Card>
      )}

      {rules && (rules.unmapped_outcomes || []).length > 0 && (
        <Card className="dh-warn" role="alert" data-testid="ct-unmapped">
          <b>Some DialHalo outcomes are not mapped</b>
          <p className="sub dh-tight">
            These DialHalo outcome names came in on real calls but have no row in the mapping below, so each call was counted as the plain
            result shown, and nothing else (stage, follow-up or opt-out) was done for it. Add a row with the same name to give them a proper meaning.
          </p>
          <ul className="dh-list">
            {rules.unmapped_outcomes.map((u) => <li key={u.name}><b>{u.name}</b>: {u.count} call{u.count === 1 ? "" : "s"}, counted as {String(u.counted_as || "").replace(/_/g, " ")}</li>)}
          </ul>
        </Card>
      )}

      {rules && links.length > 1 && (
        <Field label="Outcome rules for">
          <Select value={rules.id} onChange={(e) => setRulesFor(e.target.value)} data-testid="ct-rules-for">
            {links.map((l) => <option key={l.id} value={l.id}>{linkName(l)}</option>)}
          </Select>
        </Field>
      )}
      {rules && (
        <MappingEditor mapping={rules.mapping} outcomes={card.outcomes} stages={card.stages} busy={busy}
          title={links.length > 1 ? `What each outcome in ${linkName(rules)} does here` : undefined}
          onSave={(mapping) => run(() => api.saveCalltracker({ workspace: ws, link: rules.id, mapping }), "Saved.")}
          onReset={() => run(() => api.saveCalltracker({ workspace: ws, link: rules.id, reset_mapping: true }), "Saved.")} />
      )}

      <ManualConnect ws={ws} links={links} busy={busy} run={run} />
    </div>
  );
}
