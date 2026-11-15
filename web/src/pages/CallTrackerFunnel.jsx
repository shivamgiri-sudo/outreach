// Sales funnel: real calls from DialHalo on the leads in view (Integrations → DialHalo). Hidden until connected.
import { useEffect, useState } from "react";
import { api } from "../api.js";
import { Card, CardHeader, Stat, Stats } from "../ui/index.js";

const TILES = [
  ["sent_total", "Sent to DialHalo", "Leads in a caller's dial queue (all time)"],
  ["called", "Called", "Leads called at least once"],
  ["connected", "Connected", "Leads with a connected call"],
  ["interested", "Interested", "Said yes on a call"],
  ["meeting_booked", "Meeting booked", "Outcome mapped to meeting booked"],
  ["calls", "Calls", "Every call, connected or not"],
];

export default function CallTrackerFunnel({ from, to }) {
  const [data, setData] = useState(null);
  useEffect(() => {
    let live = true;
    api.calltrackerFunnel(from, to).then((d) => live && setData(d)).catch(() => live && setData(null));
    return () => { live = false; };
  }, [from, to]);
  if (!data || (!data.connected_workspaces && !data.sent_total && !data.calls)) return null;
  return (
    <Card as="section" className="sf-section-gap" data-testid="ct-funnel">
      <CardHeader title="Calls from DialHalo" description={`Real calls synced from callers' phones${from || to ? ` (${from || "start"} to ${to || "today"}, IST)` : ""}.`} />
      <Stats>
        {TILES.map(([key, label, hint]) => (
          <Stat key={key} value={data[key] ?? 0} label={label} title={hint} data-testid={`ct-funnel-${key}`} />
        ))}
      </Stats>
    </Card>
  );
}
