// Import wizard step "Duplicates found": which rows are already in the app or repeat in the file, checked before anything is saved.
// The choice maps to the import's own behaviour: skip those rows, or let the import fill empty fields of the lead you already have.
import { useEffect, useState } from "react";
import { api } from "../api.js";

const KIND = { exists: "Already in the app", in_file: "Repeats an earlier row", likely: "Looks like a lead you have" };

export default function ImportDuplicates({ body, choice, setChoice }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => { api.importDuplicates(body).then(setData).catch((e) => setErr(e.message)); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  if (err) return <div className="tip error" role="alert">{err}</div>;
  if (!data) return <p className="sub">Checking for duplicates…</p>;
  const c = data.counts;
  return (
    <div data-testid="import-duplicates">
      <p className="sub" style={{ margin: "0 0 8px" }}>Checked {data.total} rows. Nothing has been saved.</p>
      <div className="kpi-row">
        {Object.keys(KIND).map((k) => <div key={k} className="kpi"><b>{c[k]}</b><span>{KIND[k]}</span></div>)}
      </div>
      {data.flagged === 0 ? <p>No duplicates found.</p> : (
        <>
          <div className="table-scroll"><table>
            <thead><tr><th>Row</th><th>What we found</th><th>Matches</th><th>Why</th></tr></thead>
            <tbody>{data.rows.map((r) => (
              <tr key={r.row}><td>{r.row}</td><td>{KIND[r.kind]}</td>
                <td>{r.match_lead_id ? <a href={`#leads?lead=${r.match_lead_id}`}>{r.match_company || "Open lead"}{r.owner ? ` (${r.owner})` : ""}</a> : r.kind === "in_file" ? `Row ${r.same_as_row}` : "A lead you cannot open"}</td>
                <td>{r.matched_by || ""}</td></tr>))}</tbody>
          </table></div>
          {data.flagged > data.shown && <p className="sub">Showing the first {data.shown} of {data.flagged}.</p>}
          <fieldset style={{ marginTop: 10, border: 0, padding: 0 }}>
            <legend>What should the import do with them?</legend>
            <label className="row" style={{ margin: "4px 0" }}><input type="radio" name="dup-choice" checked={choice === "skip"} onChange={() => setChoice("skip")} data-testid="dup-skip" /> Skip duplicates (recommended)</label>
            <label className="row" style={{ margin: "4px 0" }}><input type="radio" name="dup-choice" checked={choice === "fill"} onChange={() => setChoice("fill")} data-testid="dup-fill" /> Update empty fields of the existing lead (nothing already filled in is changed)</label>
            <small className="sub">Rows marked "Looks like a lead you have" are never skipped, because the email is different.</small>
          </fieldset>
        </>
      )}
    </div>
  );
}
