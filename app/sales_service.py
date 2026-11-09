"""Sales funnel, part 6: changing a lead's sales stage, the funnel settings and the migration back-fill.

Everything that writes lead["sales"] from a person goes through `set_sales`, so the rules (app/sales_model.py) hold on
every path and every status change lands in the lead's history, its timeline and the audit log.
"""
import re
from datetime import datetime, timezone
from typing import Optional

from . import audit, sales_model as M
from .lead_model import event
from .sales_view import auto_index, effective_status, funnel_lead, sales_of

SETTING_KEY = "sales_funnel"
MAX_TARGETS = 1200
TEXT_FIELDS = {"industry": 80, "service": 80, "source": 80}


class SalesRefused(ValueError):
    """The change breaks a rule; the message says what to fix, in plain words."""


def _number(body: dict, key: str, label: str) -> Optional[float]:
    """None when the key was not sent; 0 to clear; a positive amount otherwise. Raises SalesRefused for text that is no number."""
    if key not in body or body[key] is None:
        return None
    raw = body[key]
    if raw == "" or raw == 0:
        return 0
    value = M.money(raw)
    if value <= 0:
        raise SalesRefused(f"{label} must be a number above 0 (or leave it empty).")
    return value


def _fields(sales: dict, body: dict) -> dict:
    """The plain fields of the record with whatever the person sent folded in (validated)."""
    out = {**sales}
    for key, label in (("est_value", "Est. Monthly Value (₹)"), ("closed_value", "Closed Monthly Value (₹)"), ("tenure", "Contract Tenure (Months)")):
        value = _number(body, key, label)
        if value is not None:
            out[key] = value
    if "lost_reason" in body:
        reason = str(body["lost_reason"] or "")
        if reason and reason not in M.LOST_REASONS:
            raise SalesRefused("Lost Reason must be one of: " + ", ".join(M.LOST_REASONS) + ".")
        out["lost_reason"] = reason
    if "fit" in body:
        fit = str(body["fit"] or "").upper()
        if fit and fit not in M.FITS:
            raise SalesRefused("Lead Score (Fit) must be A, B or C.")
        out["fit"] = fit
    if "engagement" in body:
        eng = str(body["engagement"] or "").capitalize()
        if eng and eng not in M.ENGAGEMENTS:
            raise SalesRefused("Engagement must be Hot, Warm or Cold.")
        out["engagement"] = eng
    for key, cap in TEXT_FIELDS.items():
        if key in body:
            out[key] = " ".join(str(body[key] or "").split())[:cap]
    return out


def set_sales(store, lead_id: str, body: dict, user: Optional[dict], now: Optional[datetime] = None) -> dict:
    """Apply a person's change to one lead's sales record. Raises LookupError (no such lead) or SalesRefused (a rule).
    Returns {"lead": saved lead, "from": old status, "to": new status, "changed": bool}."""
    now = now or datetime.now(timezone.utc)
    actor = audit.actor_of(user)
    info = {}

    def apply(lead: dict):
        sales = sales_of(lead)
        old = funnel_lead(lead).status if not sales.get("status") else M.stored_status(sales)
        cand = _fields(sales, body)
        want = body.get("status")
        new = M.clean_status(want) if want else ""
        if want and not new:
            raise SalesRefused("Pick a Lead Status from the list.")
        status = new or old
        if new and (new != old or not sales.get("status")):
            result = M.apply_status(cand, new, old, now)
            if not result["ok"]:
                raise SalesRefused(result["error"])
            cand = M.merge_set(cand, result["set"], new)
            cand["history"] = [*(sales.get("history") or []), M.history_row(actor["name"], old, new, now)][-M.MAX_HISTORY:]
            info.update(changed=True, status=True)
        else:
            if status == M.WON:                                # values edited on an already won deal
                if not M.money(cand.get("closed_value")) > 0:
                    raise SalesRefused("Closed Monthly Value (₹) must stay above 0 on a Closed Won lead.")
                tenure = M.money(cand.get("tenure")) or M.DEFAULTS["tenure_months"]
                cand["tenure"], cand["tcv"] = tenure, M.money(cand["closed_value"]) * tenure
            if status != M.INQUIRY and status in M.STATUSES[3:7] and not M.money(cand.get("est_value")) > 0:
                raise SalesRefused("Est. Monthly Value (₹) is required from SQL onwards.")
            if status == M.LOST and cand.get("lost_reason") not in M.LOST_REASONS:
                raise SalesRefused("Lost Reason is required on a Closed Lost lead.")
            if cand != sales:
                info["changed"] = True
        if info.get("changed"):
            cand["last_activity"] = M.iso(now)
        info.update(old=old, new=status)
        if not info.get("changed"):
            return None
        timeline = lead.get("timeline") or []
        if info.get("status"):
            timeline = [*timeline, {**event("sales", f"Sales stage: {old} → {status} (by {actor['name']})"), "by": actor["name"]}]
        return {**lead, "sales": cand, "timeline": timeline}

    saved = store.update_lead(lead_id, apply)
    if saved is None:
        raise LookupError("Lead not found")
    if info.get("changed"):
        what = f"{info['old']} → {info['new']}" if info.get("status") else "values changed"
        audit.record(store, user, "sales.stage" if info.get("status") else "sales.edit", saved.get("company") or lead_id,
                     f"{what} (lead {lead_id})", {"lead_id": lead_id})
    return {"lead": saved, "from": info.get("old"), "to": info.get("new"), "changed": bool(info.get("changed"))}


def detail(lead: dict, auto: Optional[dict] = None) -> dict:
    """What the lead drawer shows: the effective status, the record, the history and the choices."""
    s = sales_of(lead)
    f = funnel_lead(lead, auto)
    fmt = lambda dt: M.iso(dt) if dt else ""
    return {"status": f.status, "has_record": bool(s.get("status")), "est_value": f.est, "closed_value": f.closed_value, "tenure": f.tenure or None,
            "tcv": f.tcv, "prob": s.get("prob") if s.get("prob") is not None else M.PROBABILITY.get(next((x["key"] for x in M.STAGES if x["status"] == f.status), ""), 0),
            "lost_reason": f.lost_reason, "lost_stage": f.lost_stage, "fit": f.fit, "engagement": f.engagement, "industry": f.industry,
            "service": f.service, "source": f.source, "backfilled": f.backfilled, "first_response": fmt(f.first_response),
            "dates": {"inquiry": fmt(f.created), **{k: fmt(f.dates.get(k)) for k in ("mql", "sal", "sql", "meeting", "proposal", "negotiation")},
                      "closed": fmt(f.closed), "recycled": fmt(f.recycled)},
            "history": list(reversed(s.get("history") or [])), "statuses": list(M.STATUSES), "lost_reasons": list(M.LOST_REASONS),
            "fits": list(M.FITS), "engagements": list(M.ENGAGEMENTS)}


# ---------------------------------------------------------------- settings (per account): SLA hours, stalled days, targets
def account_key() -> str:
    from . import accounts
    return accounts.current() or accounts.MCN


def read_settings(store) -> dict:
    raw = ((store.read("settings") or {}).get(SETTING_KEY) or {}).get(account_key()) or {}
    return {"sla_hours": int(raw.get("sla_hours") or M.DEFAULTS["sla_hours"]), "stalled_days": int(raw.get("stalled_days") or M.DEFAULTS["stalled_days"]),
            "targets": [t for t in raw.get("targets") or [] if isinstance(t, dict)]}


def clean_settings(raw: dict) -> dict:
    out = {}
    for key, low, high, label in (("sla_hours", 1, 720, "SLA hours"), ("stalled_days", 1, 365, "Stalled days")):
        if raw.get(key) not in (None, ""):
            try:
                value = int(raw[key])
            except (TypeError, ValueError):
                raise SalesRefused(f"{label} must be a whole number.") from None
            if not low <= value <= high:
                raise SalesRefused(f"{label} must be between {low} and {high}.")
            out[key] = value
    if raw.get("targets") is not None:
        rows = raw["targets"]
        if not isinstance(rows, list) or len(rows) > MAX_TARGETS:
            raise SalesRefused(f"At most {MAX_TARGETS} target rows.")
        seen, targets = set(), []
        for t in rows:
            month, owner = str((t or {}).get("month") or ""), " ".join(str((t or {}).get("owner") or "").split())[:80]
            if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
                raise SalesRefused("Each target needs a month like 2026-11.")
            try:
                amount = float(str((t or {}).get("target") or 0).replace(",", ""))
            except ValueError:
                raise SalesRefused("A target must be a number.") from None
            if not 0 <= amount <= M.MAX_MONEY:
                raise SalesRefused("A target is out of range.")
            if (month, owner) in seen:
                raise SalesRefused(f"{month} appears twice for {owner or 'everyone'}.")
            seen.add((month, owner))
            targets.append({"month": month, "owner": owner, "target": int(amount) if amount == int(amount) else amount})
        out["targets"] = targets
    return out


def save_settings(store, raw: dict, user: Optional[dict]) -> dict:
    changes = clean_settings(raw)
    key = account_key()

    def apply(settings: dict) -> dict:
        all_rows = dict(settings.get(SETTING_KEY) or {})
        all_rows[key] = {**(all_rows.get(key) or {}), **changes}
        return {**settings, SETTING_KEY: all_rows}
    store.update("settings", apply)
    audit.record(store, user, "sales.settings", key, "Sales funnel settings saved (" + ", ".join(sorted(changes)) + ")")
    return read_settings(store)


# ---------------------------------------------------------------- migration: back-fill stage dates from the current status
def recalculate(store, user: Optional[dict], now: Optional[datetime] = None) -> dict:
    """For every lead in this request's scope that has a sales status: fill blank stage dates up to that status with the
    lead's Created Date and mark them back-filled. Existing dates are never touched. Returns counts."""
    from . import workspaces
    now = now or datetime.now(timezone.utc)
    stats = {"checked": 0, "updated": 0, "dates_filled": 0}

    def apply(leads: list) -> list:
        out = []
        for lead in leads:
            s = lead.get("sales")
            if not isinstance(s, dict) or not s.get("status") or not workspaces.visible(lead):
                out.append(lead)
                continue
            stats["checked"] += 1
            created = M.parse_dt((s.get("dates") or {}).get("inquiry")) or M.parse_dt(lead.get("created_at"))
            changes = M.recalc(s, created, now)
            merged = M.merge_set(s, changes)
            if merged == s:
                out.append(lead)
                continue
            stats["updated"] += 1
            stats["dates_filled"] += len(changes.get("dates") or {})
            out.append({**lead, "sales": merged})
        return out
    store.update("leads", apply)
    audit.record(store, user, "sales.recalculate", "sales funnel", f"{stats['updated']} leads back-filled, {stats['dates_filled']} dates filled")
    return stats
