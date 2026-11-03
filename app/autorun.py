"""Optional background timer that sends due sequence emails. Enable with OUTREACH_AUTORUN=1."""
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from .growth_core import public_base_url
from .reminders import run_reminders
from .senders import make_email_sender, make_transactional_sender
from .sequence_runner import run_due
from .workflows import run_time_rules

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
DEFAULT_INTERVAL_SECONDS = 900          # sequence sends and time rules
REMINDER_INTERVAL_SECONDS = 60          # task and meeting reminders need minute precision


def tick(store, now: Optional[datetime] = None, sender=None) -> dict:
    now = now or datetime.now(timezone.utc)
    sender = sender or make_email_sender(store, base_url=BASE_URL)
    result = run_due(store, now, public_base_url(store, BASE_URL), sender, ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1")
    return {**result, "workflow_rules_run": run_time_rules(store, now)}


def reminder_tick(store, now: Optional[datetime] = None, sender=None) -> dict:
    now = now or datetime.now(timezone.utc)
    return run_reminders(store, now, sender or make_transactional_sender(store), BASE_URL)


def start_background(store, sender=None, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
    """Reminders every minute; due sequence emails and time rules every `interval_seconds`, until the event is set."""
    stop = threading.Event()

    def loop():
        waited = 0
        while not stop.wait(REMINDER_INTERVAL_SECONDS):
            waited += REMINDER_INTERVAL_SECONDS
            try:
                reminder_tick(store)
            except Exception as exc:  # keep the timer alive; the next tick retries
                print(f"reminder tick failed: {exc}")
            if waited >= interval_seconds:
                waited = 0
                try:
                    tick(store, sender=sender)
                except Exception as exc:
                    print(f"autorun tick failed: {exc}")

    threading.Thread(target=loop, daemon=True, name="outreach-autorun").start()
    return stop
