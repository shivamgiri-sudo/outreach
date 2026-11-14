// Sales funnel: real calls from DialHalo on the leads in view (Integrations → DialHalo). Hidden until connected.
import { useEffect, useState } from "react";
import { api } from "../api.js";

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
    <section className="card" data-testid="ct-funnel" style={{ marginTop: 16 }}>
      <b>Calls from DialHalo</b>
      <p className="sub">Real calls synced from callers' phones{from || to ? ` (${from || "start"} to ${to || "today"}, IST)` : ""}.</p>
      <div className="grid g4">
        {TILES.map(([key, label, hint]) => (
          <div key={key} className="stat" title={hint} data-testid={`ct-funnel-${key}`}>
            <span className="sub-inline">{label}</span>
            <b>{data[key] ?? 0}</b>
          </div>
        ))}
      </div>
    </section>
  );
}
