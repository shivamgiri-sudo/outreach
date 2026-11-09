import { useEffect, useState } from "react";
import { api, getRole, isOwner } from "../api.js";
import { istLabel } from "../time.js";
import { saveBlob } from "../DownloadButton.jsx";
import { accountName, forgetLinkedinAccounts } from "./LinkedInAccountPick.jsx";

// Owner setting: each lead owner's default LinkedIn account (a lead with no account of its own uses its owner's).
function OwnerDefaults({ accounts, onMessage }) {
  const [owners, setOwners] = useState([]);
  const [map, setMap] = useState({});
  useEffect(() => {
    api.leadOwners().then((r) => setOwners(r.owners || [])).catch(() => {});
    api.linkedinAccounts().then((r) => setMap(r.owner_defaults || {})).catch(() => {});
  }, []);
  if (!accounts.length || !owners.length) return null;
  const save = async () => {
    try {
      const r = await api.saveLinkedinOwnerAccounts(map);
      setMap(r.owner_defaults); forgetLinkedinAccounts(); onMessage("Default LinkedIn accounts saved.");
    } catch (e) { onMessage(e.message); }
  };
  return (
    <details className="section" data-testid="li-owner-defaults">
      <summary>Default LinkedIn account per lead owner</summary>
      <p className="sub">A lead sends from its owner's account unless a person picked an account for that lead. Leads with neither go to whichever account takes them first.</p>
      <table><thead><tr><th>Lead owner</th><th>Sends from</th></tr></thead><tbody>
        {owners.map((o) => (
          <tr key={o}><td>{o}</td><td>
            <select aria-label={`Default LinkedIn account for ${o}`} value={map[o] || ""} disabled={!isOwner()}
              onChange={(e) => setMap({ ...Object.fromEntries(Object.entries(map).filter(([k]) => k !== o)), ...(e.target.value ? { [o]: e.target.value } : {}) })}>
              <option value="">Any account</option>
              {accounts.map((a) => <option key={a.account} value={a.account}>{accountName(accounts, a.account)}</option>)}
            </select></td></tr>))}
      </tbody></table>
      {isOwner() && <button className="btn" style={{ marginTop: 8 }} onClick={save}>Save defaults</button>}
    </details>
  );
}

function StageList({ stage, account, owner, accounts, onMessage }) {
  const [items, setItems] = useState(null);
  useEffect(() => {
    setItems(null);
    api.linkedinStages({ stage, account, owner }).then((r) => setItems(r.items || [])).catch((e) => onMessage(e.message));
  }, [stage, account, owner]);
  const canExport = isOwner() || getRole() === "admin";
  const download = async () => {
    const q = new URLSearchParams(Object.entries({ stage, account, owner }).filter(([, v]) => v));
    try {
      const { blob, filename } = await api.download(`/api/linkedin/stages.csv?${q}`, { withName: true });
      saveBlob(blob, filename || `linkedin-${stage}.csv`);
    } catch (e) { onMessage(e.message); }
  };
  if (!items) return <p className="sub">Loading…</p>;
  return (
    <div className="table-wrap" style={{ marginTop: 8 }} data-testid="li-stage-list">
      {canExport && <button className="btn ghost" onClick={download} data-testid="li-stage-csv">Download CSV</button>}
      {items.length === 0 ? <p className="sub">Nobody here.</p> : (
        <table><thead><tr><th>Company</th><th>Person</th><th>Account</th><th>Owner</th><th>Last step</th><th>Next due</th></tr></thead>
          <tbody>{items.map((i) => (
            <tr key={i.lead_id}>
              <td><a href={`#leads?lead=${i.lead_id}`}>{i.company || "—"}</a></td>
              <td>{i.person || "—"}{i.title ? <><br /><small>{i.title}</small></> : null}</td>
              <td>{i.account ? accountName(accounts, i.account) : <small className="sub">any account</small>}</td>
              <td>{i.owner || <small className="sub">—</small>}</td>
              <td>{i.last_step ? `Step ${i.last_step}` : "—"}{i.last_step_at && <><br /><small>{istLabel(i.last_step_at)}</small></>}
                {i.stopped_reason && <><br /><small>Reason: {i.stopped_reason}</small></>}</td>
              <td>{i.next_due ? istLabel(i.next_due) : "—"}</td>
            </tr>))}</tbody></table>)}
    </div>
  );
}

// LinkedIn page section: where every lead with a LinkedIn profile stands, per stage and per account.
export default function LinkedInStageBoard({ onMessage }) {
  const [board, setBoard] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [account, setAccount] = useState("");
  const [owner, setOwner] = useState("");
  const [open, setOpen] = useState("");
  useEffect(() => { api.linkedinAccounts().then((r) => setAccounts(r.accounts || [])).catch(() => {}); }, []);
  useEffect(() => { api.linkedinStages({ account, owner }).then(setBoard).catch((e) => onMessage(e.message)); }, [account, owner]);
  if (!board) return null;
  const cols = board.accounts;
  return (
    <section className="card" style={{ marginBottom: 14 }} data-testid="li-stage-board">
      <div className="row" style={{ justifyContent: "space-between" }}>
        <b>LinkedIn stages <span className="pill">{board.total}</span></b>
        <span className="row">
          <select aria-label="Stage board account" value={account} onChange={(e) => setAccount(e.target.value)} style={{ width: "auto" }}>
            <option value="">All accounts</option><option value="__none">Not assigned</option>
            {cols.map((a) => <option key={a} value={a}>{accountName(accounts, a)}</option>)}
          </select>
          <select aria-label="Stage board owner" value={owner} onChange={(e) => setOwner(e.target.value)} style={{ width: "auto" }}>
            <option value="">All owners</option><option value="__none">No owner</option>
            {board.owners.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </span>
      </div>
      <div className="table-wrap">
        <table><thead><tr><th>Stage</th><th>Total</th>{cols.map((a) => <th key={a}>{accountName(accounts, a)}</th>)}<th>Not assigned</th><th>Next due</th></tr></thead>
          <tbody>{board.stages.map((s) => (
            <tr key={s.key} className="click" onClick={() => setOpen(open === s.key ? "" : s.key)} aria-expanded={open === s.key}>
              <td><button className="linkish">{s.label}</button></td><td><b>{s.total}</b></td>
              {cols.map((a) => <td key={a}>{s.by_account[a] || 0}</td>)}<td>{s.by_account[""] || 0}</td>
              <td>{s.next_due ? istLabel(s.next_due) : "—"}</td>
            </tr>))}</tbody></table>
      </div>
      {open && <StageList stage={open} account={account} owner={owner} accounts={accounts} onMessage={onMessage} />}
      <OwnerDefaults accounts={accounts} onMessage={onMessage} />
    </section>
  );
}
