import { useEffect, useState } from "react";
import { ops } from "../opsApi.js";
import PeriodPicker, { DeltaTile, usePeriod } from "../PeriodPicker.jsx";

// Dashboard tiles for a chosen period, each with the change against the period just before it.
export default function DashboardPeriod({ go }) {
  const [period, setPeriod] = usePeriod();
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => { setError(""); ops.overview(period).then(setData).catch((e) => setError(e.message)); }, [JSON.stringify(period)]);
  const k = data?.kpis;
  return (
    <section style={{ marginTop: 16 }} data-testid="dashboard-period">
      <PeriodPicker value={period} onChange={setPeriod} range={data?.range} />
      {error && <div className="tip error" role="alert">{error}</div>}
      {k && (
        <div className="grid g4 kpis">
          <DeltaTile label="New leads" kpi={k.new_leads} tone="tone-blue" />
          <DeltaTile label="Emails sent" kpi={k.emails_sent} />
          <DeltaTile label="Replied" kpi={k.replied} tone="tone-violet" />
          <DeltaTile label="Meetings" kpi={k.meetings} tone="tone-green" />
        </div>
      )}
      {go && <p className="sub" style={{ marginTop: 6 }}><button className="linkish" onClick={() => go("analytics")}>See all numbers in Analytics</button></p>}
    </section>
  );
}
