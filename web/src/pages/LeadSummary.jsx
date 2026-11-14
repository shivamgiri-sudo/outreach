import { useEffect, useState } from "react";
import { api, isOwner } from "../api.js";
import { Button, SummaryStrip } from "../ui/index.js";

// A plain link can't send the login token, so fetch the file with it and save it.
async function downloadApolloList() {
  const blob = await api.download("/api/enrich/apollo-export");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "apollo-enrich-list.csv"; a.click();
  URL.revokeObjectURL(a.href);
}

// One slim line above the table: how complete the data is, and the data actions (Find & enrich is the single entry point here).
export default function LeadSummary({ quality, go, onFind, setMessage, load }) {
  const [enriching, setEnriching] = useState(false);
  const [apolloReady, setApolloReady] = useState(false);
  useEffect(() => {
    api.integrations().then((list) => setApolloReady(list.some((i) => i.id === "apollo" && i.mode === "api" && i.status === "configured"))).catch(() => {});
  }, []);

  const runApollo = async () => {
    try {
      const r = await api.enrichApollo(10);
      setMessage(`Apollo: checked ${r.checked}, matched ${r.matched}, ${r.errors} errors, about ${r.credits_used_estimate} credits used.`);
      load();
    } catch (e) { setMessage(e.message); }
  };
  const runEnrich = async () => {
    setEnriching(true);
    try {
      const r = await api.enrich(20);
      setMessage(`Checked ${r.checked} company websites, found new contact details for ${r.enriched}. ${r.skipped_no_website} have no website yet.`);
      load();
    } catch (e) { setMessage(e.message); }
    setEnriching(false);
  };

  if (!quality || quality.total === 0) return null;
  return (
    <SummaryStrip actions={<>
      {isOwner() && <Button size="sm" onClick={onFind}>Find &amp; enrich leads</Button>}
      {isOwner() && <Button variant="secondary" size="sm" disabled={enriching || quality.enrichable_now === 0} onClick={runEnrich}>
        {enriching ? "Checking websites…" : `Fill gaps from company websites (${quality.enrichable_now})`}
      </Button>}
      {apolloReady && isOwner() && <Button variant="secondary" size="sm" title="Uses up to 10 Apollo credits" onClick={runApollo}>Look up emails in Apollo (10)</Button>}
      {isOwner() && <Button variant="secondary" size="sm" onClick={downloadApolloList}>Download list for Apollo</Button>}
      {go && <Button variant="ghost" size="sm" onClick={() => go("hygiene")}>Duplicates and clean-up</Button>}
    </>}>
      Of <b>{quality.total}</b> companies: <b>{quality.missing_email}</b> have no email, <b>{quality.missing_phone}</b> no phone,{" "}
      <b>{quality.missing_decision_maker}</b> no decision maker, <b>{quality.missing_website}</b> no website.
    </SummaryStrip>
  );
}
