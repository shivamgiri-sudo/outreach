"""Background mailbox check for reply capture (started with OUTREACH_AUTORUN=1, like the sending timer)."""
import threading

from .config import config_for
from .imap_poll import poll_imap

DEFAULT_INTERVAL_SECONDS = 300


def poll_once(store, processor=None, imap_factory=None) -> dict:
    from ..system_health import mark
    if not config_for(store, "inbound_email", "imap"):
        return {"ok": False, "message": "IMAP reply capture is not set up.", "processed": 0}
    if processor is None:
        from ..inbound import process_inbound_message as processor
    try:
        result = poll_imap(store, processor, imap_factory)
    except Exception as exc:
        mark(store, "imap_poll", False, f"failed: {type(exc).__name__}")
        raise
    mark(store, "imap_poll", bool(result.get("ok", True)), result.get("message") or f"{result.get('processed', 0)} processed")
    return result


def start_imap_poller(store, imap_factory=None, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> threading.Event:
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_seconds):
            try:
                poll_once(store, imap_factory=imap_factory)
            except Exception as exc:  # keep the timer alive; the next round retries
                print(f"imap poll failed: {type(exc).__name__}")

    threading.Thread(target=loop, daemon=True, name="outreach-imap-poller").start()
    return stop
