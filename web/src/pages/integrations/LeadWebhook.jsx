// Integrations → Lead webhook: push new and changed leads to Zapier, Make, a CRM or a Google Sheet as they happen.
// One setting per workspace, owner only (app/routes_lead_webhook.py). The secret is never shown again once saved.
import { useEffect, useState } from "react";
import { apiRequest as request } from "../../api.js";
import "./leadWebhook.css";
import { istLabel } from "../../time.js";
import { Badge, Button, Card, CardHeader, DataTable, EmptyState, Field, Input, Segmented, Switch, useToast } from "../../ui/index.js";

const TONE = { done: "success", failed: "danger", retrying: "warning" };

export default function LeadWebhook() {
  const toast = useToast();
  const [card, setCard] = useState(null);
  const [form, setForm] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const load = () => request("/api/lead-webhook").then((c) => {
    setCard(c);
    setForm({ url: c.hook.url, secret: "", mode: c.hook.mode, events: c.hook.events, fields: c.hook.fields, enabled: c.hook.enabled });
  }).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  if (error) return <p className="dp-warn" role="alert">{error}</p>;
  if (!card || !form) return <p className="sub">Loading…</p>;

  const toggle = (key, id) => setForm({ ...form, [key]: form[key].includes(id) ? form[key].filter((x) => x !== id) : [...form[key], id] });
  const run = async (what, fn) => {
    setBusy(what);
    try { await fn(); } catch (e) { toast(e.message, "err"); }
    setBusy("");
  };
  const save = () => run("save", async () => {
    const body = { ...form, secret: form.secret ? form.secret : undefined };
    const c = await request("/api/lead-webhook", { method: "PUT", body });
    setCard(c); setForm({ ...form, secret: "" }); toast("Lead webhook saved.", "ok");
  });
  const clearSecret = () => run("save", async () => { setCard(await request("/api/lead-webhook", { method: "PUT", body: { secret: "" } })); toast("Secret removed.", "ok"); });
  const test = () => run("test", async () => {
    const r = await request("/api/lead-webhook/test", { method: "POST", body: {} });
    toast(r.ok ? `Test sent: ${r.result}` : `Test failed: ${r.result}`, r.ok ? "ok" : "err"); load();
  });
  const retry = () => run("retry", async () => { const r = await request("/api/lead-webhook/retry", { method: "POST", body: {} }); toast(`${r.requeued} delivery(ies) queued again.`, "ok"); load(); });
  const remove = () => run("remove", async () => {
    if (!window.confirm("Remove the lead webhook for this workspace? Waiting deliveries are dropped.")) return;
    await request("/api/lead-webhook", { method: "DELETE" }); load();
  });
  const sheets = form.mode === "sheets";
  const q = card.queue;

  return (
    <div className="lw-stack" data-testid="lead-webhook">
      <Card>
        <CardHeader title="Lead webhook" description="Send each new or changed lead to another system the moment it happens: Zapier, Make, your CRM, or a Google Sheet. Works for this workspace only."
          actions={<Badge tone={card.hook.enabled ? "success" : "muted"}>{card.hook.enabled ? "On" : "Off"}</Badge>} />
        <div className="lw-form">
          <Segmented label="Send to" value={form.mode} onChange={(mode) => setForm({ ...form, mode })}
            items={[{ id: "webhook", label: "Webhook (JSON)" }, { id: "sheets", label: "Google Sheets" }]} />
          <Field label={sheets ? "Apps Script web app address" : "Webhook address"}
            help={sheets ? "From Apps Script: Deploy → New deployment → Web app → Anyone. Ends in /exec." : "https only. Private or internal addresses are refused."}>
            <Input type="url" inputMode="url" value={form.url} placeholder={sheets ? "https://script.google.com/macros/s/…/exec" : "https://hooks.zapier.com/hooks/catch/…"}
              onChange={(e) => setForm({ ...form, url: e.target.value })} data-testid="lw-url" />
          </Field>
          {!sheets && (
            <Field label="Secret (optional)" help={card.hook.secret_set ? "A secret is saved. Type a new one to replace it." : "At least 12 characters. Each request then carries X-Outreach-Signature: sha256=<HMAC of the body>."}>
              <Input type="password" autoComplete="new-password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} data-testid="lw-secret" />
            </Field>
          )}
          {!sheets && card.hook.secret_set && <Button size="sm" variant="ghost" onClick={clearSecret} disabled={!!busy}>Remove the secret</Button>}
          <fieldset className="lw-set">
            <legend>When to send</legend>
            {card.events.map((e) => (
              <label key={e.id} className="check-row"><input type="checkbox" checked={form.events.includes(e.id)} onChange={() => toggle("events", e.id)} /> <span><b>{e.id}</b> — {e.text}</span></label>
            ))}
          </fieldset>
          {form.events.includes("lead.updated") && (
            <fieldset className="lw-set">
              <legend>A change counts when one of these fields changes</legend>
              <div className="lw-fields">
                {card.watchable.map((f) => <label key={f} className="check-row"><input type="checkbox" checked={form.fields.includes(f)} onChange={() => toggle("fields", f)} /> {f.replace(/_/g, " ")}</label>)}
              </div>
            </fieldset>
          )}
          <div className="row u-gap-8">
            <Switch checked={form.enabled} onChange={(enabled) => setForm({ ...form, enabled })} label="Lead webhook on" showState />
            <Button onClick={save} disabled={!!busy} data-testid="lw-save">{busy === "save" ? "Saving…" : "Save"}</Button>
            <Button variant="secondary" onClick={test} disabled={!!busy || !card.hook.configured} data-testid="lw-test">{busy === "test" ? "Sending…" : "Send a test"}</Button>
            {card.hook.configured && <Button variant="ghost" onClick={remove} disabled={!!busy}>Remove</Button>}
          </div>
        </div>
      </Card>

      {sheets && (
        <Card>
          <CardHeader title="The Apps Script to paste" description="Open your Google Sheet → Extensions → Apps Script, replace the code with this, then Deploy → New deployment → Web app (Execute as: Me, Who has access: Anyone) and paste the /exec address above. Each event adds one row; the first row becomes the header." />
          <pre className="lw-code">{card.apps_script}</pre>
          <p className="sub">Keep the web app address private: anyone with it can add rows to that sheet.</p>
        </Card>
      )}

      <Card>
        <CardHeader title="Last deliveries" description={`${q.pending} waiting · ${q.done} sent · ${q.failed} failed. Failed sends are retried after 1, 5, 15, 60 minutes, then 3, 6 and 12 hours.`}
          actions={q.failed > 0 && <Button size="sm" variant="secondary" onClick={retry} disabled={!!busy}>Retry failed</Button>} />
        {card.log.length === 0 ? <EmptyState icon="inbox" title="Nothing sent yet">Deliveries show here (the last 50).</EmptyState> : (
          <DataTable label="Lead webhook deliveries" density="dense" maxHeight="360px">
            <thead><tr><th>When</th><th>Event</th><th>Lead</th><th>Status</th><th>Result</th></tr></thead>
            <tbody>
              {card.log.map((r, i) => (
                <tr key={i}>
                  <td data-label="When">{istLabel(r.at)}</td>
                  <td data-label="Event">{r.event}</td>
                  <td data-label="Lead">{r.company || r.lead_id || "—"}</td>
                  <td data-label="Status"><Badge tone={TONE[r.status] || "neutral"}>{r.status}</Badge></td>
                  <td data-label="Result">{r.result}</td>
                </tr>
              ))}
            </tbody>
          </DataTable>
        )}
      </Card>
    </div>
  );
}
