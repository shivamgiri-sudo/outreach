// Setup > Account activity (a client account's admins): their own account's change log, including the platform
// team's actions on the account and every "view as" request a super admin made. Server: GET /api/account/audit.
import { useEffect, useState } from "react";
import { platform } from "../../platformApi.js";
import { Status, istDate } from "./common.jsx";

export default function AccountActivity() {
  const [rows, setRows] = useState(null);
  const [message, setMessage] = useState(null);
  useEffect(() => { platform.accountAudit().then((d) => setRows(d.items || [])).catch((e) => setMessage({ text: e.message, error: true })); }, []);
  return (
    <section className="card setup-card" data-testid="account-activity">
      <Status message={message} />
      {!rows ? <p className="hint">Loading…</p> : rows.length === 0 ? <p className="hint">Nothing yet.</p> : (
        <div className="table-wrap"><table className="setup-table">
          <thead><tr><th scope="col">When</th><th scope="col">Who</th><th scope="col">What</th><th scope="col">Detail</th></tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={`${r.at}-${i}`}><td data-label="When">{istDate(r.at)}</td><td data-label="Who">{r.by}</td>
              <td data-label="What">{r.action}</td><td data-label="Detail">{r.detail}</td></tr>))}</tbody>
        </table></div>)}
    </section>
  );
}
