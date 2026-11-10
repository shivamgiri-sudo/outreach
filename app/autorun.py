"""Optional background timer that sends due sequence emails. Enable with OUTREACH_AUTORUN=1."""
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from .digest import run_digest
from .growth_core import public_base_url
from .reminders import run_reminders
from .senders import make_email_sender, make_transactional_sender
from .sequence_runner import run_due
from .workflows import run_time_rules
from .system_health import mark, run_summary

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
DEFAULT_INTERVAL_SECONDS = 900          # sequence sends and time rules
REMINDER_INTERVAL_SECONDS = 60          # task and meeting reminders need minute precision


def tick(store, now: Optional[datetime] = None, sender=None, campaign_runner=None) -> dict:
    """Due sequence emails, time rules, and (when given) campaigns marked 'running' that have not run today."""
    now = now or datetime.now(timezone.utc)
    sender = sender or make_email_sender(store, base_url=BASE_URL)
    result = run_due(store, now, public_base_url(store, BASE_URL), sender, ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1")
    mark(store, "sequence_run", not result.get("failed"), run_summary(result), now)
    out = {**result, "workflow_rules_run": run_time_rules(store, now)}
    if campaign_runner is not None:
        out["campaign_runs"] = campaign_runner(now)
    mark(store, "autorun", True, run_summary(result), now)
    return out


def flow_tick(store, now: Optional[datetime] = None, sender=None) -> dict:
    """Flow builder: start leads on polled triggers and run every due flow step (emails keep every sending rule)."""
    from .flow_engine import Context, tick as run_flows
    now = now or datetime.now(timezone.utc)
    ctx = Context(sender=sender or make_email_sender(store, base_url=BASE_URL), base_url=public_base_url(store, BASE_URL),
                  notify_sender=make_transactional_sender(store),
                  ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1")
    return run_flows(store, now, ctx)


def reminder_tick(store, now: Optional[datetime] = None, sender=None) -> dict:
    now = now or datetime.now(timezone.utc)
    sender = sender or make_transactional_sender(store)
    try:
        from . import abuse_alerts
        abuse_alerts.scan(store, now)               # per-account bounce / complaint rates (auto-hold) and sending spikes
    except Exception as exc:                        # a check must never stop the reminders
        print(f"abuse scan failed: {exc}")
    try:
        from . import linkedin_watch
        linkedin_watch.tick(store, now)             # hourly LinkedIn agent summary + "agent not running" alert
    except Exception as exc:
        print(f"linkedin watch failed: {exc}")
    return {**run_reminders(store, now, sender, BASE_URL), "digest": bool(run_digest(store, now, sender))}


def start_background(store, sender=None, interval_seconds: int = DEFAULT_INTERVAL_SECONDS, campaign_runner=None, capture_runner=None) -> threading.Event:
    """Reminders and flow steps every minute; due sequence emails, time rules and daily campaigns every `interval_seconds`."""
    stop = threading.Event()

    def loop():
        waited = 0
        while not stop.wait(REMINDER_INTERVAL_SECONDS):
            waited += REMINDER_INTERVAL_SECONDS
            try:
                reminder_tick(store)
                mark(store, "reminders")
            except Exception as exc:  # keep the timer alive; the next tick retries
                print(f"reminder tick failed: {exc}")
                mark(store, "reminders", False, type(exc).__name__)
            try:
                flow_tick(store, sender=sender)
                mark(store, "flows")
            except Exception as exc:
                print(f"flow tick failed: {exc}")
                mark(store, "flows", False, type(exc).__name__)
            if waited >= interval_seconds:
                waited = 0
                if capture_runner is not None:
                    try:
                        capture_runner()               # lead capture: new rows of published Google Sheets
                        mark(store, "capture_sync")
                    except Exception as exc:
                        print(f"lead capture sync failed: {exc}")
                        mark(store, "capture_sync", False, type(exc).__name__)
                try:
                    tick(store, sender=sender, campaign_runner=campaign_runner)
                except Exception as exc:
                    print(f"autorun tick failed: {exc}")
                    mark(store, "autorun", False, f"failed: {type(exc).__name__}")

    threading.Thread(target=loop, daemon=True, name="outreach-autorun").start()
    return stop
