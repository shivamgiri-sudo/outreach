// Leads grid filters that came with the lead harvest: minimum rating, hide DND numbers, found by a harvest (any job or one).
// Rules in leadFilters.js (gridMatches) and the same on the server for downloads (app/lead_grid.py).
import { useEffect, useMemo, useState } from "react";
import { harvestApi } from "../harvestApi.js";
import { RATINGS, fromHarvest, isDnd, ratingOf } from "./leadFilters.js";
import { Select } from "../ui/index.js";

export function RatingFilter({ leads, value, onChange }) {
  const count = (min) => leads.filter((l) => ratingOf(l) !== null && ratingOf(l) >= Number(min)).length;
  return (
    <Select aria-label="Minimum rating" value={value} onChange={(e) => onChange(e.target.value)} data-testid="rating-filter">
      <option value="">Any rating (or none)</option>
      {RATINGS.map((r) => <option key={r} value={r}>{r}★ and up ({count(r)})</option>)}
    </Select>
  );
}

export function DndFilter({ leads, value, onChange }) {
  const n = leads.filter(isDnd).length;
  return (
    <label className="check-row" title="Numbers marked DND, or on the do-not-call list uploaded on the Lead harvest page">
      <input type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} data-testid="no-dnd-filter" /> Hide DND numbers <small>({n})</small>
    </label>
  );
}

// "Found by lead harvest" (any job) or one job; job names are fetched only when some lead came from a harvest.
export function HarvestFilter({ leads, value, onChange }) {
  const ids = useMemo(() => {
    const n = {};
    leads.forEach((l) => (l.harvest_jobs || []).forEach((j) => { n[j] = (n[j] || 0) + 1; }));
    return n;
  }, [leads]);
  const [names, setNames] = useState({});
  const any = Object.keys(ids).length > 0;
  useEffect(() => {
    if (!any) return undefined;
    let live = true;
    harvestApi.jobs().then((jobs) => live && setNames(Object.fromEntries((jobs || []).map((j) => [j.id, j.name])))).catch(() => {});
    return () => { live = false; };
  }, [any]);
  if (!any && !value) return null;
  return (
    <label>Lead harvest
      <Select aria-label="Filter by lead harvest" value={value} onChange={(e) => onChange(e.target.value)} data-testid="harvest-filter">
        <option value="">Any source</option>
        <option value="__any">Found by lead harvest ({leads.filter((l) => fromHarvest(l, "__any")).length})</option>
        {Object.entries(ids).map(([id, n]) => <option key={id} value={id}>{names[id] || `Job ${id}`} ({n})</option>)}
      </Select>
    </label>
  );
}

export const harvestLabel = (value) => (value === "__any" ? "Lead harvest" : `Harvest job ${value}`);
