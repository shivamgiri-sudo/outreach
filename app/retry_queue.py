"""A small store-backed job queue with retries and back-off, run by one background thread per queue.

The same pattern as the DialHalo push queue (app/calltracker.py: ct_outbox), as a reusable helper:
  - jobs are rows in one store collection: {id, status pending|done|failed, attempts, next_at, error, ...};
  - a failed job waits BACKOFF_MINUTES[attempt] before the next try, then becomes "failed" (Retry puts it back);
  - `run(store, job)` returns a short result text, or raises JobError(message, retry=False) for a final refusal;
    any other exception is a retryable failure, so a bug never stops the queue;
  - only the newest `keep_done` finished jobs are kept, and at most `max_pending` jobs wait (beyond that new jobs are
    refused, so a huge import cannot fill the store).
"""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

BACKOFF_MINUTES = (1, 5, 15, 60, 180, 360, 720)        # the same steps as the DialHalo queue


class JobError(Exception):
    def __init__(self, message: str, retry: bool = True):
        super().__init__(message)
        self.retry = retry


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def parse(text) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class RetryQueue:
    def __init__(self, collection: str, run: Callable, name: str, backoff=BACKOFF_MINUTES, keep_done: int = 200,
                 max_pending: int = 5000, after: Optional[Callable] = None):
        self.collection, self.run, self.name, self.backoff = collection, run, name, tuple(backoff)
        self.keep_done, self.max_pending, self.after = keep_done, max_pending, after
        self.max_attempts = len(self.backoff) + 1
        self.kick_enabled = True                      # tests switch the thread off and call process_due themselves
        self._lock = threading.Lock()
        self._running = False

    def enqueue(self, store, jobs: list) -> int:
        """Add jobs (dicts); returns how many were queued (the rest were over max_pending)."""
        stamp, added = iso(now_utc()), {"n": 0}

        def apply(rows: list) -> list:
            waiting = sum(1 for r in rows if r.get("status") == "pending")
            room = max(0, self.max_pending - waiting)
            new = [{"id": uuid.uuid4().hex[:12], "attempts": 0, "status": "pending", "next_at": stamp, "created_at": stamp, **j}
                   for j in jobs[:room]]
            added["n"] = len(new)
            finished = [r for r in rows if r.get("status") != "pending"]
            keep = {r["id"] for r in finished[-self.keep_done:]}
            return [*[r for r in rows if r.get("status") == "pending" or r["id"] in keep], *new]
        if jobs:
            store.update(self.collection, apply)
            if added["n"]:
                self.kick(store)
        return added["n"]

    def _settle(self, job: dict, error: str, retry: bool, result: str, now: datetime) -> dict:
        attempts, stamp = job.get("attempts", 0) + 1, iso(now_utc())
        if not error:
            return {"status": "done", "attempts": attempts, "error": "", "done_at": stamp, "result": result}
        if retry and attempts < self.max_attempts:
            return {"attempts": attempts, "error": error, "next_at": iso(now + timedelta(minutes=self.backoff[attempts - 1]))}
        return {"status": "failed", "attempts": attempts, "error": error, "failed_at": stamp}

    def process_due(self, store, now: Optional[datetime] = None, limit: int = 25) -> int:
        now = now or now_utc()
        due = [j for j in store.read(self.collection) if j.get("status") == "pending" and (parse(j.get("next_at")) or now) <= now][:limit]
        for job in due:
            result, error, retry = "", "", True
            try:
                result = self.run(store, job) or ""
            except JobError as exc:
                error, retry = str(exc) or "failed", exc.retry
            except Exception as exc:                  # noqa: BLE001 - a bug must not stop the queue; the job stays visible
                error = f"Unexpected error ({type(exc).__name__})."
            change = self._settle(job, error, retry, result, now)
            store.update(self.collection, lambda rows, jid=job["id"], c=change: [{**r, **c} if r["id"] == jid else r for r in rows])
            if self.after:
                self.after(store, {**job, **change})
        return len(due)

    def retry_failed(self, store, keep: Callable = lambda job: True) -> int:
        stamp, n = iso(now_utc()), {"n": 0}

        def apply(rows: list) -> list:
            out = []
            for r in rows:
                if r.get("status") == "failed" and keep(r):
                    r = {**r, "status": "pending", "attempts": 0, "next_at": stamp}
                    n["n"] += 1
                out.append(r)
            return out
        store.update(self.collection, apply)
        if n["n"]:
            self.kick(store)
        return n["n"]

    def kick(self, store) -> None:
        """Start the worker thread unless one is running; it sleeps until the next job is due (at most a minute)."""
        if not self.kick_enabled:
            return
        with self._lock:
            if self._running:
                return
            self._running = True

        def loop():
            try:
                while True:
                    self.process_due(store)
                    with self._lock:
                        pending = [parse(j.get("next_at")) for j in store.read(self.collection) if j.get("status") == "pending"]
                        if not pending:
                            self._running = False
                            return
                    soonest = min((p for p in pending if p), default=None)
                    time.sleep(min(60.0, max(1.0, (soonest - now_utc()).total_seconds())) if soonest else 1.0)
            except Exception as exc:                  # noqa: BLE001 - never kill the app; the next kick restarts it
                print(f"{self.name} worker stopped: {type(exc).__name__}")
                with self._lock:
                    self._running = False
        threading.Thread(target=loop, daemon=True, name=self.name).start()
