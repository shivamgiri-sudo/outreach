// Setup > Assignment (owners and workspace admins): teams, ordered rules, default team, first-touch minutes and
// switches, "Run on unowned leads", and the ownership-mode switch.
import { useEffect, useId, useState } from "react";
import { api, isOwner, isWorkspaceAdmin } from "../../api.js";
import OwnershipMode from "./OwnershipMode.jsx";

const WHEN = [["source", "Source"], ["city", "City"], ["branch", "Branch"], ["campaign", "Campaign"], ["tag", "Tag"]];
const uid = (p) => `${p}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;

function Team({ team, owners, onChange, onRemove }) {
  const id = useId();
  const toggle = (n) => onChange({ ...team, members: team.members.includes(n) ? team.members.filter((x) => x !== n) : [...team.members, n] });
  return (
    <div className="card" style={{ marginBottom: 10 }} data-testid="team-card">
      <div className="row" style={{ flexWrap: "wrap", gap: 8 }}>
        <label htmlFor={`${id}-n`} className="sr-only">Team name</label>
        <input id={`${id}-n`} placeholder="Team name" value={team.name} onChange={(e) => onChange({ ...team, name: e.target.value })} style={{ flex: 1, minWidth: 160 }} />
        <label className="row" style={{ margin: 0, gap: 6 }}>Daily cap
          <input type="number" min="0" aria-label={`Daily cap for ${team.name || "team"}`} style={{ width: 80 }} value={team.daily_cap ?? ""} onChange={(e) => onChange({ ...team, daily_cap: e.target.value === "" ? 0 : Math.max(0, parseInt(e.target.value, 10) || 0) })} />
        </label>
        <button className="btn ghost" onClick={onRemove} aria-label={`Remove team ${team.name || ""}`}>Remove</button>
      </div>
      <fieldset style={{ border: 0, padding: 0, margin: "8px 0 0" }}>
        <legend className="sub">Members</legend>
        <div className="chips">{owners.map((n) => (
          <label key={n} className="pill rec" style={{ cursor: "pointer", opacity: team.members.includes(n) ? 1 : 0.55 }}>
            <input type="checkbox" checked={team.members.includes(n)} onChange={() => toggle(n)} style={{ marginRight: 4 }} />{n}
          </label>))}
          {owners.length === 0 && <small className="sub">No team members yet. Add them under Team members.</small>}
        </div>
      </fieldset>
    </div>
  );
}

function Rule({ rule, index, last, teams, onChange, onMove, onRemove }) {
  return (
    <div className="card" style={{ marginBottom: 10 }} data-testid="rule-card">
      <div className="row" style={{ flexWrap: "wrap", gap: 8, alignItems: "flex-end" }}>
        <b style={{ minWidth: 24 }}>{index + 1}.</b>
        {WHEN.map(([k, label]) => (
          <label key={k} style={{ flex: "1 1 120px", margin: 0 }}>{label}
            <input value={rule.when?.[k] || ""} onChange={(e) => onChange({ ...rule, when: { ...rule.when, [k]: e.target.value } })} placeholder="any" />
          </label>))}
        <label style={{ flex: "1 1 140px", margin: 0 }}>Goes to team
          <select value={rule.team} onChange={(e) => onChange({ ...rule, team: e.target.value })}>
            <option value="">Choose a team</option>
            {teams.map((t) => <option key={t.id} value={t.id}>{t.name || t.id}</option>)}
          </select>
        </label>
        <button className="btn ghost" disabled={index === 0} onClick={() => onMove(-1)} aria-label={`Move rule ${index + 1} up`}>Up</button>
        <button className="btn ghost" disabled={last} onClick={() => onMove(1)} aria-label={`Move rule ${index + 1} down`}>Down</button>
        <button className="btn ghost" onClick={onRemove} aria-label={`Remove rule ${index + 1}`}>Remove</button>
      </div>
    </div>
  );
}

export default function Assignment() {
  if (!isOwner() && !isWorkspaceAdmin()) return <p className="empty">Only owners and workspace admins can change assignment.</p>;
  return <AssignmentBody />;
}

function AssignmentBody() {
  const [cfg, setCfg] = useState(null);
  const [owners, setOwners] = useState([]);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const load = () => api.assignment().then(setCfg).catch((e) => setMsg({ text: e.message, error: true }));
  useEffect(() => { load(); api.leadOwners().then((r) => setOwners(r.owners || [])).catch(() => {}); }, []);
  if (!cfg) return <div className="card setup-card">{msg ? <div className="tip error" role="alert">{msg.text}</div> : "Loading…"}</div>;
  const set = (patch) => setCfg({ ...cfg, ...patch });
  const ft = cfg.first_touch || {};
  const setFt = (patch) => set({ first_touch: { ...ft, ...patch } });
  const teams = cfg.teams || [];
  const rules = cfg.rules || [];
  const setTeam = (i, t) => set({ teams: teams.map((x, j) => (j === i ? t : x)) });
  const setDefault = (id) => set({ teams: teams.map((t) => ({ ...t, default: t.id === id })) });
  const move = (i, d) => { const r = [...rules]; [r[i], r[i + d]] = [r[i + d], r[i]]; set({ rules: r }); };
  const invalid = rules.some((r) => !r.team) || teams.some((t) => !t.name.trim());
  const save = async () => {
    setBusy(true); setMsg(null);
    try {
      const clean = { ...cfg, rules: rules.map((r) => ({ ...r, when: Object.fromEntries(Object.entries(r.when || {}).filter(([, v]) => String(v).trim()).map(([k, v]) => [k, String(v).trim()])) })) };
      setCfg(await api.saveAssignment(clean)); setMsg({ text: "Saved." });
    } catch (e) { setMsg({ text: e.message, error: true }); }
    setBusy(false);
  };
  const runUnowned = async () => {
    setBusy(true); setMsg(null);
    try { const r = await api.runAssignmentOnUnowned(); setMsg({ text: `Assigned ${r.assigned} lead(s); ${r.left} left without an owner.` }); load(); } catch (e) { setMsg({ text: e.message, error: true }); }
    setBusy(false);
  };
  return (
    <div className="setup-stack" data-testid="assignment-setup">
      <section className="card setup-card">
        <h3 style={{ marginTop: 0 }}>Assign new leads</h3>
        <label className="row" style={{ gap: 8 }}><input type="checkbox" checked={!!cfg.on} onChange={(e) => set({ on: e.target.checked })} data-testid="assignment-on" /> Give every new lead an owner automatically (first matching rule, then round-robin inside the team)</label>
        <p className="sub">A lead that matches no rule goes to the default team. {cfg.unowned ?? 0} lead(s) have no owner now.</p>
        <div className="row" style={{ flexWrap: "wrap" }}>
          <button className="btn ghost" disabled={busy || !cfg.unowned} onClick={runUnowned} data-testid="run-unowned">Run on unowned leads</button>
        </div>
      </section>

      <section className="card setup-card" aria-labelledby="as-teams">
        <h3 id="as-teams" style={{ marginTop: 0 }}>Teams</h3>
        {teams.map((t, i) => (
          <div key={t.id}>
            <Team team={t} owners={owners} onChange={(x) => setTeam(i, x)} onRemove={() => set({ teams: teams.filter((_, j) => j !== i), rules: rules.filter((r) => r.team !== t.id) })} />
            <label className="row" style={{ gap: 8, margin: "-4px 0 10px" }}><input type="radio" name="default-team" checked={!!t.default} onChange={() => setDefault(t.id)} /> Default team (gets leads that match no rule)</label>
          </div>))}
        {teams.length === 0 && <p className="empty">No teams yet.</p>}
        <button className="btn ghost" onClick={() => set({ teams: [...teams, { id: uid("team"), name: "", members: [], default: teams.length === 0, daily_cap: 0 }] })}>Add team</button>
      </section>

      <section className="card setup-card" aria-labelledby="as-rules">
        <h3 id="as-rules" style={{ marginTop: 0 }}>Rules</h3>
        <p className="sub">Checked from top to bottom; the first rule whose filled boxes all match decides the team. Empty boxes match anything.</p>
        {rules.map((r, i) => (
          <Rule key={r.id} rule={r} index={i} last={i === rules.length - 1} teams={teams}
            onChange={(x) => set({ rules: rules.map((y, j) => (j === i ? x : y)) })} onMove={(d) => move(i, d)} onRemove={() => set({ rules: rules.filter((_, j) => j !== i) })} />))}
        {rules.length === 0 && <p className="empty">No rules. Every lead goes to the default team.</p>}
        <button className="btn ghost" disabled={!teams.length} onClick={() => set({ rules: [...rules, { id: uid("rule"), when: {}, team: teams.find((t) => t.default)?.id || teams[0].id }] })}>Add rule</button>
      </section>

      <section className="card setup-card" aria-labelledby="as-ft">
        <h3 id="as-ft" style={{ marginTop: 0 }}>First touch</h3>
        <p className="sub">The clock runs in working hours. A first touch is an email, WhatsApp or LinkedIn message sent, a call logged, a note or a meeting.</p>
        <div className="row" style={{ flexWrap: "wrap", gap: 16 }}>
          <label className="row" style={{ gap: 8, margin: 0 }}><input type="checkbox" checked={!!ft.alert} onChange={(e) => setFt({ alert: e.target.checked })} data-testid="ft-alert" /> Alert the owner after
            <input type="number" min="1" aria-label="Alert after minutes" style={{ width: 80 }} value={ft.alert_minutes ?? ""} onChange={(e) => setFt({ alert_minutes: Math.max(1, parseInt(e.target.value, 10) || 1) })} /> minutes</label>
          <label className="row" style={{ gap: 8, margin: 0 }}><input type="checkbox" checked={!!ft.reassign} onChange={(e) => setFt({ reassign: e.target.checked })} data-testid="ft-reassign" /> Move to the next person after
            <input type="number" min="1" aria-label="Reassign after minutes" style={{ width: 80 }} value={ft.reassign_minutes ?? ""} onChange={(e) => setFt({ reassign_minutes: Math.max(1, parseInt(e.target.value, 10) || 1) })} /> minutes</label>
        </div>
      </section>

      <div className="row" style={{ flexWrap: "wrap" }}>
        <button className="btn" disabled={busy || invalid} onClick={save} data-testid="assignment-save">Save assignment</button>
        {invalid && <small className="sub">Give every team a name and every rule a team.</small>}
        {msg && <span className={`tip${msg.error ? " error" : ""}`} role={msg.error ? "alert" : "status"}>{msg.text}</span>}
      </div>

      <OwnershipMode />
    </div>
  );
}
