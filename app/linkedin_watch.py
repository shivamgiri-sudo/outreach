"""Is the LinkedIn agent really working? Today's numbers for the LinkedIn page, an hourly summary in the bell, and an
alert when people are waiting but the agent is not running."""
from datetime import datetime, timedelta
from typing import Optional

from . import accounts as accts_mod, linkedin_agent as agent
from .activities import iso
from .reminders import notify

MCN = agent.MCN
SUMMARY_EVERY = timedelta(hours=1)
OFFLINE_AFTER = timedelta(minutes=30)
OFFLINE_REPEAT = timedelta(hours=6)
STATE_KEY = "linkedin_watch"


def _owner_leads(store, owner: Optional[str]) -> list:
    amap = accts_mod.ws_account_map(store)
    out = []
    for l in store.read("leads"):
        if not l.get("linkedin_seq"):
            continue
        if owner is not None and accts_mod.account_of_lead(amap, l) != owner:
            continue
        out.append(l)
    return out


def summary(store, now: datetime, owner: Optional[str] = MCN) -> dict:
    """Today (IST) for one client account: queued, sent, confirmed, not confirmed, problems, and whether the agent is up."""
    since = agent._day_start(now)
    rows = [r for r in agent.log_rows(store, owner) if datetime.fromisoformat(r["at"]) >= since]
    sent = [r for r in rows if r.get("kind") in ("connect", "message")]
    queued = sum(1 for l in _owner_leads(store, owner) if l["linkedin_seq"].get("status") == "waiting_to_connect")
    settings = store.read("settings")
    acct = owner or MCN
    seen = (settings.get(agent.SETTINGS_SEEN) or {}) if acct == MCN else ((settings.get(agent.SETTINGS_SEEN_BY) or {}).get(acct) or {})
    last_seen = seen.get("at")
    online = bool(last_seen) and (now - datetime.fromisoformat(last_seen)).total_seconds() < 180
    return {"queued": queued, "connect": sum(1 for r in sent if r["kind"] == "connect"), "message": sum(1 for r in sent if r["kind"] == "message"),
            "confirmed": sum(1 for r in sent if r.get("verified") is True), "unconfirmed": sum(1 for r in sent if r.get("verified") is False),
            "problems": sum(1 for r in rows if r.get("kind") == "error"), "accepted": sum(1 for r in rows if r.get("kind") == "accepted"),
            "replied": sum(1 for r in rows if r.get("kind") == "replied"), "online": online, "last_seen": last_seen,
            "enabled": bool(agent.config(store, acct).get("enabled"))}


def _owners(store) -> list:
    rows = store.read("settings").get("linkedin_log") or []
    return sorted({r.get("account_id") or MCN for r in rows} | {MCN})


def _recipients(store, owner: str) -> list:
    if owner == MCN:
        return [store.read("settings").get("linkedin_owner") or "Sales team"]
    from . import linkedin_scope as scope
    return list(scope.admins_of(store, owner)) or []


def _tell(store, owner: str, title: str, text: str) -> None:
    for who in _recipients(store, owner):
        notify(store, who, title, text, kind="linkedin", account_id=owner if owner != MCN else "")


def tick(store, now: datetime) -> dict:
    """Called every minute. At most one summary per hour per client account, and only when something happened."""
    done = {"summaries": 0, "offline": 0}
    for owner in _owners(store):
        st = (store.read("settings").get(STATE_KEY) or {}).get(owner) or {}
        last = datetime.fromisoformat(st["summary_at"]) if st.get("summary_at") else now - SUMMARY_EVERY
        new = [r for r in agent.log_rows(store, owner) if datetime.fromisoformat(r["at"]) > last and r.get("kind") in ("connect", "message", "error", "accepted", "replied")]
        patch = {}
        if new and now - last >= SUMMARY_EVERY:
            c = sum(1 for r in new if r["kind"] == "connect"); m = sum(1 for r in new if r["kind"] == "message")
            ok = sum(1 for r in new if r["kind"] in ("connect", "message") and r.get("verified") is True)
            bad = [r for r in new if r["kind"] == "error"]; unc = sum(1 for r in new if r.get("verified") is False)
            parts = [f"{c} connection request{'s' if c != 1 else ''} sent", f"{m} message{'s' if m != 1 else ''} sent"]
            if c + m:
                parts.append(f"{ok} confirmed on LinkedIn" + (f", {unc} not confirmed" if unc else ""))
            a = sum(1 for r in new if r["kind"] == "accepted"); rp = sum(1 for r in new if r["kind"] == "replied")
            if a: parts.append(f"{a} accepted")
            if rp: parts.append(f"{rp} replied")
            text = "; ".join(parts) + "."
            if bad:
                text += f" {len(bad)} problem{'s' if len(bad) != 1 else ''}, latest: {bad[-1].get('note', '')[:140]}"
            _tell(store, owner, "LinkedIn agent: what it did", text)
            patch["summary_at"] = iso(now); done["summaries"] += 1
        s = summary(store, now, owner)
        seen = datetime.fromisoformat(s["last_seen"]) if s["last_seen"] else None
        gone = seen is None or now - seen > OFFLINE_AFTER
        last_off = datetime.fromisoformat(st["offline_at"]) if st.get("offline_at") else None
        if s["queued"] and gone and (last_off is None or now - last_off >= OFFLINE_REPEAT):
            _tell(store, owner, "LinkedIn agent is not running",
                  f"{s['queued']} people are waiting to be sent a connection request, but the LinkedIn agent has not been seen "
                  f"{'for ' + str(int((now - seen).total_seconds() // 60)) + ' minutes' if seen else 'at all'}. Nothing is sent until it runs: "
                  "start it on the office computer (LinkedIn page, Advanced).")
            patch["offline_at"] = iso(now); done["offline"] += 1
        if patch:
            store.update("settings", lambda s_: {**s_, STATE_KEY: {**(s_.get(STATE_KEY) or {}), owner: {**st, **patch}}})
    return done
