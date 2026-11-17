import { useState } from "react";
import { api, isOwner } from "../../api.js";
import { istLabel } from "../../time.js";
import { Badge, Button, Card, CardHeader, Field, Input, Select } from "../../ui";
import CallTrackerMatch from "./CallTrackerMatch.jsx";
import { CopyRow } from "./Fields.jsx";

// Integrations → DialHalo: the one-click Connect form and one card per linked DialHalo workspace.
export const LEAD_SYNC = {
  off: "Off: send no leads",
  call_tasks: "Leads that get a call task",
  owners_members: "Leads whose owner is a member there",
  all_with_phone: "Every lead with a phone",
};

export const linkName = (l) => l?.dh_workspace?.name || (l?.public_url || l?.base_url || "DialHalo").replace(/^https?:\/\//, "");

export function StatusLine({ title, row, testid }) {
  if (!row) return <span className="dh-status">{title}: never</span>;
  return (
    <span className={`dh-status ${row.ok ? "ok" : "bad"}`} data-testid={testid}>
      {row.ok ? "✓" : "✗"} {title} {istLabel(row.at)}{row.detail ? `: ${row.detail}` : ""}
    </span>
  );
}

export function ConnectCard({ ws, card, busy, run }) {
  const [url, setUrl] = useState(card.connect?.dialhalo_url || "");
  const start = () => run(async () => {
    const out = await api.startCalltrackerConnect(ws, url);
    window.location.assign(out.url);                 // same tab: this tab's sign-in comes back with the browser
    return { message: "Opening DialHalo…" };
  });
  return (
    <Card data-testid="ct-connect">
      <CardHeader title="Connect a DialHalo workspace"
        description="Opens DialHalo, where you sign in and pick the workspace (for example MAS Callnet or BMI). DialHalo then sends you back here and the link is ready: no keys to copy." />
      <div className="dh-connect">
        <Field label="DialHalo address">
          <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://calls.example.com" data-testid="ct-connect-url" />
        </Field>
        <Button disabled={busy || !url.trim()} onClick={start} data-testid="ct-connect-start">Connect a DialHalo workspace</Button>
      </div>
      <p className="sub dh-tight">You can link more than one DialHalo workspace. The link expires if you do not finish in DialHalo within 10 minutes.</p>
    </Card>
  );
}

function People({ pairing }) {
  if (!pairing?.checked) return <p className="dh-meta">People matched by email: press Test to check.</p>;
  return (
    <div data-testid="ct-pairing">
      <p className="dh-meta">People matched by email: <b>{pairing.matched} of {pairing.total}</b> Outreach people are DialHalo members here ({pairing.members} member{pairing.members === 1 ? "" : "s"}).</p>
      <ul className="dh-people">
        {pairing.people.slice(0, 40).map((p) => (
          <li key={p.name} className={p.matched ? "yes" : "no"}>{p.matched ? "✓" : "–"} {p.name}{p.matched && p.member && p.member !== p.name ? ` (${p.member})` : ""}</li>
        ))}
      </ul>
    </div>
  );
}

export function LinkCard({ ws, link, card, busy, run, onRefresh, many }) {
  const [matchOpen, setMatchOpen] = useState(false);
  const status = link.auth_failed ? ["danger", "Not authorised — reconnect"] : !link.api_key_set ? ["warning", "Key missing"]
    : link.enabled ? ["success", "Connected"] : ["warning", "Paused"];
  const save = (changes, okText) => run(() => api.saveCalltracker({ workspace: ws, link: link.id, ...changes }), okText);
  return (
    <div className={`dh-link${link.primary ? " is-primary" : ""}`} data-testid="ct-link">
      <div className="dh-link-head">
        <button type="button" className="dh-star" aria-pressed={!!link.primary} disabled={busy || link.primary}
          title={link.primary ? "Primary: wins when several links could take a lead" : "Make primary"}
          aria-label={link.primary ? `${linkName(link)} is the primary link` : `Make ${linkName(link)} the primary link`}
          onClick={() => save({ primary: true }, `${linkName(link)} is now the primary link.`)} data-testid="ct-primary">★</button>
        <span className="dh-link-name">{linkName(link)}</span>
        <Badge tone={status[0]} data-testid="ct-status">{status[1]}</Badge>
        <Badge tone="muted">{link.via === "connect" ? "One-click" : "Manual"}</Badge>
      </div>
      <p className="dh-meta">{link.public_url || link.base_url}{link.dh_workspace?.slug ? ` · workspace ${link.dh_workspace.slug}` : ""}</p>
      {link.auth_failed && (
        <p className="dh-status bad" role="alert" data-testid="ct-auth-failed">
          DialHalo refused this link's key {istLabel(link.auth_failed.at)} (it may have been revoked there). Press Connect a DialHalo workspace and pick the same workspace: the new link replaces this one.
        </p>
      )}
      <StatusLine title="Last call event" row={link.last_event} testid="ct-last-call-event" />
      <People pairing={link.pairing} />
      <div className="dh-row">
        <Field label="Send leads to this DialHalo workspace">
          <Select value={link.lead_sync} disabled={busy} data-testid="ct-lead-sync"
            onChange={(e) => save({ lead_sync: e.target.value }, "Lead sending saved.")}>
            {Object.entries(LEAD_SYNC).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Button variant="secondary" disabled={busy} onClick={() => run(() => api.testCalltracker(ws, link.id))} data-testid="ct-test">Test</Button>
        <Button variant="secondary" disabled={busy} aria-expanded={matchOpen} onClick={() => setMatchOpen(!matchOpen)} data-testid="ct-match-open">Match past calls</Button>
        <Button variant="danger" disabled={busy} data-testid="ct-disconnect"
          onClick={() => window.confirm(`Disconnect ${linkName(link)}? DialHalo is asked to turn the key off, and unsent pushes for it are removed.`)
            && run(() => api.deleteCalltracker(ws, link.id))}>Disconnect</Button>
      </div>
      {many && link.lead_sync !== "off" && !link.primary && <p className="dh-meta">When another link could also take a lead, the primary (★) link gets it.</p>}
      {(link.via !== "connect" || isOwner()) && (
        <details className="dh-details">
          <summary>Webhook receiver</summary>
          <CopyRow label="Receiver address for the DialHalo webhook" value={link.receiver?.public} testid="ct-receiver" />
          {isOwner() && <CopyRow label="Same server (DialHalo on this machine; needs WEBHOOK_ALLOW_PRIVATE_HOSTS there)" value={link.receiver?.internal} />}
          <p className="sub">Events to tick in DialHalo: {card.events.join(", ")}.</p>
          <StatusLine title="Last test" row={link.last_test} />{" "}
          <StatusLine title="Last push" row={link.last_push} />
        </details>
      )}
      {matchOpen && (
        <CallTrackerMatch ws={ws} link={link.id} match={{ ...card.match, backfill: link.backfill }} lastMatch={link.last_match}
          busy={busy} run={run} onRefresh={onRefresh} />
      )}
    </div>
  );
}
