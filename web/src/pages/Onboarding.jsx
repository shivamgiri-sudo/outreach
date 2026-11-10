// First-run setup wizard for the owner: your business, what you sell, industry preset, email, leads, team, done.
// Every step can be skipped; where you are is saved on the server (settings.onboarding), so it resumes after a reload
// and can be opened again from Setup → Brand & offer.
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { setBrand } from "../brand.js";
import { useModalFocus } from "../DrawerShell.jsx";
import { LogoPicker, pickFields } from "./BrandSettings.jsx";
import Abbr from "../components/Abbr.jsx";

const STEPS = [
  ["business", "Your business"], ["offer", "What you sell"], ["industry", "Industry"], ["email", "Connect email"],
  ["leads", "Import leads"], ["team", "Invite team"], ["done", "Done"],
];
const BUSINESS = [["company_name", "Company name", "Skyline Homes"], ["product_name", "Product or service", "Skyline Residences"],
  ["tagline", "Tagline (optional)", "Homes that are ready when you are"], ["website", "Website (optional)", "https://skylinehomes.in"],
  ["sender_name", "Who signs the emails", "Sales team at Skyline Homes"]];
const OFFER = [["what", "What you sell", "Ready-to-move 2 and 3 BHK homes in Pune."], ["who", "Who it is for", "Families and investors in Pune"],
  ["value_1", "Value point 1", "Possession in 6 months"], ["value_2", "Value point 2", "No brokerage"],
  ["value_3", "Value point 3", "Free site-visit pick-up"], ["cta", "Call to action", "Shall we book a site visit this weekend?"],
  ["booking_link", "Demo / booking link (optional)", "https://cal.com/you/visit"]];

// /api/presets may answer a list or {presets: [...]}; names may sit under name, label or title.
const presetList = (data) => (Array.isArray(data) ? data : data?.presets || data?.items || []).filter((p) => p && (p.id || p.key));
const presetName = (p) => p.name || p.label || p.title || p.id || p.key;

function Fields({ fields, value, onChange, prefix }) {
  return fields.map(([key, label, placeholder]) => (
    <label key={key} htmlFor={`${prefix}-${key}`}>{label}
      {key === "what" ? <textarea id={`${prefix}-${key}`} rows={2} value={value[key] || ""} placeholder={placeholder} onChange={(e) => onChange({ ...value, [key]: e.target.value })} />
        : <input id={`${prefix}-${key}`} value={value[key] || ""} placeholder={placeholder} onChange={(e) => onChange({ ...value, [key]: e.target.value })} />}
    </label>
  ));
}

function Industry({ onApplied }) {
  const [presets, setPresets] = useState(null);
  const [state, setState] = useState("");
  const [chosen, setChosen] = useState("");
  useEffect(() => { api.presets().then((d) => setPresets(presetList(d))).catch(() => setPresets(false)); }, []);
  if (presets === null) return <p className="sub">Loading industries…</p>;
  if (presets === false || presets.length === 0) {
    return <p className="tip" role="status">Industry presets are unavailable on this server. Skip this step; you can write your own sequences and templates later.</p>;
  }
  const apply = () => {
    setState("Applying…");
    api.applyPreset(chosen).then(() => { setState(`Applied: ${presetName(presets.find((p) => (p.id || p.key) === chosen))}.`); onApplied?.(); })
      .catch((e) => setState(e.message));
  };
  return (
    <>
      <p className="sub">A preset fills in starter sequences, templates and stage names for your industry. You can change everything afterwards.</p>
      <div className="onb-presets" role="radiogroup" aria-label="Industry presets">
        {presets.map((p) => {
          const id = p.id || p.key;
          return (
            <label key={id} className={`onb-preset${chosen === id ? " on" : ""}`}>
              <input type="radio" name="preset" value={id} checked={chosen === id} onChange={() => setChosen(id)} />
              <span><b>{presetName(p)}</b>{p.description && <small>{p.description}</small>}</span>
            </label>
          );
        })}
      </div>
      <button type="button" className="btn" disabled={!chosen} onClick={apply}>Apply preset</button>
      {state && <p className="sub" role="status">{state}</p>}
    </>
  );
}

export default function Onboarding({ onClose, go }) {
  const panel = useRef(null);
  const [step, setStep] = useState(null);
  const [done, setDone] = useState([]);
  const [data, setData] = useState(null);
  const [business, setBusiness] = useState({});
  const [offer, setOffer] = useState({});
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useModalFocus(panel, () => onClose(), true, "h2[tabindex]");     // Escape closes for now; it opens again next time

  const take = (d) => {
    setData(d); setBrand(d);
    setBusiness(d.is_default ? Object.fromEntries(BUSINESS.map(([k]) => [k, ""])) : pickFields(d.brand, BUSINESS));
    setOffer(d.is_default ? Object.fromEntries(OFFER.map(([k]) => [k, ""])) : pickFields(d.offer, OFFER));
  };
  useEffect(() => {
    Promise.all([api.onboarding(), api.brand()]).then(([o, d]) => {
      setStep(o.step || "business"); setDone(o.done_steps || []); take(d);
    }).catch((e) => setMsg(e.message));
  }, []);
  useEffect(() => { panel.current?.querySelector("h2[tabindex]")?.focus(); }, [step]);

  const index = STEPS.findIndex(([id]) => id === step);
  const move = (to, markDone = true) => {
    const nextDone = markDone && step && !done.includes(step) ? [...done, step] : done;
    setDone(nextDone); setStep(to); setMsg("");
    api.saveOnboarding({ step: to, done_steps: nextDone }).catch(() => {});
  };
  const next = () => move(STEPS[Math.min(index + 1, STEPS.length - 1)][0]);
  const back = () => move(STEPS[Math.max(index - 1, 0)][0], false);
  const skipAll = () => { api.saveOnboarding({ skipped: true, step }).catch(() => {}).finally(() => onClose()); };
  const finish = () => { api.saveOnboarding({ finished: true, step: "done", done_steps: [...new Set([...done, "done"])] }).catch(() => {}).finally(() => onClose()); };

  const saveAndNext = async (save) => {
    setBusy(true); setMsg("");
    try { take(await save()); next(); } catch (e) { setMsg(e.message); }
    setBusy(false);
  };
  const nonEmpty = (o) => Object.fromEntries(Object.entries(o).filter(([, v]) => String(v || "").trim()));
  const leave = (page) => { api.saveOnboarding({ step }).catch(() => {}); onClose(); go?.(page); };

  if (!step || !data) {
    return <div className="onb-scrim"><div className="onb" ref={panel} role="dialog" aria-modal="true" aria-label="Setup"><p className="sub">{msg || "Loading…"}</p></div></div>;
  }
  const [, title] = STEPS[index];
  return (
    <div className="onb-scrim">
      <div className="onb" ref={panel} role="dialog" aria-modal="true" aria-labelledby="onb-title">
        <div className="onb-head">
          <ol className="onb-steps" aria-label="Setup steps">
            {STEPS.map(([id, label], i) => (
              <li key={id} className={`${id === step ? "on" : ""}${done.includes(id) ? " done" : ""}`} aria-current={id === step ? "step" : undefined}>
                <button type="button" onClick={() => move(id, false)} aria-label={`Step ${i + 1}: ${label}${done.includes(id) ? " (done)" : ""}`}>
                  <span className="onb-num" aria-hidden="true">{done.includes(id) ? "✓" : i + 1}</span><span className="onb-label">{label}</span>
                </button>
              </li>
            ))}
          </ol>
          <div className="row onb-top-actions">
            <button type="button" className="btn ghost" onClick={onClose}>Later</button>
            <button type="button" className="btn ghost" onClick={skipAll}>Skip setup</button>
          </div>
        </div>
        <div className="onb-body">
          <p className="sub" style={{ margin: 0 }}>Step {index + 1} of {STEPS.length}</p>
          <h2 id="onb-title" tabIndex={-1}>{title}</h2>
          {step === "business" && (
            <>
              <p className="sub">Your name and logo appear in the app, on emails and on the demo page.</p>
              <LogoPicker data={data} onSaved={take} />
              <div className="brand-grid"><Fields fields={BUSINESS} value={business} onChange={setBusiness} prefix="onb-b" /></div>
            </>
          )}
          {step === "offer" && (
            <>
              <p className="sub">This fills the {"{{my_product}}"}, {"{{my_value_1}}"}…{"{{my_value_3}}"} and {"{{my_cta}}"} placeholders, and tells the AI writer what it may say.</p>
              <div className="brand-grid"><Fields fields={OFFER} value={offer} onChange={setOffer} prefix="onb-o" /></div>
            </>
          )}
          {step === "industry" && <Industry />}
          {step === "email" && (
            <>
              <p className="sub">Send from a separate look-alike domain (for example mail.yourcompany.com) so your main email stays safe.</p>
              <ol className="steps">
                <li>Buy the look-alike domain and add it to your email provider.</li>
                <li>Add the <Abbr term="SPF">SPF</Abbr>, <Abbr term="DKIM">DKIM</Abbr> and <Abbr term="DMARC">DMARC</Abbr> records they show you.</li>
                <li>Open Integrations → Email and follow the guided steps, then send yourself a test.</li>
              </ol>
              <button type="button" className="btn ghost" onClick={() => leave("integrations")}>Open Integrations</button>
            </>
          )}
          {step === "leads" && (
            <>
              <p className="sub">Upload a <Abbr term="CSV">CSV</Abbr> or Excel sheet; the importer matches your columns and removes duplicates.</p>
              <button type="button" className="btn ghost" onClick={() => leave("leads")}>Open the importer</button>
            </>
          )}
          {step === "team" && (
            <>
              <p className="sub">Give each caller and viewer their own login. Owners see everything; callers work leads and calls.</p>
              <button type="button" className="btn ghost" onClick={() => leave("setup/members")}>Open Team members</button>
            </>
          )}
          {step === "done" && (
            <p>You are set. {data.public.company_name || data.public.product_name} now appears in the app header, on emails and on the demo page. Change anything later in Setup → Brand &amp; offer.</p>
          )}
          {msg && <p className="error" role="alert">{msg}</p>}
        </div>
        <div className="onb-foot">
          {index > 0 && <button type="button" className="btn ghost" onClick={back}>Back</button>}
          <span style={{ flex: 1 }} />
          {step === "business" && <button type="button" className="btn" disabled={busy} onClick={() => saveAndNext(() => api.saveBrand(nonEmpty(business)))}>Save and continue</button>}
          {step === "offer" && <button type="button" className="btn" disabled={busy} onClick={() => saveAndNext(() => api.saveOfferProfile(nonEmpty(offer)))}>Save and continue</button>}
          {!["business", "offer", "done"].includes(step) && <button type="button" className="btn" onClick={next}>Continue</button>}
          {["business", "offer"].includes(step) && <button type="button" className="btn ghost" onClick={next}>Skip this step</button>}
          {step === "done" && <button type="button" className="btn" onClick={finish}>Finish</button>}
        </div>
      </div>
    </div>
  );
}
