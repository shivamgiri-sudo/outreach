import { useEffect, useState } from "react";
import { api, getRole } from "../../api.js";
import { Button, Field, Input, Select } from "../../ui";
import { linkName } from "./CallTrackerLinks.jsx";

// Integrations → DialHalo: "Advanced: connect manually" (an API key and webhook secret made in DialHalo by hand; also for
// an older DialHalo without one-click connect) and the outcome mapping of one link.
const LABEL = (s) => String(s || "").replace(/_/g, " ");
const EMPTY = { base_url: "", public_url: "", api_key: "", webhook_secret: "", enabled: true };

export function ManualConnect({ ws, links, busy, run }) {
  const [target, setTarget] = useState(links[0]?.id || "new");
  const link = links.find((l) => l.id === target);
  const [form, setForm] = useState(EMPTY);
  useEffect(() => { if (!links.some((l) => l.id === target)) setTarget(links[0]?.id || "new"); }, [links]);
  useEffect(() => {
    setForm(link ? { base_url: link.base_url || "", public_url: link.public_url || "", api_key: "", webhook_secret: "", enabled: link.enabled !== false } : EMPTY);
  }, [target, link?.updated_at]);
  const save = () => run(() => api.saveCalltracker({ workspace: ws, ...(link ? { link: link.id } : { new: true }), ...form }), "Saved.");
  if (!["owner", "admin"].includes(getRole())) return null;   // the server refuses callers and viewers too
  return (
    <details className="card dh-details" data-testid="ct-manual">
      <summary>Advanced: connect manually</summary>
      <p className="sub">For a DialHalo that has no one-click connect yet, or to change a link's addresses or keys by hand.</p>
      <ol className="sub">
        <li>In DialHalo: Settings → Integrations → API keys → "Fill in the MCN Outreach preset" → Create key, and paste it below.</li>
        <li>Save, then add a webhook in DialHalo with the receiver address shown on the link (Webhook receiver) and the MCN Outreach events; paste its signing secret here.</li>
        <li>Press Test on the link. Callers are matched by email; anyone else's leads are shared out by DialHalo's distribution.</li>
      </ol>
      {links.length > 0 && (
        <Field label="Change">
          <Select value={target} onChange={(e) => setTarget(e.target.value)} data-testid="ct-manual-target">
            {links.map((l) => <option key={l.id} value={l.id}>{linkName(l)}</option>)}
            <option value="new">A new DialHalo workspace</option>
          </Select>
        </Field>
      )}
      <div className="dh-grid">
        <Field label="DialHalo address (server to server)">
          <Input value={form.base_url} placeholder="https://calls.example.com or http://127.0.0.1:8081" data-testid="ct-base-url"
            onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
        </Field>
        <Field label="Web address for links (https)">
          <Input value={form.public_url} placeholder="https://calls.example.com" data-testid="ct-public-url"
            onChange={(e) => setForm({ ...form, public_url: e.target.value })} />
        </Field>
        <Field label="API key" help={link?.api_key_set ? `Saved (${link.api_key_hint}); leave empty to keep.` : undefined}>
          <Input type="password" autoComplete="off" value={form.api_key} placeholder="ctk_…" data-testid="ct-api-key"
            onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
        </Field>
        <Field label="Webhook signing secret" help={link?.webhook_secret_set ? "Saved; leave empty to keep." : undefined}>
          <Input type="password" autoComplete="off" value={form.webhook_secret} placeholder="whsec_…" data-testid="ct-secret"
            onChange={(e) => setForm({ ...form, webhook_secret: e.target.value })} />
        </Field>
      </div>
      <label className="row"><input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} /> Send leads and accept calls</label>
      <div className="row">
        <Button disabled={busy} onClick={save} data-testid="ct-save">{link ? "Save" : "Connect"}</Button>
      </div>
    </details>
  );
}

export function MappingEditor({ mapping, outcomes, stages, onSave, onReset, busy, title }) {
  const [rows, setRows] = useState(() => Object.entries(mapping).map(([name, r]) => ({ name, ...r })));
  useEffect(() => setRows(Object.entries(mapping).map(([name, r]) => ({ name, ...r }))), [mapping]);
  const set = (i, patch) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const save = () => onSave(Object.fromEntries(rows.filter((r) => r.name.trim()).map(({ name, ...r }) => [name.trim().toLowerCase(), r])));
  return (
    <div className="card" data-testid="ct-mapping">
      <b>{title || "What each DialHalo outcome does here"}</b>
      <p className="sub">Outcome names as your callers pick them in DialHalo. Stages only move forward; Unsubscribed is never undone.
        <b> Wrong number</b> marks that phone as wrong: Outreach stops calling, WhatsApping and texting that number, but the person, their email and their email sequence are left alone (add the number again, or correct it, to use it). Only <b>Do not call</b> puts the person on the do-not-contact list. A saved mapping of your own is kept as it is; Reset to defaults uses these rules.</p>
      <div className="table-wrap">
        <table>
          <thead><tr><th>DialHalo outcome</th><th>Logged as</th><th>Move stage to</th><th>Stop emails</th><th>Do not contact</th><th /></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input aria-label="DialHalo outcome name" value={r.name} onChange={(e) => set(i, { name: e.target.value })} /></td>
                <td>
                  <select aria-label={`Logged as for ${r.name}`} value={r.outcome || ""} onChange={(e) => set(i, { outcome: e.target.value })}>
                    <option value="">connected / no answer</option>
                    {outcomes.map((o) => <option key={o} value={o}>{LABEL(o)}</option>)}
                  </select>
                </td>
                <td>
                  <select aria-label={`Stage for ${r.name}`} value={r.stage || ""} onChange={(e) => set(i, { stage: e.target.value })}>
                    <option value="">leave as it is</option>
                    {stages.map((s) => <option key={s} value={s}>{LABEL(s)}</option>)}
                  </select>
                </td>
                <td><input type="checkbox" aria-label={`Stop emails for ${r.name}`} checked={!!r.stop_sequence} onChange={(e) => set(i, { stop_sequence: e.target.checked })} /></td>
                <td><input type="checkbox" aria-label={`Do not contact for ${r.name}`} checked={!!r.opt_out} onChange={(e) => set(i, { opt_out: e.target.checked })} /></td>
                <td><button type="button" className="btn ghost danger" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="row">
        <button type="button" className="btn ghost" onClick={() => setRows([...rows, { name: "", outcome: "", stage: "", stop_sequence: false, opt_out: false }])}>+ Add outcome</button>
        <button type="button" className="btn" disabled={busy} onClick={save} data-testid="ct-save-mapping">Save mapping</button>
        <button type="button" className="btn ghost" disabled={busy} onClick={onReset}>Use the defaults</button>
      </div>
    </div>
  );
}
