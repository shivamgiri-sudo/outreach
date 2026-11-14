import { isOwner } from "../api.js";
import { Button } from "../ui/index.js";

// The action bar that appears when people are picked in Prospect search.
export default function ProspectBulkBar({ count, busy, canAdd, onAdd, onList, onStart, onFind, onExport, onClear }) {
  return (
    <div className="bulkbar" data-testid="prospect-bulkbar">
      <b>{count} picked</b>
      {canAdd && <Button size="sm" disabled={busy} onClick={onAdd}>Add to leads</Button>}
      {canAdd && <Button size="sm" variant="secondary" disabled={busy} onClick={onList}>Add to list…</Button>}
      {canAdd && <Button size="sm" variant="secondary" disabled={busy} onClick={onStart}>Add & start sequence</Button>}
      {canAdd && <Button size="sm" variant="secondary" disabled={busy} onClick={onFind}>Find emails…</Button>}
      {isOwner() && <Button size="sm" variant="secondary" disabled={busy} onClick={onExport}>Export CSV</Button>}
      <Button size="sm" variant="ghost" onClick={onClear}>Clear</Button>
    </div>
  );
}
