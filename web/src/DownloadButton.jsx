import { useState } from "react";
import { api, isOwner } from "./api.js";

// "/api/export/leads.csv?stage=new" from a dataset name and filters (empty filters are left out).
export function exportPath(dataset, params = {}, format = "csv") {
  const query = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false));
  const text = query.toString();
  return `/api/export/${dataset}.${format}${text ? `?${text}` : ""}`;
}

// Hands a downloaded file to the browser under the given name.
export function saveBlob(blob, filename) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// A button that downloads a file from the API with the login token, shows "Preparing…" meanwhile, and says what went wrong.
// `body` makes it a POST (for long filters such as picked lead ids). The server's file name wins over `filename`.
export default function DownloadButton({ path, body, filename = "download.csv", label = "Download CSV", className = "btn ghost", title, disabled, testId, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!isOwner()) return null;                       // downloads are owner-only (the server refuses callers and viewers)
  const run = async () => {
    setBusy(true); setError("");
    try {
      const { blob, filename: served } = await api.download(path, { body, withName: true });
      saveBlob(blob, served || filename);
      onDone?.(served || filename);
    } catch (e) {
      setError(e.message === "Failed to fetch" ? "The app server is not answering. Try again." : e.message);
    }
    setBusy(false);
  };
  return (
    <span className="dl">
      <button type="button" className={className} disabled={busy || disabled} onClick={run} title={title} data-testid={testId} aria-busy={busy}>
        {busy ? "Preparing…" : label}
      </button>
      {error && <span className="dl-error" role="alert">{error}</span>}
    </span>
  );
}
