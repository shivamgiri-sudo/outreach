import { useEffect, useState } from "react";
import { api } from "../../api.js";
import { istLabel } from "../../time.js";
import { Badge, Button, Card, CardHeader, Field, Select, Stat, Stats } from "../../ui";

// Integrations → DialHalo → "Outreach match": every number your team dials in DialHalo is checked against this
// workspace's leads, and DialHalo's "Outreach match" page shows which calls were to Outreach leads. Here: what is
// shared, the last send, and "Match past calls" (backfill from this link's DialHalo call history; one run per link).
const STATUS = { running: ["info", "Running"], done: ["success", "Finished"], failed: ["danger", "Stopped with a problem"], stopped: ["warning", "Stopped"] };
const n = (v) => Number(v || 0).toLocaleString("en-IN");

const sentence = (text) => (/[.!?]$/.test(String(text || "").trim()) ? text : `${text}.`);

export default function CallTrackerMatch({ ws, link, match, lastMatch, busy, run, onRefresh }) {
  const [days, setDays] = useState(30);
  const bf = match?.backfill || {};
  const [tone, label] = STATUS[bf.status] || ["neutral", "Not run yet"];
  const running = bf.status === "running";
  useEffect(() => {                                  // progress while a run is going (quietly, every 5 s)
    if (!running) return undefined;
    const t = setInterval(() => onRefresh?.(), 5000);
    return () => clearInterval(t);
  }, [running]);
  const canResume = (bf.status === "failed" || bf.status === "stopped") && bf.days === days && bf.page > 0;
  return (
    <Card data-testid="ct-match">
      <CardHeader title="Outreach match in DialHalo"
        description="For every number your team dials in DialHalo, Outreach says whether it is one of this workspace's leads. DialHalo's Outreach match page then shows matched calls next to numbers that are not in Outreach." />
      <ul className="sub u-m-4-0-8">
        <li>Shared only for numbers your team already dialled: the contact name, company, stage and a link to the lead here. No emails, notes or anything else.</li>
        <li>A number that is not in Outreach yet is remembered for a while. When someone adds or edits a lead with that phone, DialHalo is told within a few minutes.</li>
        <li>People on the do-not-contact list still show as matched (their stage says so). Erasing a person here also removes their match from DialHalo.</li>
      </ul>
      <p className="sub" data-testid="ct-match-last">
        {lastMatch ? <>{lastMatch.ok ? "Last sent" : "Last send failed"} {istLabel(lastMatch.at)}: {sentence(lastMatch.detail)}</> : "Nothing sent yet."}
        {match?.waiting ? ` ${n(match.waiting)} number(s) not in Outreach are being watched for a new lead.` : ""}
      </p>

      <h4 className="u-m-12-0-4">Match past calls</h4>
      <p className="sub">Checks the calls already in DialHalo (up to {n(match?.max_calls)} calls per run) and sends the results the same way. Running it again is safe.</p>
      <div className="row u-gap-8 u-aend">
        <Field label="Calls from the last">
          <Select value={days} onChange={(e) => setDays(Number(e.target.value))} disabled={running || busy} data-testid="ct-backfill-days">
            {(match?.days || [7, 30, 90]).map((d) => <option key={d} value={d}>{d} days</option>)}
          </Select>
        </Field>
        <Button disabled={running || busy} data-testid="ct-backfill"
          onClick={() => run(() => api.backfillCalltracker(ws, days, link))}>
          {running ? "Matching…" : canResume ? "Continue" : "Match past calls"}
        </Button>
        {running && <Button variant="secondary" disabled={busy} onClick={() => onRefresh?.()}>Refresh</Button>}
      </div>

      {bf.status && (
        <div className="u-m-12-0-0" data-testid="ct-backfill-state">
          <p className="sub">
            <Badge tone={tone}>{label}</Badge>{" "}
            Last {bf.days} days{bf.started_at ? `, started ${istLabel(bf.started_at)}` : ""}{bf.by ? ` by ${bf.by}` : ""}
            {bf.finished_at ? `, ended ${istLabel(bf.finished_at)}` : ""}.
          </p>
          <Stats>
            <Stat label="Calls checked" value={n(bf.calls)} hint={bf.total != null ? `of ${n(bf.total)}` : undefined} />
            <Stat label="Matched to a lead" value={n(bf.matched)} tone="green" />
            <Stat label="Not in Outreach" value={n(bf.not_found)} tone="amber" />
          </Stats>
          {bf.error && <p className={`tip ${bf.status === "done" ? "" : "error"}`} role="status">{bf.error}</p>}
        </div>
      )}
    </Card>
  );
}
