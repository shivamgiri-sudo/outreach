import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { istLabel } from "../../time.js";
import { CustomHttpBuilder, CustomWebhookMapper } from "./CustomBuilder.jsx";
import { CopyRow, FieldInput, StatusPill, initialValues, missingRequired } from "./Fields.jsx";

// What the Test step asks for, per channel: [input type, label, placeholder]
const TEST_TARGET = {
  email_send: ["email", "Send the test email to", "you@yourcompany.in"],
  whatsapp: ["tel", "Your WhatsApp number (optional: sends a real message)", "+91 98765 43210"],
  sms: ["tel", "Your mobile number (optional: sends a real SMS)", "+91 98765 43210"],
  calls: ["tel", "Your phone number (optional: places a real call)", "+91 98765 43210"],
};
const HOOK_LABELS = {
  meta: ["Callback URL", "Verify token"], calcom: ["Subscriber URL", "Secret"], calendly: ["Webhook address", "Signing key"],
  svix: ["Endpoint URL", null], token: ["Webhook address", null],
};

function useDialog(panel, onClose) {
  useEffect(() => {
    const previous = document.activeElement;
    const onKey = (e) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !panel.current) return;
      const focusable = panel.current.querySelectorAll("button:not([disabled]), input, select, textarea, a[href]");
      if (!focusable.length) return;
      const [first, last] = [focusable[0], focusable[focusable.length - 1]];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    panel.current?.querySelector("h2")?.focus();
    return () => { document.removeEventListener("keydown", onKey); previous?.focus?.(); };
  }, []);
}

export default function Wizard({ channel, provider: start, meta, onClose, onChanged }) {
  const [provider, setProvider] = useState(start);
  const [step, setStep] = useState(0);
  const [mode, setMode] = useState(start.mode || start.modes?.[0] || "api");
  const [values, setValues] = useState(() => initialValues(start));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [hook, setHook] = useState(null);
  const [test, setTest] = useState({ to: "", template: "", result: null });
  const [confirmRemove, setConfirmRemove] = useState(false);
  const panel = useRef(null);
  useDialog(panel, onClose);

  const legacy = provider.legacy;
  const custom = provider.custom;
  const extraStep = provider.oauth ? "Connect" : provider.webhook ? "Webhook address" : null;
  const steps = ["What you need", "Your details", ...(extraStep ? [extraStep] : []), "Test"];
  const last = steps.length - 1;
  const canActivate = channel.kind === "exclusive" && !provider.active && ["configured", "error"].includes(provider.status);

  useEffect(() => {
    if (provider.webhook) api.channelWebhooks(channel.id, provider.id).then((d) => setHook(d.webhooks[0])).catch(() => {});
  }, []);

  const refresh = async () => {
    const data = await onChanged();
    const fresh = data?.channels.find((c) => c.id === channel.id)?.providers.find((p) => p.id === provider.id);
    if (fresh) setProvider(fresh);
    return fresh;
  };

  const save = async () => {
    setError("");
    const gaps = legacy && mode === "manual" ? [] : missingRequired(provider, values);
    if (gaps.length) { setError(`Please fill in: ${gaps.join(", ")}`); return false; }
    setBusy(true);
    try {
      if (legacy) await api.saveIntegration(provider.id, mode, values);
      else await api.saveChannel(channel.id, provider.id, values);
      const fresh = await refresh();
      if (fresh) setValues(initialValues(fresh));
      return true;
    } catch (e) { setError(e.message); return false; } finally { setBusy(false); }
  };

  const runTest = async () => {
    setBusy(true); setTest((t) => ({ ...t, result: null }));
    try {
      const result = legacy ? await api.testIntegration(provider.id, test.to)
        : await api.testChannel(channel.id, provider.id, { to: test.to, template: test.template });
      setTest((t) => ({ ...t, result }));
      await refresh();
    } catch (e) { setTest((t) => ({ ...t, result: { ok: false, message: e.message } })); } finally { setBusy(false); }
  };

  const act = async (fn) => { setError(""); setBusy(true); try { await fn(); await refresh(); } catch (e) { setError(e.message); } finally { setBusy(false); } };
  const connect = () => act(async () => {
    const r = await api.oauthStart(provider.id, window.location.origin + window.location.pathname);
    window.location.assign(r.url);
  });
  const remove = () => act(async () => { await api.deleteChannel(channel.id, provider.id); onClose(); });
  const next = async () => { if (step === 1 && !(await save())) return; setStep(Math.min(step + 1, last)); };
  const target = TEST_TARGET[channel.id] && provider.id !== "click_to_call" && provider.id !== "custom_webhook" ? TEST_TARGET[channel.id] : null;
  const [urlLabel, secretLabel] = HOOK_LABELS[provider.webhook?.auth] || HOOK_LABELS.token;

  return (
    <>
      <div className="scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer wizard" role="dialog" aria-modal="true" aria-labelledby="wizard-title" ref={panel} data-testid="wizard">
        <div className="drawer-head">
          <div>
            <h2 id="wizard-title" tabIndex={-1}>{provider.title}</h2>
            <span className="sub-inline">{channel.title}</span> <StatusPill status={provider.status} oauth={provider.oauth} saved={provider.saved} />
            {provider.active && <span className="pill ok" style={{ marginLeft: 6 }}>Active</span>}
          </div>
          <button className="btn ghost" onClick={onClose}>Close</button>
        </div>
        <ol className="wizard-steps" aria-label="Steps">
          {steps.map((s, i) => (
            <li key={s}><button type="button" className={i === step ? "on" : ""} aria-current={i === step ? "step" : undefined}
              onClick={() => setStep(i)}>{i + 1}. {s}</button></li>
          ))}
        </ol>
        {error && <div className="tip error" role="alert">{error}</div>}

        {step === 0 && (
          <section>
            <p>{provider.why}</p>
            <ol className="steps">{provider.steps.map((s) => <li key={s}>{s}</li>)}</ol>
            {provider.cost_note && <p className="field-help">Cost: {provider.cost_note}</p>}
            <a className="btn ghost" href={provider.docs_url} target="_blank" rel="noreferrer">Open provider dashboard / docs ↗</a>
          </section>
        )}

        {step === 1 && (
          <section>
            {legacy && provider.modes?.length > 1 && (
              <div className="row">
                {provider.modes.map((m) => (
                  <label key={m} className="row" style={{ fontWeight: 400, margin: 0 }}>
                    <input type="radio" checked={mode === m} onChange={() => setMode(m)} />
                    {m === "api" ? "Connect directly (API)" : "Manual (CSV / copy-paste)"}
                  </label>
                ))}
              </div>
            )}
            {provider.id === "custom_http" && <CustomHttpBuilder provider={provider} values={values} setValues={setValues} />}
            {provider.id === "custom_webhook" && <CustomWebhookMapper channel={channel.id} provider={provider} values={values} setValues={setValues} />}
            {!custom && (!legacy || mode === "api") && provider.fields.map((f) => (
              <FieldInput key={f.key} field={f} value={values[f.key]} saved={provider.values?.[f.key]}
                onChange={(v) => setValues({ ...values, [f.key]: v })} />
            ))}
            {!custom && provider.fields.length === 0 && !legacy && <p className="sub">Nothing to type for this one. Click Save, then copy the webhook address.</p>}
            <p className="field-help">Keys are stored on your server and shown masked afterwards.</p>
          </section>
        )}

        {step === 2 && extraStep === "Connect" && (
          <section>
            <p>Add this exact address as the <b>redirect URI</b> in the provider's console, then click Connect and allow access.</p>
            <CopyRow label="Redirect URI" value={meta.oauth_redirects?.[provider.id] || ""} testid="redirect-uri" />
            {meta.public_base_url?.includes("localhost") && (
              <p className="field-help">This uses a localhost address, which only works on this computer. Set your public web address in Setup first for a shared server.</p>
            )}
            <button className="btn" disabled={busy || !provider.saved} onClick={connect} style={{ marginTop: 12 }}>
              {provider.oauth_connected ? "Reconnect" : "Connect"} {provider.title.split(" ")[0]}
            </button>
            {!provider.saved && <p className="field-help">Save your details in step 2 first.</p>}
            {provider.oauth_connected && <p className="result ok">✓ Connected. Run the test to confirm.</p>}
          </section>
        )}

        {step === 2 && extraStep === "Webhook address" && (
          <section>
            <p>Paste this into <b>{provider.webhook.where}</b>{provider.webhook.events.length > 0 && <> and tick: {provider.webhook.events.join(", ")}</>}.</p>
            {hook ? (
              <>
                <CopyRow label={urlLabel} value={hook.url} testid="webhook-url" />
                {secretLabel && <CopyRow label={secretLabel} value={hook.secret} testid="webhook-secret" />}
                {provider.webhook.auth === "calendly" && <p className="field-help">The Test step registers this with Calendly for you.</p>}
                {provider.webhook.auth === "svix" && <p className="field-help">Then copy Resend's signing secret (whsec_…) into step 2 so every call is signature-checked.</p>}
                <p className="field-help">The address contains a secret code: treat it like a password. It only works once this card is saved.</p>
                <button type="button" className="btn ghost" disabled={busy} onClick={() => act(async () => {
                  await api.rotateWebhook(channel.id, provider.id);
                  setHook((await api.channelWebhooks(channel.id, provider.id)).webhooks[0]);
                })}>Make a new secret (the old address stops working)</button>
              </>
            ) : <p className="sub">Loading the address…</p>}
          </section>
        )}

        {step === last && (
          <section>
            <p className="sub">{provider.test}</p>
            {target && (
              <label>{target[1]}
                <input type={target[0]} value={test.to} placeholder={target[2]} onChange={(e) => setTest({ ...test, to: e.target.value })} />
              </label>
            )}
            {channel.id === "whatsapp" && provider.id !== "custom_webhook" && (
              <label>Approved template name {provider.id === "meta_cloud" ? "(empty = Meta's hello_world)" : ""}
                <input value={test.template} onChange={(e) => setTest({ ...test, template: e.target.value })} />
              </label>
            )}
            {provider.id === "custom_webhook" ? (
              <p>Use <b>Preview what the app reads</b> in step 2 with a sample payload to check the mapping.</p>
            ) : (
              <div className="row" style={{ marginTop: 10 }}>
                <button className="btn" disabled={busy || !provider.saved} onClick={runTest} data-testid="run-test">{busy ? "Testing…" : "Run test"}</button>
                {provider.id === "imap" && provider.saved && (
                  <button className="btn ghost" disabled={busy} onClick={() => act(async () => setTest({ ...test, result: await api.pollImap() }))}>Check for replies now</button>
                )}
              </div>
            )}
            {!provider.saved && <p className="field-help">Save your details in step 2 first.</p>}
            {test.result && (
              <div className={`tip ${test.result.ok ? "" : "error"}`} role="status" style={{ marginTop: 10 }} data-testid="test-result">
                <b className={`result ${test.result.ok ? "ok" : "bad"}`}>{test.result.ok ? "✓ Works. " : "✗ Not yet. "}</b>{test.result.message}
                <p style={{ margin: "6px 0 0" }}>
                  {test.result.ok ? (canActivate ? "Next: make it the active provider below." : "All set.") : "Next: check the details in step 2 and try again."}
                </p>
                {test.result.preview && <><b>Request sent</b><pre>{`${test.result.preview.method} ${test.result.preview.url}\n${Object.entries(test.result.preview.headers || {}).map(([k, v]) => `${k}: ${v}`).join("\n")}${test.result.preview.body ? `\n\n${test.result.preview.body}` : ""}`}</pre></>}
                {test.result.response && <><b>Provider reply (first 2 KB)</b><pre>{test.result.response}</pre></>}
              </div>
            )}
            {provider.last_test && !test.result && (
              <p className="field-help">Last test {istLabel(provider.last_test.at)}: {provider.last_test.ok ? "worked" : "failed"} — {provider.last_test.message}</p>
            )}
            {canActivate && (
              <button className="btn cta" style={{ marginTop: 12 }} disabled={busy} onClick={() => act(() => api.setActiveChannel(channel.id, provider.id))}>
                Make this the active provider for {channel.title}
              </button>
            )}
          </section>
        )}

        <div className="wizard-foot">
          <div className="row">
            {step > 0 && <button className="btn ghost" onClick={() => setStep(step - 1)}>Back</button>}
            {step < last && <button className="btn" disabled={busy} onClick={next}>{step === 1 ? (busy ? "Saving…" : "Save and continue") : "Next"}</button>}
            {step === 1 && <button className="btn ghost" disabled={busy} onClick={save}>Save</button>}
          </div>
          {provider.saved && !legacy && (confirmRemove
            ? <span className="row"><button className="btn danger-solid" disabled={busy} onClick={remove}>Yes, remove these details</button>
                <button className="btn ghost" onClick={() => setConfirmRemove(false)}>Keep</button></span>
            : <button className="btn ghost danger" disabled={busy} onClick={() => setConfirmRemove(true)}>Remove</button>)}
        </div>
      </div>
    </>
  );
}
