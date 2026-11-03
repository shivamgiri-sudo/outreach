"""Optional background timer that sends due sequence emails. Enable with OUTREACH_AUTORUN=1."""
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from .growth_core import public_base_url
from .senders import make_email_sender
from .sequence_runner import run_due
from .workflows import run_time_rules

BASE_URL = os.environ.get("OUTREACH_BASE_URL", "http://localhost:8000")
DEFAULT_INTERVAL_SECONDS = 900


def tick(store, now: Optional[datetime] = None, sender=None) -> dict:
    now = now or datetime.now(timezone.utc)
    sender = sender or make_email_sender(store, base_url=BASE_URL)
    result = run_due(store, now, public_base_url(store, BASE_URL), sender, ignore_hours=os.environ.get("OUTREACH_IGNORE_BUSINESS_HOURS") == "1")
    return {**result, "workflow_rules_run": run_time_rules(store, now)}


def start_background(store, sender=None, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
    """Run tick() every interval until the returned event is set."""
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_seconds):
            try:
                tick(store, sender=sender)
            except Exception as exc:  # keep the timer alive; the next tick retries
                print(f"autorun tick failed: {exc}")

    threading.Thread(target=loop, daemon=True, name="outreach-autorun").start()
    return stop
