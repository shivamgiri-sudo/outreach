import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { istLabel } from "../time.js";
import { Badge, Button, DataTable, Field, Input } from "../ui/index.js";

// Two parts of the Deliverability card: the bounce meter per sending mailbox (green / amber / red, Resume for a paused
// one) and the owner's share of each mailbox's daily sends for unconfirmed addresses (app/sender_health.py,
// app/send_volume.py). Data comes with GET /api/deliverability.
const pct = (x) => `${(x * 100).toFixed(1)}%`;
const LEVEL = { green: ["success", "Healthy"], amber: ["warning", "Watch"], red: ["danger", "Too many bounces"], none: ["muted", "No sends"] };

export function SenderMeter({ rows, limits, onChanged }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const owner = isOwner();
  const resume = async (sender) => {
    setBusy(sender); setError("");
    try { await api.resumeSender(sender); await onChanged(); } catch (e) { setError(e.message); }
    setBusy("");
  };
  return (
    <div className="mt-4" data-testid="sender-meter">
      <b>By sending mailbox</b>
      <p className="sub m0">
        Hard bounces for each address you send from. Amber from {pct(limits.warn_rate)}; a mailbox is paused above {pct(limits.pause_rate)} once it
        sent {limits.min_sends} emails in 7 days (with one mailbox, the whole account pauses above 2% as before). A paused mailbox waits for an owner to resume it.
      </p>
      {error && <div className="tip error" role="alert">{error}</div>}
      {rows.length ? (
        <DataTable label="Bounces by sending mailbox">
          <thead><tr><th>Mailbox</th><th>Last 7 days</th><th>Last 30 days</th><th>Status</th></tr></thead>
          <tbody>{rows.map((r) => {
            const [tone, word] = LEVEL[r.level] || LEVEL.none;
            return (
              <tr key={r.sender} data-testid="sender-row" data-level={r.level}>
                <td><span className="u-breakall">{r.sender}</span>{r.current && <small className="sub"> (in use)</small>}</td>
                <td>{r.sent_7} sent · {r.bounced_7} bounced · <b>{r.sent_7 ? pct(r.rate_7) : "-"}</b></td>
                <td>{r.sent_30} sent · {r.bounced_30} bounced · <b>{r.sent_30 ? pct(r.rate_30) : "-"}</b></td>
                <td>
                  <Badge tone={tone}>{r.paused ? (r.paused.scope === "all" ? "Paused (all sending)" : "Paused") : word}</Badge>
                  {r.paused && <small className="sub u-block" title={r.paused.reason}>Since {istLabel(r.paused.at)}</small>}
                  {r.paused && owner && <Button size="sm" variant="secondary" disabled={busy === r.sender} onClick={() => resume(r.sender)}>Resume</Button>}
                </td>
              </tr>
            );
          })}</tbody>
        </DataTable>
      ) : <p className="sub m0">No emails sent in the last 30 days.</p>}
    </div>
  );
}

export function UnconfirmedShare({ volume, onChanged }) {
  const [value, setValue] = useState(String(volume.domain_ready_share));
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { setValue(String(volume.domain_ready_share)); }, [volume.domain_ready_share]);
  const n = Number(value);
  const valid = value !== "" && Number.isInteger(n) && n >= 0 && n <= 100;
  const save = async () => {
    setNote(""); setError("");
    try { const r = await api.setUnconfirmedShare(n); setNote(`Saved: ${r.domain_ready_share}%.`); await onChanged(); } catch (e) { setError(e.message); }
  };
  const meaning = volume.domain_ready_share === 0 ? "Addresses whose mailbox is not confirmed are never emailed by sequences."
    : volume.domain_ready_share >= 100 ? "No limit: unconfirmed addresses are emailed like confirmed ones."
    : `Up to ${volume.domain_ready_share}% of each mailbox's daily emails may go to addresses whose mailbox is not confirmed (catch-all, unknown, never checked). The rest wait for the next day; nothing is dropped. Confirmed addresses are never held.`;
  return (
    <div className="mt-4" data-testid="unconfirmed-share">
      <b>Unconfirmed addresses at a lower volume</b>
      <p className="sub m0">{meaning}</p>
      {isOwner() && (
        <div className="row mt-3">
          <Field label="Share of daily sends (%)" help={`0 = never, 100 = no limit (the default). 20 is a good setting once most addresses have a real mailbox check.`}>
            <Input type="number" min={0} max={100} step={1} inputMode="numeric" value={value} onChange={(e) => setValue(e.target.value)} aria-label="Share of daily sends for unconfirmed addresses" />
          </Field>
          <Button disabled={!valid || n === volume.domain_ready_share} onClick={save}>Save</Button>
        </div>
      )}
      {note && <p className="sub" role="status">{note}</p>}
      {error && <div className="tip error" role="alert">{error}</div>}
    </div>
  );
}
