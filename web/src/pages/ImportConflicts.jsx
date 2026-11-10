// After an import: "N rows skipped: already owned by someone else", opening the list of those rows.
// Hidden when there are none or the server does not show them to this login.
import { useEffect, useState } from "react";
import { api, canWrite } from "../api.js";
import { Modal } from "./setup/common.jsx";

export default function ImportConflicts({ importId }) {
  const [rows, setRows] = useState([]);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!importId || !canWrite()) return;
    api.importConflicts(importId).then((r) => setRows(r.conflicts || [])).catch(() => {});
  }, [importId]);
  if (!rows.length) return null;
  return (
    <>
      <p>
        <button type="button" className="linkish" onClick={() => setOpen(true)} data-testid="import-conflicts-link">
          {rows.length} row{rows.length === 1 ? "" : "s"} skipped: already owned by someone else
        </button>
      </p>
      {open && (
        <Modal title="Rows skipped: already owned" description="These people already belong to someone else. They keep them; nothing was changed." onClose={() => setOpen(false)} testId="import-conflicts">
          <div className="table-scroll"><table>
            <thead><tr><th>Row in file</th><th>Lead</th><th>Owned by</th></tr></thead>
            <tbody>{rows.map((c) => <tr key={`${c.row}-${c.lead_id}`}><td>{c.row}</td><td>{c.lead_id}</td><td>{c.owner}</td></tr>)}</tbody>
          </table></div>
          <div className="row" style={{ marginTop: 12, justifyContent: "flex-end" }}><button className="btn" onClick={() => setOpen(false)}>Close</button></div>
        </Modal>
      )}
    </>
  );
}
