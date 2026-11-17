import { useEffect, useRef, useState } from "react";
import { isOwner } from "../api.js";
import { ops } from "../opsApi.js";
import { Button, Card, CardHeader } from "../ui/index.js";

// Owners: check the mail records and age of up to 500 lead domains in the background (each domain at most once a month).
export default function DomainBatchCard() {
  const [state, setState] = useState(null);
  const [error, setError] = useState("");
  const timer = useRef(null);
  const poll = () => ops.domainBatchState().then((s) => {
    setState(s);
    if (s.status === "running") timer.current = setTimeout(poll, 3000);
  }).catch((e) => setError(e.message));
  useEffect(() => { if (isOwner()) poll(); return () => clearTimeout(timer.current); }, []);
  if (!isOwner()) return null;
  const start = async () => {
    setError("");
    try { const s = await ops.domainBatchStart(500); setState(s); if (s.status === "running") timer.current = setTimeout(poll, 3000); }
    catch (e) { setError(e.message); }
  };
  const running = state?.status === "running";
  return (
    <Card as="section" className="u-mt-16" aria-label="Domain trust signals" data-testid="domain-batch">
      <CardHeader title="Domain trust signals"
        description="Checks each lead company's public mail records (MX, SPF, DMARC) and when its domain was registered. Very new domains, or domains with no SPF / DMARC, more often belong to a fake, inactive or brand-new company, and email to them bounces more often. Up to 500 domains per run; a domain is checked at most once a month. Filter the Leads page by Young domain or No SPF/DMARC afterwards."
        actions={<Button variant="secondary" onClick={start} disabled={running}>{running ? "Checking domains…" : "Check 500 domains"}</Button>} />
      {state && state.status !== "idle" && (
        <p className="sub" role="status">
          {running ? `Working: ${state.done} of ${state.total} domains checked.` : state.note || `Last run: ${state.done} domain(s) checked, ${state.young} younger than a year, ${state.no_mail_auth} without SPF or DMARC${state.unknown ? `, ${state.unknown} could not be read` : ""}.`}
        </p>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </Card>
  );
}
