"""The super-admin console's work: per-account figures, approve / reject / ask, suspend / reactivate, export, delete.

Figures (one pass over the data per request): logins, workspaces, leads, emails sent today / in 7 days, last activity
(sign-in or change-log row), limits, flags ("free email" from sign-up, "bounce rate" when at least BOUNCE_MIN leads
were emailed and BOUNCE_FLAG of them bounced).
Suspend: status "suspended" (every login of the account is refused at once by auth.subject_principal, every session
issued before is void through tokens_revoked_ms even after a later reactivation, the account's pairing / API / agent
tokens are deleted, and hold_reason holds its email / WhatsApp / flows / LinkedIn).
Delete: erases the account's leads, their tasks, messages and activities, its workspaces, logins, settings and the
account row. The hash-chained change log keeps its rows (it cannot be rewritten); they name the account only by id.
"""
import io
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone

from . import account_limits, accounts, auth
from .audit_pack import to_csv

IST = timezone(timedelta(hours=5, minutes=30))
BOUNCE_MIN = 20
BOUNCE_FLAG = 0.05
LEAD_COLLECTIONS = ("tasks", "outbox", "whatsapp_outbox", "activities", "transactional_outbox", "notifications", "delivery_events", "inbox")


def _day(value) -> str:
    try:
        at = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return ""
    return (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).astimezone(IST).date().isoformat()


def _read(store, key: str) -> list:
    try:
        rows = store.read(key)
    except KeyError:
        return []
    return rows if isinstance(rows, list) else []


def overview(store, status: str = "", now: datetime = None) -> list:
    """Every account with its figures; pending first, then newest."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()
    week = {(today - timedelta(days=n)).isoformat() for n in range(7)}
    amap = accounts.ws_account_map(store)
    lead_acct, leads_n, bounced, per_ws = {}, {}, {}, {}
    for lead in store.read("leads"):
        aid = amap.get(lead.get("workspace") or "main", accounts.MCN)
        lead_acct[lead.get("id")] = aid
        leads_n[aid] = leads_n.get(aid, 0) + 1
        if (lead.get("sequence") or {}).get("stopped") == "bounced":
            bounced[aid] = bounced.get(aid, 0) + 1
    sent_today, sent_week, emailed = {}, {}, {}
    for m in _read(store, "outbox"):
        if m.get("status") != "sent":
            continue
        aid = m.get("account_id") or lead_acct.get(m.get("lead_id"), accounts.MCN)
        day = m.get("sent_at_ist_date") or _day(m.get("sent_at") or m.get("at"))
        sent_today[aid] = sent_today.get(aid, 0) + (day == today.isoformat())
        sent_week[aid] = sent_week.get(aid, 0) + (day in week)
        emailed.setdefault(aid, set()).add(m.get("lead_id"))
    users = auth.users(store)
    user_acct = {u["email"]: accounts.of_user(u) for u in users}
    last = {}
    for r in _read(store, "login_history"):
        aid = user_acct.get(r.get("email") or "")
        if aid and r.get("result") == "success" and r.get("at", "") > last.get(aid, ""):
            last[aid] = r["at"]
    for r in _read(store, "audit"):
        aid = r.get("account_id")
        if aid and r.get("at", "") > last.get(aid, "") and not str(r.get("action", "")).startswith("view_as."):
            last[aid] = r["at"]
    spaces = [w for w in (store.read("settings").get("workspaces") or []) if isinstance(w, dict)]
    out = []
    for row in accounts.all_accounts(store):
        aid = row["id"]
        mine = [w for w in spaces if (w.get("account_id") or accounts.MCN) == aid]
        flags = list(row.get("flags") or [])
        n_emailed = len(emailed.get(aid, set()))
        rate = round(bounced.get(aid, 0) / n_emailed, 3) if n_emailed else 0.0
        if n_emailed >= BOUNCE_MIN and rate >= BOUNCE_FLAG:
            flags.append(f"bounce rate {round(rate * 100)}%")
        out.append({**{k: row.get(k) for k in ("id", "name", "status", "created_at", "limits", "integrations_granted", "notes", "asked")},
                    "signup": {k: (row.get("signup") or {}).get(k) for k in ("email", "name", "phone", "via", "at")} if row.get("signup") else None,
                    "members": sum(1 for a in user_acct.values() if a == aid),
                    "workspaces": [{"id": w["id"], "name": w.get("name"), "status": w.get("status") or "active",
                                    "requested_by": w.get("requested_by", "")} for w in mine] + ([{"id": "main", "name": "Main", "status": "active"}] if aid == accounts.MCN else []),
                    "leads": leads_n.get(aid, 0), "sends_today": sent_today.get(aid, 0), "sends_7d": sent_week.get(aid, 0),
                    "bounce_rate": rate, "last_activity": last.get(aid), "flags": flags,
                    "usage": account_limits.summary(store, aid) if aid != accounts.MCN else [], "auto_hold": row.get("auto_hold"),
                    "logins": [{"email": u["email"], "name": u.get("name") or u["email"], "role": u.get("role")}
                               for u in users if user_acct[u["email"]] == aid][:200]})
    if status:
        out = [a for a in out if a["status"] == status]
    return sorted(out, key=lambda a: (a["status"] != "pending", a["id"] != accounts.MCN, a.get("created_at") or ""), reverse=False)


def _set_row(store, account_id: str, changes: dict) -> None:
    store.update("settings", lambda s: {**s, "accounts": [{**a, **changes} if isinstance(a, dict) and a.get("id") == account_id else a
                                                          for a in s.get("accounts") or []]})


def admins_of(store, account_id: str) -> list:
    return [u for u in auth.users(store) if accounts.of_user(u) == account_id and u.get("role") == "admin"]


def approve(store, account_id: str) -> dict:
    from . import workspace_admin
    row = accounts.find(store, account_id)
    if row is None or account_id == accounts.MCN:
        raise LookupError("No such account.")
    if row["status"] != "pending":
        raise ValueError("Only a pending account can be approved (use Reactivate for a suspended one).")
    from . import account_limits
    accounts.update(store, account_id, {"status": "active"})
    cap = account_limits.limit_for(store, account_id, "workspaces")
    for w in store.read("settings").get("workspaces") or []:
        if isinstance(w, dict) and w.get("account_id") == account_id and w.get("status") == "requested":
            if cap is not None and len(accounts.workspaces_of(store, account_id)) >= cap:
                break                          # the workspaces limit set before approval: the rest stay "requested"
            workspace_admin.approve(store, {**w})
    return accounts.find(store, account_id)


def ask(store, account_id: str, message: str, by: str) -> dict:
    row = accounts.find(store, account_id)
    if row is None or account_id == accounts.MCN:
        raise LookupError("No such account.")
    text = " ".join(str(message or "").split())[:1000]
    if not text:
        raise ValueError("Write what you need from them.")
    _set_row(store, account_id, {"asked": [*(row.get("asked") or [])[-19:], {"at": auth.now_iso(), "by": by, "message": text}]})
    return accounts.find(store, account_id)


def revoke_tokens(store, account_id: str) -> int:
    """Every session of the account's logins issued until now stops working; its pairing / API / agent tokens go."""
    _set_row(store, account_id, {"tokens_revoked_ms": int(time.time() * 1000)})
    subs = {"user:" + u["email"] for u in auth.users(store) if accounts.of_user(u) == account_id}
    removed = [0]

    def apply(s):
        keep = [t for t in s.get("extension_tokens") or [] if not (isinstance(t, dict) and t.get("sub") in subs)]
        removed[0] = len(s.get("extension_tokens") or []) - len(keep)
        return {**s, "extension_tokens": keep}
    store.update("settings", apply)
    return removed[0]


def suspend(store, account_id: str, reason: str = "") -> dict:
    if account_id == accounts.MCN or accounts.find(store, account_id) is None:
        raise LookupError("No such account." if account_id != accounts.MCN else "The platform account always stays active.")
    accounts.update(store, account_id, {"status": "suspended"})
    _set_row(store, account_id, {"suspended": {"at": auth.now_iso(), "reason": " ".join(str(reason or "").split())[:300]}})
    revoked = revoke_tokens(store, account_id)
    return {**accounts.find(store, account_id), "tokens_removed": revoked}


def reactivate(store, account_id: str) -> dict:
    row = accounts.find(store, account_id)
    if row is None or account_id == accounts.MCN:
        raise LookupError("No such account.")
    accounts.update(store, account_id, {"status": "active"})
    return accounts.find(store, account_id)


def _account_data(store, account_id: str) -> dict:
    own_ws = accounts.workspaces_of(store, account_id, active_only=False)
    leads = [l for l in store.read("leads") if (l.get("workspace") or "main") in own_ws]
    ids = {l.get("id") for l in leads}
    users = [u for u in auth.users(store) if accounts.of_user(u) == account_id]
    emails = {u["email"] for u in users}
    return {"workspaces": own_ws, "leads": leads, "lead_ids": ids, "users": users, "emails": emails,
            "rows": {key: [r for r in _read(store, key) if isinstance(r, dict) and (r.get("lead_id") in ids or r.get("account_id") == account_id)]
                     for key in LEAD_COLLECTIONS}}


def export_zip(store, account_id: str) -> bytes:
    row = accounts.find(store, account_id)
    if row is None:
        raise LookupError("No such account.")
    data = _account_data(store, account_id)
    spaces = [w for w in store.read("settings").get("workspaces") or [] if isinstance(w, dict) and w.get("id") in data["workspaces"]]
    audit_rows = [r for r in _read(store, "audit") if r.get("account_id") == account_id]
    logins = [r for r in _read(store, "login_history") if r.get("email") in data["emails"]]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        safe = {k: v for k, v in row.items() if k not in ("tokens_revoked_ms",)}
        z.writestr("account.json", json.dumps({"account": safe, "settings": accounts.masked_settings(store, account_id)}, indent=1, default=str))
        z.writestr("logins.csv", to_csv([auth.public_user(u) for u in data["users"]], ("email", "name", "role")))
        z.writestr("workspaces.csv", to_csv(spaces, ("id", "name", "status")))
        z.writestr("leads.json", json.dumps(data["leads"], indent=1, default=str, ensure_ascii=False))
        z.writestr("leads.csv", to_csv([{k: v for k, v in l.items() if k != "timeline"} for l in data["leads"]], ("id", "company", "contact_name", "email")))
        for key, rows in data["rows"].items():
            if rows:
                z.writestr(f"{key}.csv", to_csv(rows))
        z.writestr("change_log.csv", to_csv(audit_rows, ("at", "by", "action", "target", "detail")))
        z.writestr("login_history.csv", to_csv(logins, ("at", "email", "event", "result")))
        z.writestr("README.txt", f"Export of account {row['name']} ({account_id}) made {auth.now_iso()}.\n"
                                 "Secrets (password hashes, two-step secrets, API keys) are not included.\n")
    return buf.getvalue()


def delete(store, account_id: str) -> dict:
    """Erase everything of the account (see the module text). Returns counts."""
    if account_id == accounts.MCN or accounts.find(store, account_id) is None:
        raise LookupError("No such account." if account_id != accounts.MCN else "The platform account cannot be deleted.")
    data = _account_data(store, account_id)
    ids, emails, own_ws = data["lead_ids"], data["emails"], data["workspaces"]
    store.update("leads", lambda rows: [l for l in rows if l.get("id") not in ids])
    for key in LEAD_COLLECTIONS:
        try:
            store.update(key, lambda rows: [r for r in rows if not (isinstance(r, dict) and (r.get("lead_id") in ids or r.get("account_id") == account_id))])
        except KeyError:
            pass
    subs = {"user:" + e for e in emails}

    def apply(s):
        acc_settings = {k: v for k, v in (s.get("account_settings") or {}).items() if k != account_id}
        return {**s, "accounts": [a for a in s.get("accounts") or [] if not (isinstance(a, dict) and a.get("id") == account_id)],
                "workspaces": [w for w in s.get("workspaces") or [] if not (isinstance(w, dict) and w.get("id") in own_ws)],
                "users": [u for u in s.get("users") or [] if not (isinstance(u, dict) and u.get("email") in emails)],
                "extension_tokens": [t for t in s.get("extension_tokens") or [] if not (isinstance(t, dict) and t.get("sub") in subs)],
                "account_settings": acc_settings}
    store.update("settings", apply)
    return {"leads": len(ids), "logins": len(emails), "workspaces": len(own_ws)}
