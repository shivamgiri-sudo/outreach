"""Reports by email: a designed HTML email (numbers, what they mean, per day and per person) with a plain-text twin,
the activity log attached as CSV, and a signed link that downloads the same file (valid LINK_DAYS days).

The link stores the workspace scope of the person who asked, so the file it serves holds exactly what they could see.
"""
import hashlib
import html
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import daily_report
from .analytics_range import public_range

LINK_DAYS = 7
MAX_LINKS = 500
LABEL = {"leads_added": "Leads added", "calls": "Calls", "calls_connected": "Calls connected", "interested": "Interested",
         "not_interested": "Not interested", "follow_ups": "Follow-ups asked", "bad_numbers": "Wrong / inactive numbers",
         "emails_sent": "Emails sent", "whatsapp_sent": "WhatsApp sent", "linkedin": "LinkedIn steps", "replies": "Replies received",
         "meetings": "Meetings", "notes": "Notes", "stage_changes": "Stage changes", "tasks_done": "Tasks done"}
TILES = ("leads_added", "calls", "calls_connected", "interested", "not_interested", "follow_ups", "emails_sent", "replies", "meetings")
DAY_COLS = ("calls", "calls_connected", "interested", "not_interested", "follow_ups", "emails_sent", "whatsapp_sent", "replies", "meetings")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_link(store, rng: dict, who: str, kind: str, scope: tuple, by: str, now: Optional[datetime] = None) -> str:
    """A one-off token for the download link (only its hash is kept)."""
    now = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(24)
    selected, allowed = scope
    row = {"hash": _hash(token), "from": rng["from"], "to": rng["to"], "who": who, "kind": kind, "by": by,
           "selected": selected, "allowed": sorted(allowed) if allowed is not None else None,
           "created_at": now.isoformat(timespec="seconds"), "expires_at": (now + timedelta(days=LINK_DAYS)).isoformat(timespec="seconds")}
    store.update("settings", lambda s: {**s, "report_links": [*[r for r in s.get("report_links") or []
                                                                 if r.get("expires_at", "") > now.isoformat()], row][-MAX_LINKS:]})
    return token


def find_link(store, token: str, now: Optional[datetime] = None) -> Optional[dict]:
    now = now or datetime.now(timezone.utc)
    h = _hash(token or "")
    row = next((r for r in store.read("settings").get("report_links") or [] if secrets.compare_digest(r.get("hash", ""), h)), None)
    return row if row and row.get("expires_at", "") > now.isoformat() else None


def _n(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def analysis(report: dict) -> list:
    """Plain sentences on what the numbers say."""
    t, days, team = report["totals"], report["days"], report["team"]
    out = []
    if t["calls"]:
        rate = round(100 * t["calls_connected"] / t["calls"])
        out.append(f"{_n(t['calls'], 'call')} made and {t['calls_connected']} connected ({rate}%)."
                   + (" Fewer than 1 in 3 calls connect: check the numbers, and try calling at other times." if rate < 33 else ""))
    else:
        out.append("No calls were logged in this period.")
    if t["calls_connected"]:
        out.append(f"Of the leads reached, {t['interested']} said they are interested, {t['not_interested']} said no and "
                   f"{t['follow_ups']} asked to be called back.")
    if t["bad_numbers"]:
        out.append(f"{_n(t['bad_numbers'], 'number')} wrong or inactive: correct them on the lead (Edit details or add another number).")
    if t["emails_sent"]:
        out.append(f"{_n(t['emails_sent'], 'email')} sent and {t['replies']} {'reply' if t['replies'] == 1 else 'replies'} received.")
    busiest = max(days, key=lambda d: d["calls"] + d["emails_sent"], default=None)
    if busiest and busiest["calls"] + busiest["emails_sent"]:
        out.append(f"The busiest day was {busiest['day']} ({_n(busiest['calls'], 'call')}, {_n(busiest['emails_sent'], 'email')}).")
    idle = [d["day"] for d in days if datetime.fromisoformat(d["day"]).weekday() < 5 and not any(d[k] for k in DAY_COLS)]
    if idle:
        out.append(f"No outreach on {len(idle)} working day(s): {', '.join(sorted(idle)[:5])}{'…' if len(idle) > 5 else ''}.")
    if team:
        top = team[0]
        out.append(f"Most active: {top['who']} ({_n(top['actions'], 'action')}, {_n(top['calls'], 'call')}, {top['interested']} interested).")
    return out


def _cell(v, head=False, align="right") -> str:
    tag = "th" if head else "td"
    style = f"padding:6px 8px;border-bottom:1px solid #e2e8f0;text-align:{align};font-size:13px;" + ("background:#f1f5f9;color:#334155;" if head else "color:#0f172a;")
    return f"<{tag} style=\"{style}\">{html.escape(str(v))}</{tag}>"


EMPTY_TEAM = '<tr><td style="padding:6px;">No one did anything in this period.</td></tr>'


def render_html(report: dict, title: str, period_text: str, link: str, brand: str = "MCN Outreach") -> str:
    t = report["totals"]
    tiles = "".join(f"<td style=\"padding:10px;border:1px solid #e2e8f0;border-radius:8px;text-align:center;\"><div style=\"font-size:22px;font-weight:700;color:#0369a1;\">{t[k]}</div>"
                    f"<div style=\"font-size:12px;color:#475569;\">{LABEL[k]}</div></td>" for k in TILES)
    points = "".join(f"<li style=\"margin:4px 0;\">{html.escape(p)}</li>" for p in analysis(report))
    day_rows = "".join("<tr>" + _cell(d["day"], align="left") + "".join(_cell(d[c]) for c in DAY_COLS) + "</tr>" for d in report["days"][:62])
    team_rows = "".join("<tr>" + _cell(p["who"], align="left") + _cell(p["actions"]) + "".join(_cell(p[c]) for c in DAY_COLS) + "</tr>" for p in report["team"][:50])
    head = lambda first: "<tr>" + _cell(first, True, "left") + "".join(_cell(LABEL[c], True) for c in DAY_COLS) + "</tr>"
    team_head = "<tr>" + _cell("Person", True, "left") + _cell("Actions", True) + "".join(_cell(LABEL[c], True) for c in DAY_COLS) + "</tr>"
    button = (f"<p style=\"margin:18px 0;\"><a href=\"{html.escape(link)}\" style=\"background:#0369a1;color:#fff;padding:10px 16px;border-radius:8px;"
              f"text-decoration:none;font-weight:600;\">Download the activity log (CSV)</a></p>"
              f"<p style=\"font-size:12px;color:#475569;\">The link works for {LINK_DAYS} days. The same file is attached to this email.</p>") if link else ""
    return (f"<!doctype html><html><body style=\"margin:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;\">"
            f"<div style=\"max-width:760px;margin:0 auto;padding:20px;background:#fff;\">"
            f"<div style=\"font-size:12px;color:#475569;\">{html.escape(brand)}</div>"
            f"<h1 style=\"font-size:20px;color:#0f172a;margin:4px 0;\">{html.escape(title)}</h1>"
            f"<div style=\"font-size:13px;color:#475569;margin-bottom:14px;\">{html.escape(period_text)} (India time)</div>"
            f"<table role=\"presentation\" cellspacing=\"6\" style=\"width:100%;border-collapse:separate;\"><tr>{tiles}</tr></table>"
            f"<h2 style=\"font-size:16px;color:#0f172a;margin:18px 0 6px;\">What the numbers say</h2><ul style=\"padding-left:18px;color:#0f172a;font-size:14px;\">{points}</ul>"
            f"{button}"
            f"<h2 style=\"font-size:16px;color:#0f172a;margin:18px 0 6px;\">Each day</h2>"
            f"<div style=\"overflow-x:auto;\"><table style=\"border-collapse:collapse;width:100%;\">{head('Day')}{day_rows}</table></div>"
            f"<h2 style=\"font-size:16px;color:#0f172a;margin:18px 0 6px;\">By person</h2>"
            f"<div style=\"overflow-x:auto;\"><table style=\"border-collapse:collapse;width:100%;\">{team_head}{team_rows or EMPTY_TEAM}</table></div>"
            f"<p style=\"font-size:12px;color:#64748b;margin-top:20px;\">Sent by {html.escape(brand)}. Every number comes from the activity log in the attachment.</p>"
            f"</div></body></html>")


def render_text(report: dict, title: str, period_text: str, link: str) -> str:
    t = report["totals"]
    lines = [title, period_text + " (India time)", ""] + [f"{LABEL[k]}: {t[k]}" for k in TILES] + ["", "What the numbers say:"]
    lines += [f"- {p}" for p in analysis(report)]
    if link:
        lines += ["", f"Download the activity log (CSV, link valid {LINK_DAYS} days): {link}"]
    return "\n".join(lines)


def compose(store, rng: dict, who: str, kind: str, scope: tuple, by: str, base_url: str) -> dict:
    """{subject, body, html, attachments, link} for one report email."""
    report = daily_report.build(store, rng, who, kind)
    token = make_link(store, rng, who, kind, scope, by)
    link = f"{base_url.rstrip('/')}/api/reports/file/{token}"
    period_text = rng["from"] if rng["from"] == rng["to"] else f"{rng['from']} to {rng['to']}"
    title = ("Daily report" if rng["from"] == rng["to"] else "Activity report") + (f" — {who}" if who else "")
    log = daily_report.activity_log(store, rng)
    if who:
        log = [r for r in log if r["who"] == who]
    if kind:
        log = [r for r in log if r["kind"] == kind]
    return {"subject": f"{title}: {period_text}", "body": render_text(report, title, period_text, link),
            "html": render_html(report, title, period_text, link),
            "attachments": [{"filename": f"activity-log-{rng['from']}-to-{rng['to']}.csv",
                             "content": daily_report.csv_text(log).encode("utf-8"), "content_type": "text/csv; charset=utf-8"}],
            "link": link, "range": public_range(rng), "totals": report["totals"]}
