import { isOwner } from "../../api.js";

// Whose template this is. Owners see every template's workspace; a workspace login sees "Main · read-only" on the
// shared main ones (it copies them into its own workspace to change them) and its own workspace's name otherwise.
export default function WorkspaceTag({ row }) {
  if (!row || !row.workspace_id) return null;
  if (isOwner()) return <span className="pill" data-testid="ws-tag">{row.workspace_name || row.workspace_id}</span>;
  if (row.workspace_id === "main") return <span className="pill" data-testid="ws-tag">Main · read-only</span>;
  return <span className="pill ok" data-testid="ws-tag">{row.workspace_name || row.workspace_id}</span>;
}
