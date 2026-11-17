"""Matching leads to the local company register (app/company_register.py) and writing the result onto the lead.

Per lead: candidates by exact cleaned name, then name prefix, then the name's telling words (tok_key); scored and ranked
by app/company_match.py. What happens with the best match:
  high     attached at once: lead.registry = {cin, name, status, class, category, incorporated, paid_up, authorised,
           state, roc, address, activity, email (only when the register has one), confidence, matched_at, source}
  medium   kept as lead.registry_match (best + up to 3 alternatives): the drawer asks "Is this the company?"
  low      kept as lead.registry_match too, but never attached; a person may still pick one by hand
A person confirms (attach that CIN) or rejects (that CIN is never suggested for this lead again; rejecting the attached
company takes it off and clears the fields it filled, when they were not changed since).

Attaching fills ONLY empty lead fields (company_legal, company, state, address) with provenance "mca-master-data".
The registered email is shown on the lead's register panel only; it is never copied into the lead's email.
"""
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import company_match as M
from .company_register import SOURCE, repo_for
from .lead_model import event, now_iso

BATCH_MAX = 500
RECHECK_DAYS = 30
FILL = (("company_legal", "name"), ("company", "name"), ("state", "state"), ("address", "address"))
SHOWN = ("cin", "name", "status", "cls", "category", "sub_category", "incorporated", "authorised", "paid_up", "state", "roc",
         "address", "activity", "nic", "email", "last_return")


class RegisterEmpty(ValueError):
    pass


def lead_name(lead: dict) -> str:
    return (lead.get("company_legal") or lead.get("company") or "").strip()


def candidates(repo, lead: dict) -> list:
    key = M.normalise(lead_name(lead))
    if not key:
        return []
    found = {r["cin"]: r for r in repo.by_name(key)}
    words = M.tokens(key)
    prefix = " ".join(words[:2]) if len(words) > 1 else words[0]
    for row in repo.by_prefix(prefix):
        found.setdefault(row["cin"], row)
    for row in repo.by_tokens(M.distinctive(key)):
        found.setdefault(row["cin"], row)
    return list(found.values())


def find(store, lead: dict) -> dict:
    """The ranked result for one lead (nothing written). RegisterEmpty when no register has been imported yet."""
    if not lead_name(lead):
        raise ValueError("This lead has no company name to look up.")
    repo = repo_for(store)
    if not repo.q1("SELECT cin FROM mca_companies LIMIT 1"):
        raise RegisterEmpty("The company register is empty. An owner imports it under Data sources > Company register.")
    rejected = set((lead.get("registry_rejected") or []))
    return M.rank(lead, candidates(repo, lead), rejected)


def shown(row: dict) -> dict:
    """The register facts kept on the lead (class under its plain name; email only when the register has one)."""
    out = {("class" if k == "cls" else k): row.get(k) for k in SHOWN if row.get(k) not in (None, "")}
    if "match" in row:
        out["score"] = row["match"]["score"]
        out["why"] = {k: row["match"][k] for k in ("name", "state", "city", "active")}
    return out


def registry_of(row: dict, confidence: str, by: str) -> dict:
    return {**shown(row), "confidence": confidence, "matched_at": now_iso(), "matched_by": by, "source": SOURCE}


def _fill(lead: dict, reg: dict) -> list:
    filled = []
    for field, key in FILL:
        value = reg.get(key)
        if value and not str(lead.get(field) or "").strip():
            lead[field] = value
            filled.append(field)
    if filled:
        lead["provenance"] = {**(lead.get("provenance") or {}), **{f: SOURCE for f in filled}}
    return filled


def _attach(lead: dict, reg: dict, text: str) -> dict:
    filled = _fill(lead, reg)
    lead["registry"] = {**reg, "filled": filled}
    lead.pop("registry_match", None)
    extra = f"; filled {', '.join(f.replace('_', ' ') for f in filled)}" if filled else ""
    lead["timeline"] = [*lead.get("timeline", []), event("enrichment", f"{text}: {reg['name']} (CIN {reg['cin']}){extra}")]
    return lead


def write_result(store, lead_id: str, result: dict, by: str = "System") -> Optional[dict]:
    """Save one ranked result: attach when high, else keep it as a suggestion. Never replaces an attached company."""
    out = {}

    def change(lead: dict):
        if lead.get("registry"):
            out["kept"] = True
            return None
        best = result.get("best")
        if best is not None and result["confidence"] == "high":
            out["attached"] = True
            return _attach(lead, registry_of(best, "high", by), "Matched in the company register")
        lead["registry_match"] = {"checked_at": now_iso(), "confidence": result["confidence"],
                                  "best": shown(best) if best else None, "alternatives": [shown(r) for r in result["alternatives"]]}
        return lead
    saved = store.update_lead(lead_id, change)
    return {"lead": saved, "attached": bool(out.get("attached")), "kept": bool(out.get("kept"))}


def check(store, lead: dict, by: str = "System") -> dict:
    result = find(store, lead)
    saved = write_result(store, lead["id"], result, by)
    after = (saved or {}).get("lead") or {}
    return {"confidence": result["confidence"], "attached": saved["attached"], "registry": after.get("registry"),
            "registry_match": after.get("registry_match"), "filled": (after.get("registry") or {}).get("filled", []) if saved["attached"] else []}


def confirm(store, lead_id: str, cin: str, by: str) -> dict:
    row = repo_for(store).get(cin)
    if row is None:
        raise ValueError("That CIN is not in the company register.")
    out = {}

    def change(lead: dict):
        if (lead.get("registry") or {}).get("cin") == row["cin"]:
            return None
        if lead.get("registry"):
            _undo_fill(lead)
        lead["registry_rejected"] = [c for c in lead.get("registry_rejected") or [] if c != row["cin"]]
        out["done"] = True
        match = M.score(lead, row)
        return _attach(lead, registry_of({**row, "match": match}, "confirmed", by), f"Company register match confirmed by {by}")
    saved = store.update_lead(lead_id, change)
    return {"lead": saved, "registry": (saved or {}).get("registry")}


def _undo_fill(lead: dict) -> None:
    reg = lead.get("registry") or {}
    prov = dict(lead.get("provenance") or {})
    for field in reg.get("filled") or []:
        key = dict(FILL)[field]
        if prov.get(field) == SOURCE and lead.get(field) == reg.get(key):
            lead[field] = ""
            prov.pop(field, None)
    lead["provenance"] = prov
    lead.pop("registry", None)


def reject(store, lead_id: str, cin: str, by: str) -> dict:
    cin = str(cin or "").strip().upper()

    def change(lead: dict):
        lead["registry_rejected"] = list(dict.fromkeys([*(lead.get("registry_rejected") or []), cin]))[-50:]
        if (lead.get("registry") or {}).get("cin") == cin:
            _undo_fill(lead)
            lead["timeline"] = [*lead.get("timeline", []), event("enrichment", f"Company register match {cin} removed by {by}")]
        match = lead.get("registry_match") or {}
        if match:
            rest = [r for r in [match.get("best"), *(match.get("alternatives") or [])] if r and r.get("cin") != cin]
            lead["registry_match"] = {**match, "best": None, "alternatives": rest[:3], "confidence": "rejected"}
        return lead
    saved = store.update_lead(lead_id, change)
    return {"lead": saved}


# ---------- bounded batch ----------
def pick(leads: list, limit: int, now: Optional[datetime] = None) -> list:
    """Lead ids with a company name, no attached register entry and no check in the last RECHECK_DAYS (closed leads skipped)."""
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=RECHECK_DAYS)).isoformat(timespec="seconds")
    out = []
    for lead in leads:
        if not lead_name(lead) or lead.get("registry") or lead.get("stage") in ("unsubscribed", "disqualified"):
            continue
        if (lead.get("registry_match") or {}).get("checked_at", "") >= since:
            continue
        out.append(lead["id"])
    return out[:max(1, min(int(limit), BATCH_MAX))]


class Batch:
    """One batch at a time, in a background thread, with progress (like app/hiring_signal.Batch)."""

    def __init__(self, store):
        self.store, self._lock = store, threading.Lock()
        self.state = {"status": "idle"}

    def start(self, ids: list, by: str, background: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("A register match is already running. Wait for it to finish.")
        self.state = {"status": "running", "done": 0, "total": len(ids), "attached": 0, "suggested": 0, "low": 0, "errors": 0,
                      "started_at": now_iso(), "finished_at": ""}
        if background:
            threading.Thread(target=self._run, args=(ids, by), daemon=True).start()
        else:
            self._run(ids, by)
        return dict(self.state)

    def _run(self, ids: list, by: str) -> None:
        try:
            leads = {l["id"]: l for l in self.store.read("leads") if l["id"] in set(ids)}
            for lead_id in ids:
                try:
                    lead = leads.get(lead_id)
                    if lead is not None:
                        res = check(self.store, lead, by)
                        key = "attached" if res["attached"] else "suggested" if res["confidence"] == "medium" else "low"
                        self.state[key] += 1
                except RegisterEmpty as exc:
                    self.state.update(errors=self.state["errors"] + 1, note=str(exc))
                    break
                except Exception:                      # noqa: BLE001 - one bad lead never stops the batch
                    self.state["errors"] += 1
                self.state["done"] += 1
            self.state["status"] = "done"
        finally:
            self.state["finished_at"] = now_iso()
            if self.state["status"] == "running":
                self.state["status"] = "failed"
            self._lock.release()
