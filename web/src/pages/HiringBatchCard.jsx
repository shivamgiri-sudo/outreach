import { useEffect, useRef, useState } from "react";
import { isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { Button, Card, CardHeader } from "../ui/index.js";
import RecrawlSettings from "./RecrawlSettings.jsx";

// Owners: read the career pages of up to 50 lead companies in the background (each site at most once a week).
export default function HiringBatchCard() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const poll = () => ops.hiringBatchState().then((s) => {
    setState(s);
    if (s.status === "running") timer.current = setTimeout(poll, 3000);
  }).catch((e) => setError(e.message));
  useEffect(() => { if (isOwner()) poll(); return () => clearTimeout(timer.current); }, []);
  if (!isOwner()) return null;
  const start = async () => {
    setError("");
    try { const s = await ops.hiringBatchStart(50); setState(s); if (s.status === "running") timer.current = setTimeout(poll, 3000); }
    catch (e) { setError(e.message); }
  };
  const running = state?.status === "running";
  return (
    <Card as="section" aria-label="Hiring now from career pages">
      <CardHeader title="Hiring now from career pages"
        description="Reads the jobs your lead companies publish on their own websites (the markup Google for Jobs uses) and fills empty company details from the same pages. Up to 50 websites per run; a website is read at most once a week. Job boards and LinkedIn are never read."
        actions={<Button variant="secondary" onClick={start} disabled={running}>{running ? "Reading websites…" : "Check 50 websites"}</Button>} />
      {state && state.status !== "idle" && (
        <p className="sub" role="status">
          {running ? `Working: ${state.done} of ${state.total} websites read.` : state.note || `Last run: ${state.done} website(s) read, ${state.hiring} hiring now, ${state.filled} empty field(s) filled${state.errors ? `, ${state.errors} could not be read` : ""}.`}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <RecrawlSettings />
    </Card>
  );
}
