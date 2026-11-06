// Setup → Brand & offer: who you are (names, logo, colours, sign-off, address), what you sell, and your words for
// pipeline stages and lead numbers. A live preview shows the designed email and the app header as you type.
import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { contrast, setBrand } from "../brand.js";
import { STAGE_LABELS } from "../labels.js";
import { useConfirm } from "../Dialog.jsx";

const BRAND_FIELDS = [
  ["company_name", "Company name", "Mas Callnet"], ["product_name", "Product or service name", "BookMyInterview.ai"],
  ["app_name", "App name (menu and sign-in page)", "MCN Outreach"], ["tagline", "Tagline", "One line about what you do"],
  ["website", "Website", "https://yourcompany.com"], ["support_email", "Support email", "hello@yourcompany.com"],
  ["sender_name", "Default sender name", "Priya from Your Company"], ["industry", "Industry", "Real estate"],
];
const OFFER_FIELDS = [
  ["what", "What you sell (one or two sentences)", "Ready-to-move 2 and 3 BHK homes in Pune with flexible payment plans.", true],
  ["who", "Who it is for", "Families and investors looking for a home in Pune"],
  ["value_1", "Value point 1", "Possession in 6 months"], ["value_2", "Value point 2", "No brokerage"],
  ["value_3", "Value point 3", "Site visits any day, with pick-up"],
  ["cta", "Call to action", "Would you like to book a site visit this weekend?"],
  ["booking_link", "Demo / booking link", "https://cal.com/yourname/visit"],
];
const MAX_LOGO = 1024 * 1024;
const TYPES = ["image/png", "image/jpeg", "image/svg+xml"];

export function LogoPicker({ data, onSaved }) {
  const [msg, setMsg] = useState("");
  const input = useRef(null);
  const pick = (file) => {
    setMsg("");
    if (!file) return;
    if (!TYPES.includes(file.type)) { setMsg("Choose a PNG, JPG or SVG image."); return; }
    if (file.size > MAX_LOGO) { setMsg(`That file is ${Math.round(file.size / 1024)} KB; the limit is 1 MB.`); return; }
    const reader = new FileReader();
    reader.onload = () => api.uploadLogo({ content_type: file.type, data: String(reader.result).split(",").pop(), filename: file.name })
      .then((d) => { onSaved(d); setMsg("Logo saved."); }).catch((e) => setMsg(e.message));
    reader.readAsDataURL(file);
  };
  const logo = data.public;
  return (
    <div className="brand-logo-row">
      <div className="brand-logo-box" aria-hidden={!logo.custom_logo}>
        {logo.custom_logo ? <img src={logo.logo_url} alt="Your logo" /> : <span className="sub">No logo yet</span>}
      </div>
      <div>
        <input ref={input} id="logo-file" type="file" accept=".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml" className="sr-only"
          onChange={(e) => { pick(e.target.files[0]); e.target.value = ""; }} />
        <label htmlFor="logo-file" className="btn ghost" role="button" tabIndex={0}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.current?.click(); } }}>
          {logo.custom_logo ? "Change logo" : "Upload logo"}</label>
        {logo.custom_logo && <button type="button" className="btn ghost" onClick={() => api.deleteLogo().then(onSaved).catch((e) => setMsg(e.message))}>Remove</button>}
        <p className="sub" style={{ margin: "4px 0 0" }}>PNG, JPG or SVG, up to 1 MB. PNG shows best in email (most mail apps block SVG, so an SVG logo appears as your name there).</p>
        {msg && <p className="sub" role="status">{msg}</p>}
      </div>
    </div>
  );
}

function ColourField({ id, label, value, onChange, checkText }) {
  const ratio = checkText ? contrast(value) : null;
  return (
    <div>
      <label htmlFor={id}>{label}</label>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input type="color" aria-label={`${label} picker`} value={/^#[0-9a-f]{6}$/i.test(value) ? value : "#0369a1"} style={{ width: 48, padding: 2 }}
          onChange={(e) => onChange(e.target.value)} />
        <input id={id} value={value} maxLength={7} onChange={(e) => onChange(e.target.value)} style={{ maxWidth: 120 }} />
      </div>
      {checkText && ratio !== null && (
        <p className="sub" role="status" style={{ margin: "4px 0 0", color: ratio < 3 ? "var(--hot)" : undefined }}>
          White text on it: {ratio}:1 {ratio < 3 ? "— too light, pick a darker shade" : ratio < 4.5 ? "— fine for buttons, a darker shade reads better" : "— good"}
        </p>
      )}
    </div>
  );
}

export function Preview({ brand, offer }) {
  const [view, setView] = useState(null);
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => api.brandPreview({ brand, offer }).then((v) => live && setView(v)).catch(() => {}), 350);
    return () => { live = false; clearTimeout(t); };
  }, [JSON.stringify(brand), JSON.stringify(offer)]);
  if (!view) return <p className="sub">Loading preview…</p>;
  const pub = view.public || {};
  return (
    <div className="brand-preview">
      <p className="nav-title" style={{ margin: "0 0 6px" }}>App header</p>
      <div className="brand brand-preview-head">
        <img className="brand-mark" src={pub.header_logo} width="34" height="34" alt="" />
        <div className="brand-text"><b>{pub.app_name}</b><span className="brand-sub">by {pub.company_name}</span></div>
      </div>
      <p className="nav-title" style={{ margin: "14px 0 6px" }}>Designed email</p>
      {view.ok === false ? <p className="error" role="alert">{view.error}</p> : (
        <iframe title="Designed email preview" className="brand-email-frame" sandbox="" srcDoc={view.html} />
      )}
      {view.missing?.length > 0 && <p className="sub">Not filled yet: {view.missing.map((m) => `{{${m}}}`).join(", ")} — emails that use them are held until you fill them.</p>}
    </div>
  );
}

export function pickFields(source, fields) {
  return Object.fromEntries(fields.map(([k]) => [k, source?.[k] ?? ""]));
}

function Labels({ data, onSaved }) {
  const [stages, setStages] = useState({ ...data.stage_labels });
  const [terms, setTerms] = useState({ ...data.lead_terms });
  const [msg, setMsg] = useState("");
  const save = (e) => {
    e.preventDefault();
    api.saveLabels({ stage_labels: stages, lead_terms: terms })
      .then(() => api.brand()).then((d) => { onSaved(d); setMsg("Saved."); }).catch((err) => setMsg(err.message));
  };
  return (
    <form className="card" style={{ marginBottom: 16 }} onSubmit={save} aria-labelledby="labels-h">
      <b id="labels-h">Your words for stages and lead numbers</b>
      <p className="sub" style={{ margin: "2px 0 8px" }}>Rename how pipeline stages read everywhere (lists, boards, exports), e.g. “Interested” → “Site visit booked”. The stage itself and its rules stay the same.</p>
      <div className="brand-grid">
        {Object.keys(STAGE_LABELS).map((id) => (
          <label key={id}>{STAGE_LABELS[id]}
            <input value={stages[id] || ""} placeholder={STAGE_LABELS[id]} maxLength={40} onChange={(e) => setStages({ ...stages, [id]: e.target.value })} />
          </label>
        ))}
      </div>
      <div className="brand-grid" style={{ marginTop: 10 }}>
        <label>What the “open roles” number means<input value={terms.open_roles || ""} placeholder="Open roles / Units / Seats" maxLength={40} onChange={(e) => setTerms({ ...terms, open_roles: e.target.value })} /></label>
        <label>What the lead’s “role” field means<input value={terms.role || ""} placeholder="Role / Requirement / Property type" maxLength={40} onChange={(e) => setTerms({ ...terms, role: e.target.value })} /></label>
      </div>
      <button className="btn" style={{ marginTop: 10 }}>Save names</button>
      {msg && <span className="sub" role="status" style={{ marginLeft: 10 }}>{msg}</span>}
    </form>
  );
}

export default function BrandSettings({ onOpenWizard }) {
  const [data, setData] = useState(null);
  const [brand, setBrandDraft] = useState(null);
  const [offer, setOffer] = useState(null);
  const [colours, setColours] = useState(null);
  const [msg, setMsg] = useState("");
  const [error, setError] = useState("");
  const confirm = useConfirm();
  const take = (d) => {
    setData(d); setBrand(d);
    setBrandDraft({ ...pickFields(d.brand, BRAND_FIELDS), sign_off: d.brand.sign_off, postal_address: d.brand.postal_address });
    setOffer({ ...pickFields(d.offer, OFFER_FIELDS), demo_kind: d.offer.demo_kind });
    setColours({ primary_color: d.brand.primary_color, accent_color: d.brand.accent_color });
  };
  useEffect(() => { api.brand().then(take).catch((e) => setError(e.message)); }, []);
  if (error) return <p className="error" role="alert">{error}</p>;
  if (!data || !brand) return <p className="sub">Loading…</p>;

  const ratio = contrast(colours.primary_color);
  const save = async (e) => {
    e.preventDefault();
    setMsg("");
    try {
      await api.saveBrand({ ...brand, ...colours });
      take(await api.saveOfferProfile(offer));
      setMsg("Saved. Emails, the demo page and the app now use these details.");
    } catch (err) { setMsg(err.message); }
  };
  const reset = async () => {
    if (!(await confirm({ message: "Go back to the original brand and offer? Your names, logo and offer text are removed.", confirmLabel: "Reset", danger: true }))) return;
    api.resetBrand().then((d) => { take(d); setMsg("Back to the original brand."); }).catch((err) => setMsg(err.message));
  };
  const field = (state, setState) => ([key, label, placeholder, wide]) => (
    <label key={key} className={wide ? "wide" : ""}>{label}
      {wide ? <textarea rows={2} value={state[key]} placeholder={placeholder} onChange={(e) => setState({ ...state, [key]: e.target.value })} />
        : <input value={state[key]} placeholder={placeholder} onChange={(e) => setState({ ...state, [key]: e.target.value })} />}
    </label>
  );
  return (
    <div className="brand-page">
      <div className="tip" style={{ marginBottom: 16 }}>
        <b>{data.is_default ? "You are using the original brand." : "Your brand is set."}</b> Everything here fills the {"{{my_company}}"}, {"{{my_product}}"}, {"{{my_value_1}}"}…, {"{{my_cta}}"}, {"{{my_website}}"} and {"{{sign_off}}"} placeholders in emails, WhatsApp, LinkedIn, the call script and flows.
        {onOpenWizard && <div className="row" style={{ marginTop: 8 }}><button type="button" className="btn ghost" onClick={onOpenWizard}>Open the setup wizard</button></div>}
      </div>
      <div className="brand-layout">
        <form className="card" onSubmit={save} aria-labelledby="brand-h">
          <b id="brand-h">Your business</b>
          <LogoPicker data={data} onSaved={take} />
          <div className="brand-grid">{BRAND_FIELDS.map(field(brand, setBrandDraft))}</div>
          <div className="brand-grid">
            <ColourField id="c-primary" label="Main colour" value={colours.primary_color} checkText onChange={(v) => setColours({ ...colours, primary_color: v })} />
            <ColourField id="c-accent" label="Accent colour" value={colours.accent_color} onChange={(v) => setColours({ ...colours, accent_color: v })} />
          </div>
          <label htmlFor="sign-off">Email sign-off</label>
          <textarea id="sign-off" rows={2} value={brand.sign_off} onChange={(e) => setBrandDraft({ ...brand, sign_off: e.target.value })} />
          <label htmlFor="postal">Postal address (needed for emails to the US and Canada)</label>
          <textarea id="postal" rows={2} value={brand.postal_address} onChange={(e) => setBrandDraft({ ...brand, postal_address: e.target.value })} />
          <h3 style={{ marginTop: 18 }}>What you sell</h3>
          <div className="brand-grid">{OFFER_FIELDS.map(field(offer, setOffer))}
            <label>Demo page style
              <select value={offer.demo_kind} onChange={(e) => setOffer({ ...offer, demo_kind: e.target.value })}>
                <option value="overview">Product overview (any business)</option>
                <option value="screening">Sample screening kit (hiring)</option>
              </select>
            </label>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <button className="btn" disabled={ratio !== null && ratio < 3}>Save brand &amp; offer</button>
            {!data.is_default && <button type="button" className="btn ghost danger" onClick={reset}>Reset to original</button>}
          </div>
          {msg && <p className="sub" role="status">{msg}</p>}
        </form>
        <div className="card brand-preview-card"><Preview brand={{ ...brand, ...colours }} offer={offer} /></div>
      </div>
      <Labels data={data} onSaved={take} />
    </div>
  );
}
