// Setup > Accounts (owners): one account per client company, above its workspaces (server: app/accounts.py).
// Super admins get the console on top: the pending queue (approve / reject / ask), "Stop all sending", figures per
// account (logins, workspaces, leads, sends, last activity, flags), a status filter and the account detail (suspend,
// reactivate, workspace requests, view as, export, delete). Dialogs: AccountDialogs.jsx and console/.
import { useEffect, useState } from "react";
import { api } from "../../api.js";
import Icon from "../../icons.jsx";
import { platform } from "../../platformApi.js";
import { Status, istDate } from "./common.jsx";
import { EditAccount, NewAccount, changed } from "./AccountDialogs.jsx";
import AccountDetail, { AskDialog } from "./console/AccountDetail.jsx";
import { PendingQueue, StatusFilter, StopAllSwitch, flagText } from "./console/ConsoleParts.jsx";

export default function Accounts() {
  const [data, setData] = useState(null);
  const [cons, setCons] = useState(null);           // the super-admin console's rows (null: not a super admin)
  const [filter, setFilter] = useState("");
  const [message, setMessage] = useState(null);
  const [dialog, setDialog] = useState(null);
  const load = () => Promise.all([
    api.accounts().then((d) => { setData(d); changed(); return d; }),
    platform.console(filter).then(setCons).catch(() => setCons(null)),
  ]).then(([d]) => d).catch((e) => setMessage({ text: e.message, error: true }));
  useEffect(() => { load(); }, [filter]);
  const say = (text) => { setMessage({ text }); load().then((d) => {
    if (dialog?.kind === "edit" && d) setDialog({ kind: "edit", row: d.accounts.find((a) => a.id === dialog.row.id) || dialog.row });
    if (dialog?.kind === "detail") setDialog(null);
  }); };
  const fail = (err) => setMessage({ text: err.message, error: true });
  const action = (kind, row) => {
    if (kind === "approve") platform.approve(row.id).then(() => say(`“${row.name}” approved.`)).catch(fail);
    else setDialog({ kind: "text", text: kind, row });
  };
  const manage = (row) => setDialog({ kind: "edit", row: data?.accounts.find((a) => a.id === row.id) || row });
  const figures = Object.fromEntries((cons?.accounts || []).map((a) => [a.id, a]));
  const rows = cons ? cons.accounts : (data?.accounts || []);
  return (
    <section className="card setup-card" aria-labelledby="acct-h">
      <div className="card-head">
        <div><h3 id="acct-h" className="sr-only">Accounts</h3>
          <p className="hint">One account per client company. A client's logins see only their own account; Mas Callnet staff (owners) see every account.</p></div>
        <button className="btn" onClick={() => setDialog({ kind: "new" })} data-testid="create-account"><Icon name="plus" size={16} /> New account</button>
      </div>
      <Status message={message} />
      {cons && <StopAllSwitch stop={cons.stop_all_sending} onChanged={load} />}
      {cons && <PendingQueue rows={cons.accounts} onAction={action} onDetails={(a) => setDialog({ kind: "detail", row: a })} />}
      {cons && <div className="row" style={{ justifyContent: "flex-end", marginBottom: 8 }}><StatusFilter value={filter} onChange={setFilter} /></div>}
      {!data ? <p className="hint">Loading…</p> : (
        <div className="table-wrap">
          <table className="setup-table">
            <thead><tr><th scope="col">Account</th><th scope="col">Status</th>{cons && <th scope="col">Created</th>}<th scope="col" className="num">Workspaces</th>
              <th scope="col" className="num">Logins</th><th scope="col" className="num">Leads</th>
              {cons && <><th scope="col" className="num">Sent today / 7 days</th><th scope="col">Last activity</th><th scope="col">Limits</th><th scope="col">Flags</th></>}
              <th scope="col">Sending</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const a = data.accounts.find((x) => x.id === r.id) || r;
                const f = figures[r.id];
                return (
                  <tr key={r.id} data-testid={`account-${r.name}`}>
                    <td data-label="Account"><b>{r.name}</b>{r.id === "mcn" && <span className="pill tiny">Platform</span>}</td>
                    <td data-label="Status">{r.status}</td>
                    {f && <td data-label="Created">{istDate(f.created_at) || "—"}</td>}
                    <td data-label="Workspaces" className="num">{(r.workspaces || []).length}</td>
                    <td data-label="Logins" className="num">{f ? f.members : a.logins}</td>
                    <td data-label="Leads" className="num">{r.leads}</td>
                    {f && <><td data-label="Sent" className="num">{f.sends_today} / {f.sends_7d}</td><td data-label="Last activity">{istDate(f.last_activity) || "—"}</td>
                      <td data-label="Limits">{Object.entries(f.limits || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(", ") || "—"}</td>
                      <td data-label="Flags">{flagText(f) || "—"}</td></>}
                    <td data-label="Sending">{r.id === "mcn" ? "Main settings" : a.email_hold ? <span className="muted">Held</span> : a.sending_ready ? "Own domain" : "Granted"}</td>
                    <td className="actions">
                      {f && <button className="btn ghost small" onClick={() => setDialog({ kind: "detail", row: f })} aria-label={`Details ${r.name}`}>Details</button>}
                      {data.accounts.some((x) => x.id === r.id) && <button className="btn ghost small" onClick={() => manage(r)} aria-label={`Manage ${r.name}`}>
                        <Icon name="pencil" size={15} /> Manage</button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {dialog?.kind === "new" && <NewAccount onClose={() => setDialog(null)} onDone={say} />}
      {dialog?.kind === "edit" && <EditAccount key={dialog.row.id + JSON.stringify(dialog.row.workspaces)} row={dialog.row} grantable={data?.grantable || []}
        limitKeys={data?.limit_keys || []} onClose={() => setDialog(null)} onDone={say} />}
      {dialog?.kind === "detail" && <AccountDetail row={dialog.row} onClose={() => setDialog(null)} onDone={say} onManage={manage}
        onText={(kind, row) => setDialog({ kind: "text", text: kind, row })} />}
      {dialog?.kind === "text" && <AskDialog row={dialog.row} kind={dialog.text} onClose={() => setDialog(null)} onDone={say} />}
    </section>
  );
}
