import { useRef } from "react";
import { STAGE_ORDER, stageLabel } from "../../labels.js";
import {
  ACTIVATION_EVENTS, BRANCH_HEADINGS, EMAIL_PLACEHOLDERS, IF_FIELDS, IF_OPS, OUTPUT_LABELS, PERSONAS, REPLY_LABELS, REPLY_OUTPUTS,
  TEXT_PLACEHOLDERS, helpOf, isTrigger, labelOf, opsFor, outputsOf, titleOf,
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
export default function NodeConfig({ node, nodes, edges, catalog, problems, onChange, onConnect, onDelete, panelRef }) {
  const c = node.config || {};
  const lastText = useRef({ key: null, el: null });
  const team = catalog?.team?.length ? catalog.team : [catalog?.caller || "Priya"];
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
  const owner = (key, label) => select(key, label, [["", `${catalog?.caller || "Priya"} (the caller)`], ...team.filter((t) => t !== catalog?.caller).map((t) => [t, t])]);
  const kind = IF_FIELDS[c.field]?.[1] || "text";
  const valueInput = () => {
    if (c.op === "exists") return null;
    if (kind === "bool") return select("value", "Value", [["true", "Yes"], ["false", "No"]]);
    if (kind === "stage" && c.op !== "in") return select("value", "Value", STAGE_ORDER.map((s) => [s, stageLabel(s)]));
    if (kind === "band") return select("value", "Value", [["hot", "Hot"], ["warm", "Warm"], ["cold", "Cold"]]);
    if (kind === "persona" && c.op !== "in") return select("value", "Value", PERSONAS.map((p) => [p, pretty(p)]));
    const hint = c.op === "in" ? "Separate several values with commas." : undefined;
    const placeholder = { number: "60", text: c.field === "city" ? "Pune, Bengaluru, Gurugram" : c.field === "country" ? "IN" : "", tags: "diwali-hiring" }[kind] || "";
    return text("value", "Value", { hint, placeholder });
  };

  const fields = {
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
      {text("subject", "Subject", { placeholder: "{{role}} hiring at {{company}}" })}
      {text("body", "Email text", { long: true, rows: 8, hint: "Short and honest. Keep {{unsub_link}}: every email needs an unsubscribe." })}
      <Chips names={EMAIL_PLACEHOLDERS} onInsert={insert} />
      <details className="section"><summary>Version for leads without a role (optional)</summary>
        {text("subject_no_role", "Subject without a role", { placeholder: "Hiring at {{company}}" })}
        {text("body_no_role", "Text without a role", { long: true, rows: 5 })}
      </details>
    </>),
    whatsapp: () => select("template", "Approved template", templates.map((t) => [t, pretty(t)]), "Sent only to leads who said yes (consent recorded)."),
    sms: () => (<>
      {text("text", "SMS text", { long: true, rows: 4, hint: "Must match your DLT-approved template. Include an opt-out." })}
      <Chips names={TEXT_PLACEHOLDERS} onInsert={insert} />
      {text("template_id", "DLT template id (optional)")}
    </>),
    linkedin_task: () => (<>
      {select("mode", "What to do", [["task", "One task with my message"], ["start_sequence", "Start the LinkedIn sequence (needs their profile link)"]])}
      {(c.mode || "task") === "task" && (<>{text("message", "Message for the person to send by hand", { long: true, rows: 4, hint: "Connection notes: 300 characters or fewer." })}
        <Chips names={TEXT_PLACEHOLDERS} onInsert={insert} /></>)}
    </>),
    call_task: () => (<>{owner("owner", "Who calls")}{text("note", "Note for the caller", { long: true, rows: 3, placeholder: "They said yes. Call through PD only, before 18:55." })}</>),
    meeting_link: () => (<>{owner("owner", "Who sends the link")}{text("note", "Note (optional)", { long: true, rows: 3 })}</>),
    set_stage: () => select("stage", "Move to stage", STAGE_ORDER.map((s) => [s, stageLabel(s)]), "Only if the stage rules allow it; closed leads stay closed."),
    add_tag: () => text("tag", "Tag", { placeholder: "diwali-hiring" }),
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
      {text("title", "Step name (optional)", { placeholder: labelOf(node.type) })}
      {fields[node.type]?.()}
      {outputsOf(node, edges).length > 0 && (
        <fieldset className="fb-fieldset"><legend>Where each path goes</legend>
          {outputsOf(node, edges).map((label) => {
            const current = edges.find((e) => e.from === node.id && e.label === label);
            return (
              <Field key={label} id={id(`out-${label}`)} label={BRANCH_HEADINGS[label] || OUTPUT_LABELS[label] || label}>
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
        <button type="button" className="btn ghost danger" onClick={onDelete}>Delete this step</button>
      </div>
    </section>
  );
}
