import { useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { STAGE_ORDER, stageLabel } from "../../labels.js";
import {
  ACTIVATION_EVENTS, BRANCH_HEADINGS, EMAIL_PLACEHOLDERS, IF_FIELDS, IF_OPS, PERSONAS, REPLY_LABELS, REPLY_OUTPUTS,
  TEXT_PLACEHOLDERS, helpOf, isTrigger, labelOf, opsFor, outputLabel, outputsOf, titleOf,
} from "./catalog.js";

const pretty = (s) => String(s).replace(/_/g, " ");

function Field({ id, label, hint, children }) {
  return (
    <label htmlFor={id}>
      {label}
      {children}
      {hint && <span className="field-help">{hint}</span>}
    </label>
  );
}

// Click a chip to put {{placeholder}} where the cursor is in the last text box you used.
function Chips({ names, onInsert }) {
  return (
    <div className="chips" aria-label="Insert a placeholder">
      {names.map((p) => (
        <button key={p} type="button" className="chip" onMouseDown={(e) => e.preventDefault()} onClick={() => onInsert(`{{${p}}}`)}>
          {`{{${p}}}`}
        </button>
      ))}
    </div>
  );
}

// Plain-English settings for the selected step, its connections and its problems.
function WebhookBox({ flowId }) {
  const [hook, setHook] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let alive = true;
    api.flowWebhook(flowId).then((h) => alive && setHook(h)).catch((e) => alive && setErr(e.message || "Could not make the address."));
    return () => { alive = false; };
  }, [flowId]);
  if (err) return <p className="field-help" role="alert">{err}</p>;
  if (!hook) return <p className="field-help">Making the address…</p>;
  const url = hook.urls?.capture || "";
  const curl = `curl -X POST '${url}' -H 'Authorization: Bearer ${hook.token}' -H 'Content-Type: application/json' -d '{"name":"Asha Rao","email":"asha@example.com","company":"Example Co","phone":"98100 12345"}'`;
  const copy = (text) => { try { navigator.clipboard?.writeText(text); } catch { /* the text is selectable anyway */ } };
  return (
    <fieldset className="fb-fieldset">
      <legend>Address for other tools</legend>
      <label>Post to<input readOnly value={url} onFocus={(e) => e.target.select()} /></label>
      <label>Token (send as a Bearer token)<input readOnly value={hook.token || ""} onFocus={(e) => e.target.select()} /></label>
      <div className="row"><button type="button" className="btn" onClick={() => copy(url)}>Copy address</button>
        <button type="button" className="btn" onClick={() => copy(curl)}>Copy test command</button></div>
      <span className="field-help">
        {hook.active ? `Live. ${hook.counters?.total || 0} lead(s) received so far.` : "Turn the flow on and the address starts working."}
        {" "}A lead needs an e-mail or phone number. A person already in your list is updated, not started again.
      </span>
    </fieldset>
  );
}

export default function NodeConfig({ flowId, node, nodes, edges, catalog, problems, onChange, onConnect, onDelete, onDuplicate, panelRef }) {
  const c = node.config || {};
  const lastText = useRef({ key: null, el: null });
  const team = catalog?.team?.length ? catalog.team : [catalog?.caller || "Sales team"];
  const templates = catalog?.whatsapp_templates || ["first_after_yes"];
  const set = (key, value) => onChange({ [key]: value }, `${node.id}:${key}`);
  const num = (key) => (e) => set(key, e.target.value === "" ? "" : Number(e.target.value));
  const id = (key) => `fb-${node.id}-${key}`;
  const track = (key) => ({ onFocus: (e) => { lastText.current = { key, el: e.target }; } });
  const insert = (token) => {
    const { key, el } = lastText.current;
    if (!key) return;
    const value = String(c[key] ?? "");
    const start = el?.selectionStart ?? value.length;
    const end = el?.selectionEnd ?? value.length;
    set(key, value.slice(0, start) + token + value.slice(end));
    requestAnimationFrame(() => { el?.focus(); el?.setSelectionRange?.(start + token.length, start + token.length); });
  };
  const text = (key, label, opts = {}) => (
    <Field id={id(key)} label={label} hint={opts.hint}>
      {opts.long
        ? <textarea id={id(key)} rows={opts.rows || 6} value={c[key] ?? ""} placeholder={opts.placeholder || ""} {...track(key)} onChange={(e) => set(key, e.target.value)} />
        : <input id={id(key)} value={c[key] ?? ""} placeholder={opts.placeholder || ""} {...track(key)} onChange={(e) => set(key, e.target.value)} />}
    </Field>
  );
  const select = (key, label, options, hint) => (
    <Field id={id(key)} label={label} hint={hint}>
      <select id={id(key)} value={c[key] ?? ""} onChange={(e) => set(key, e.target.value)}>
        {options.map(([value, shown]) => <option key={value} value={value}>{shown}</option>)}
      </select>
    </Field>
  );
  const flowVars = [...new Set(nodes.flatMap((n) => (n.type === "set_vars" ? (n.config?.values || []).map((v) => v.name) : ["ai", "http"].includes(n.type) ? [n.config?.save_as] : []))
    .filter((v) => v && /^[a-z][a-z0-9_]{0,29}$/.test(v)))];
  const withVars = (names) => [...names, ...flowVars];
  const owner = (key, label) => select(key, label, [["", `${catalog?.caller || "Sales team"} (the caller)`], ...team.filter((t) => t !== catalog?.caller).map((t) => [t, t])]);
  const kind = IF_FIELDS[c.field]?.[1] || "text";
  const valueInput = () => {
    if (c.op === "exists") return null;
    if (kind === "bool") return select("value", "Value", [["true", "Yes"], ["false", "No"]]);
    if (kind === "stage" && c.op !== "in") return select("value", "Value", STAGE_ORDER.map((s) => [s, stageLabel(s)]));
    if (kind === "band") return select("value", "Value", [["hot", "Hot"], ["warm", "Warm"], ["cold", "Cold"]]);
    if (kind === "persona" && c.op !== "in") return select("value", "Value", PERSONAS.map((p) => [p, pretty(p)]));
    const hint = c.op === "in" ? "Separate several values with commas." : undefined;
    const placeholder = { number: "60", text: c.field === "city" ? "Pune, Bengaluru, Gurugram" : c.field === "country" ? "IN" : "", tags: "diwali-offer" }[kind] || "";
    return text("value", "Value", { hint, placeholder });
  };

  const listOf = (key) => (Array.isArray(c[key]) ? c[key] : []);
  const setRow = (i, patch) => set("values", listOf("values").map((row, j) => (j === i ? { ...row, ...patch } : row)));
  const fields = {
    "trigger.webhook": () => <WebhookBox flowId={flowId} />,
    "trigger.schedule": () => (<>
      {select("when", "How often", [["daily", "Every day"], ["weekdays", "Every weekday (Mon–Fri)"], ["weekly", "Once a week"]])}
      {c.when === "weekly" && select("weekday", "Day", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((d, i) => [String(i), d]))}
      <Field id={id("hour")} label="Hour (India time)"><input id={id("hour")} type="number" min="0" max="23" value={c.hour ?? ""} onChange={num("hour")} /></Field>
      <Field id={id("limit")} label="Start at most this many leads each time" hint="Only leads that are not already in this flow.">
        <input id={id("limit")} type="number" min="1" max="500" value={c.limit ?? 50} onChange={num("limit")} /></Field>
      <fieldset className="fb-fieldset"><legend>Which leads (all optional)</legend>
        {select("band", "Temperature", [["", "Any"], ["hot", "Hot"], ["warm", "Warm"], ["cold", "Cold"]])}
        {select("stage", "Stage", [["", "Any"], ...STAGE_ORDER.map((s2) => [s2, stageLabel(s2)])])}
        {text("source", "Source contains", { placeholder: "apollo, meta_ads" })}
        {text("city", "City", { placeholder: "Pune" })}
        {text("tag", "Has tag", { placeholder: "bpo" })}
      </fieldset>
    </>),
    ai: () => (<>
      {select("mode", "What should Claude do", [["draft", "Write a draft"], ["classify", "Choose one of my options"], ["extract", "Pull out one detail"]])}
      {text("prompt", "Instruction", { long: true, rows: 5, placeholder: "Write a two-line opening for {{first_name}} at {{company}} about how {{my_product}} could help them.",
        hint: "Claude also sees the lead's company, title, city and stage. It is told to use only these facts." })}
      <Chips names={withVars(TEXT_PLACEHOLDERS)} onInsert={insert} />
      {c.mode === "classify" && (
        <Field id={id("options")} label="Options (separate with commas)" hint="Claude must answer with exactly one. Anything else becomes 'unknown'.">
          <input id={id("options")} value={listOf("options").join(", ")} placeholder="high, medium, low"
            onChange={(e) => set("options", e.target.value.split(",").map((v) => v.trim()).filter(Boolean))} /></Field>
      )}
      {text("save_as", "Save the answer as", { placeholder: "intent", hint: "Lowercase letters, numbers and _ . Use it later as {{intent}}, or in a Switch." })}
      <p className="field-help">Needs Claude connected under Integrations → AI. Leads' texts are treated as data, never as instructions.</p>
    </>),
    set_vars: () => (<>
      {listOf("values").map((row, i) => (
        <fieldset key={i} className="fb-fieldset"><legend>Field {i + 1}</legend>
          <Field id={id(`n${i}`)} label="Name"><input id={id(`n${i}`)} value={row.name ?? ""} placeholder="pitch" onChange={(e) => setRow(i, { name: e.target.value })} /></Field>
          <Field id={id(`v${i}`)} label="Value" hint="Can use {{company}}, {{role}}, {{first_name}} and earlier fields.">
            <textarea id={id(`v${i}`)} rows={2} value={row.value ?? ""} placeholder="{{my_product}} for {{company}}" onChange={(e) => setRow(i, { value: e.target.value })} /></Field>
          {listOf("values").length > 1 && <button type="button" className="btn ghost" onClick={() => set("values", listOf("values").filter((_, j) => j !== i))}>Remove this field</button>}
        </fieldset>
      ))}
      {listOf("values").length < 8 && <button type="button" className="btn ghost" onClick={() => set("values", [...listOf("values"), { name: "", value: "" }])}>Add a field</button>}
    </>),
    switch: () => (<>
      {select("field", "Switch on", [...Object.entries(IF_FIELDS).map(([k, [shown]]) => [k, shown]), ...flowVars.map((v) => [`var:${v}`, `Field made earlier: ${v}`])])}
      <Field id={id("cases")} label="Values to match (up to 4, separate with commas)" hint="Each value gets its own path, in this order. Everything else goes to 'other'. Capital letters do not matter.">
        <input id={id("cases")} value={listOf("cases").join(", ")} placeholder="hot, warm, cold"
          onChange={(e) => set("cases", e.target.value.split(",").map((v) => v.trim()).slice(0, 4))} /></Field>
    </>),
    http: () => (<>
      {select("method", "Method", [["GET", "GET (read)"], ["POST", "POST (send)"]])}
      {text("url", "Address (https only)", { placeholder: "https://api.example.com/lookup?company={{company}}", hint: "Public addresses only. Private and internal ones are refused." })}
      {c.method === "POST" && text("body", "Body (JSON)", { long: true, rows: 5, placeholder: '{"company": "{{company}}"}' })}
      <Chips names={withVars(TEXT_PLACEHOLDERS)} onInsert={insert} />
      {text("save_as", "Save the answer as (optional)", { placeholder: "lookup", hint: "Use it later as {{lookup}}, or in a Switch." })}
    </>),
    note: () => text("text", "Note", { long: true, rows: 5, placeholder: "Remind your team what this part of the flow is for." }),
    "trigger.lead_added": () => (<>
      {text("source", "Source contains (optional)", { placeholder: "apollo, sheet, careers_page" })}
      {text("city", "City (optional)", { placeholder: "Pune" })}
      {text("country", "Country (optional)", { placeholder: "IN" })}
      {text("tag", "Has tag (optional)", { placeholder: "bpo" })}
      {text("campaign_id", "Campaign id (optional)")}
    </>),
    "trigger.reply": () => select("label", "Which reply", REPLY_LABELS.map((l) => [l, l === "any" ? "Any real reply" : pretty(l)])),
    "trigger.stage_changed": () => select("stage", "Stage", STAGE_ORDER.map((s) => [s, stageLabel(s)])),
    "trigger.activation_event": () => select("event", "Product activity", ACTIVATION_EVENTS.map((e) => [e, pretty(e)])),
    "trigger.no_reply_days": () => (
      <Field id={id("days")} label="Days without a reply"><input id={id("days")} type="number" min="1" max="90" value={c.days ?? ""} onChange={num("days")} /></Field>
    ),
    if: () => (<>
      {select("field", "Check", Object.entries(IF_FIELDS).map(([k, [shown]]) => [k, shown]))}
      {select("op", "How", opsFor(kind).map((o) => [o, IF_OPS[o]]))}
      {valueInput()}
    </>),
    loop: () => (
      <Field id={id("times")} label="How many times to repeat" hint="Each round goes down 'again'; after the last round the lead goes down 'done'. The loop must include a Wait.">
        <input id={id("times")} type="number" min="1" max="10" value={c.times ?? 3} onChange={num("times")} />
      </Field>
    ),
    split: () => (
      <Field id={id("percent_a")} label={`Share of leads on path A: ${c.percent_a ?? 50}%`} hint="The rest go down path B.">
        <input id={id("percent_a")} type="number" min="1" max="99" value={c.percent_a ?? 50} onChange={num("percent_a")} />
      </Field>
    ),
    wait: () => (<>
      <div className="split-2">
        <Field id={id("days")} label="Days"><input id={id("days")} type="number" min="0" max="60" value={c.days ?? 0} onChange={num("days")} /></Field>
        <Field id={id("hours")} label="Hours"><input id={id("hours")} type="number" min="0" max="23" value={c.hours ?? 0} onChange={num("hours")} /></Field>
      </div>
      <label className="check-row"><input type="checkbox" checked={!!c.business_hours} onChange={(e) => set("business_hours", e.target.checked)} />
        Then wait until business hours (Mon–Fri 9:00–18:00 IST)</label>
    </>),
    wait_for_reply: () => (<>
      <Field id={id("days")} label="Wait up to (days)"><input id={id("days")} type="number" min="1" max="60" value={c.days ?? ""} onChange={num("days")} /></Field>
      <fieldset className="fb-fieldset"><legend>Paths to show</legend>
        {REPLY_OUTPUTS.map((o) => (
          <label key={o} className="check-row"><input type="checkbox" checked={outputsOf(node, edges).includes(o)}
            onChange={(e) => set("branches", e.target.checked ? [...new Set([...outputsOf(node, edges), o])] : outputsOf(node, edges).filter((x) => x !== o))} />
            {BRANCH_HEADINGS[o]}</label>
        ))}
      </fieldset>
    </>),
    email: () => (<>
      {text("subject", "Subject", { placeholder: "A quick idea for {{company}}" })}
      {text("body", "Email text", { long: true, rows: 8, hint: "Short and honest. Keep {{unsub_link}}: every email needs an unsubscribe." })}
      <Chips names={withVars(EMAIL_PLACEHOLDERS)} onInsert={insert} />
      <details className="section"><summary>Version for leads without a role (optional)</summary>
        {text("subject_no_role", "Subject without a role", { placeholder: "For {{company}}" })}
        {text("body_no_role", "Text without a role", { long: true, rows: 5 })}
      </details>
    </>),
    whatsapp: () => select("template", "Approved template", templates.map((t) => [t, pretty(t)]), "Sent only to leads who said yes (consent recorded)."),
    sms: () => (<>
      {text("text", "SMS text", { long: true, rows: 4, hint: "Must match your DLT-approved template. Include an opt-out." })}
      <Chips names={withVars(TEXT_PLACEHOLDERS)} onInsert={insert} />
      {text("template_id", "DLT template id (optional)")}
    </>),
    linkedin_task: () => (<>
      {select("mode", "What to do", [["task", "One task with my message"], ["start_sequence", "Start the LinkedIn sequence (needs their profile link)"]])}
      {(c.mode || "task") === "task" && (<>{text("message", "Message for the person to send by hand", { long: true, rows: 4, hint: "Connection notes: 300 characters or fewer." })}
        <Chips names={withVars(TEXT_PLACEHOLDERS)} onInsert={insert} /></>)}
    </>),
    call_task: () => (<>{owner("owner", "Who calls")}{text("note", "Note for the caller", { long: true, rows: 3, placeholder: "They said yes. Call through PD only, before 18:55." })}</>),
    meeting_link: () => (<>{owner("owner", "Who sends the link")}{text("note", "Note (optional)", { long: true, rows: 3 })}</>),
    set_stage: () => select("stage", "Move to stage", STAGE_ORDER.map((s) => [s, stageLabel(s)]), "Only if the stage rules allow it; closed leads stay closed."),
    add_tag: () => text("tag", "Tag", { placeholder: "diwali-offer" }),
    notify: () => (<>{owner("to", "Who to notify")}{text("message", "Message", { long: true, rows: 3, placeholder: "No meeting two days after their yes. Follow up today." })}</>),
    webhook: () => (<>
      {text("url", "Address (https only)", { placeholder: "https://hooks.example.com/outreach" })}
      <label className="check-row"><input type="checkbox" checked={!!c.include_contact} onChange={(e) => set("include_contact", e.target.checked)} />
        Include name, email and phone (off: company, stage and city only)</label>
    </>),
  };
  const others = nodes.filter((n) => n.id !== node.id && !isTrigger(n.type));

  return (
    <section className="fb-config card" ref={panelRef} tabIndex={-1} aria-labelledby="fb-config-title" data-testid="node-config">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h3 id="fb-config-title" style={{ margin: 0 }}>{titleOf(node)}</h3>
        <span className="pill">{labelOf(node.type)}</span>
      </div>
      <p className="field-help">{helpOf(node.type)}</p>
      {problems.length > 0 && (
        <div className="tip error" role="alert">{problems.map((p, i) => <div key={i}>{p}</div>)}</div>
      )}
      {text("title", node.type === "note" ? "Heading (optional)" : "Step name (optional)", { placeholder: labelOf(node.type) })}
      {fields[node.type]?.()}
      {node.type !== "note" && !isTrigger(node.type) && node.type !== "stop" && (
        <label className="check-row"><input type="checkbox" checked={!!c.disabled} onChange={(e) => set("disabled", e.target.checked)} />
          Turn this step off (leads skip it and carry on; shortcut: press D on the canvas)</label>
      )}
      {outputsOf(node, edges).length > 0 && (
        <fieldset className="fb-fieldset"><legend>Where each path goes</legend>
          {outputsOf(node, edges).map((label) => {
            const current = edges.find((e) => e.from === node.id && e.label === label);
            return (
              <Field key={label} id={id(`out-${label}`)} label={node.type === "switch" ? (label === "other" ? "Anything else" : `If it is “${outputLabel(node, label)}”`) : BRANCH_HEADINGS[label] || outputLabel(node, label)}>
                <select id={id(`out-${label}`)} value={current?.to || ""} onChange={(e) => onConnect(label, e.target.value || null)}>
                  <option value="">— end here (not connected) —</option>
                  {others.map((n) => <option key={n.id} value={n.id}>{titleOf(n)} ({n.id})</option>)}
                </select>
              </Field>
            );
          })}
        </fieldset>
      )}
      <div className="row" style={{ marginTop: 12 }}>
        {onDuplicate && !isTrigger(node.type) && <button type="button" className="btn ghost" onClick={onDuplicate}>Duplicate (Ctrl+D)</button>}
        <button type="button" className="btn ghost danger" onClick={onDelete}>Delete this step</button>
      </div>
    </section>
  );
}
