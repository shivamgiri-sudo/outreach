import { useMemo, useState } from "react";
import { salesStageOf } from "../salesStage.js";
import { liStageMatches } from "./LeadLinkedInStage.jsx";
import { customMatches } from "./LeadCustomFields.jsx";
import { isWaiting } from "./LeadFirstTouch.jsx";
import { contactOf, isFresh } from "../contactStatus.js";
import { SORTS, matches } from "./leadFilters.js";

// All Leads filter state in one place: values `f`, setters `set`, the filtered+sorted list, saved-view helpers.
export default function useLeadFilters({ leads, me, customFields, initialOnlyIds }) {
  const [query, setQuery] = useState("");
  const [stage, setStage] = useState("");
  const [band, setBand] = useState("");
  const [srcFilter, setSrcFilter] = useState([]);
  const [inboundOnly, setInboundOnly] = useState(false);
  const [ownerFilter, setOwnerFilter] = useState("");
  const [tagFilter, setTagFilter] = useState("");
  const [hasFilter, setHasFilter] = useState([]);
  const [liStage, setLiStage] = useState("");
  const [salesStage, setSalesStage] = useState("");
  const [waitingOnly, setWaitingOnly] = useState(false);
  const [leadType, setLeadType] = useState("");       // "" any | valid | invalid | consultant (marked by a caller)
  const [contact, setContact] = useState("");        // "" any, never | tried | connected | replied | fresh
  const [ageing, setAgeing] = useState("");         // "" any | untouched | stale | stuck (server field `ageing`)
  const [customFilter, setCustomFilter] = useState({});
  const [sort, setSort] = useState("score");
  const [onlyIds, setOnlyIds] = useState(initialOnlyIds);
  const customOn = Object.values(customFilter).some((v) => (typeof v === "object" ? v.min || v.max : v));

  const shown = useMemo(() => leads.filter((l) => matches(l, query, stage, band, onlyIds, srcFilter, inboundOnly, ownerFilter, tagFilter, me, hasFilter)
    && customMatches(l, customFilter, customFields) && liStageMatches(l, liStage) && (!salesStage || salesStageOf(l) === salesStage) && (!waitingOnly || isWaiting(l)) && (!leadType || (leadType === "valid" ? !l.lead_quality : l.lead_quality?.kind === leadType)) && (!contact || (contact === "fresh" ? isFresh(l) : contactOf(l).status === contact)) && (!ageing || l.ageing === ageing)).sort(SORTS[sort]),
  [leads, query, stage, band, sort, onlyIds, srcFilter, inboundOnly, ownerFilter, tagFilter, me, customFilter, customFields, hasFilter, liStage, salesStage, waitingOnly, contact, leadType, ageing]);

  const viewFilters = { query, stage, band, sources: srcFilter, inbound: inboundOnly, owner: ownerFilter, tag: tagFilter, has: hasFilter, li_stage: liStage, contact, quality: leadType, ageing, sort };
  const applyView = (v) => {
    setQuery(v.query || ""); setStage(v.stage || ""); setBand(v.band || ""); setSrcFilter(v.sources || []); setInboundOnly(Boolean(v.inbound));
    setOwnerFilter(v.owner || ""); setTagFilter(v.tag || ""); setHasFilter(Array.isArray(v.has) ? v.has : []); setLiStage(v.li_stage || ""); setContact(v.contact || ""); setLeadType(v.quality || ""); setAgeing(v.ageing || ""); setSort(SORTS[v.sort] ? v.sort : "score"); setOnlyIds(null);
  };
  const filtered = Boolean(waitingOnly || query.trim() || stage || band || onlyIds || srcFilter.length || inboundOnly || ownerFilter || tagFilter || customOn || hasFilter.length || liStage || contact || leadType || ageing);
  const clearFilters = () => { setQuery(""); setStage(""); setBand(""); setOnlyIds(null); setSrcFilter([]); setInboundOnly(false); setOwnerFilter(""); setTagFilter(""); setCustomFilter({}); setHasFilter([]); setLiStage(""); setSalesStage(""); setWaitingOnly(false); setContact(""); setLeadType(""); setAgeing(""); };
  // How many of the filters tucked under "More filters" are active (shown as a badge on that button).
  const nMore = [salesStage, band, leadType, liStage, tagFilter, inboundOnly, srcFilter.length, hasFilter.length, customOn].filter(Boolean).length;

  const f = { query, stage, band, srcFilter, inboundOnly, ownerFilter, tagFilter, hasFilter, liStage, salesStage, waitingOnly, leadType, contact, ageing, customFilter, onlyIds };
  const set = { query: setQuery, stage: setStage, band: setBand, srcFilter: setSrcFilter, inboundOnly: setInboundOnly, ownerFilter: setOwnerFilter, tagFilter: setTagFilter, hasFilter: setHasFilter, liStage: setLiStage, salesStage: setSalesStage, waitingOnly: setWaitingOnly, leadType: setLeadType, contact: setContact, ageing: setAgeing, customFilter: setCustomFilter };
  const resetBasic = () => { setQuery(""); setStage(""); setBand(""); };
  return { f, set, sort, setSort, onlyIds, setOnlyIds, shown, viewFilters, applyView, filtered, clearFilters, customOn, nMore, resetBasic };
}
