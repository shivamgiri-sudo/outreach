"""The owner's "Re-check now (max 500)": a background run of email_gate.recheck_stale over the caller's own leads whose
address was not checked within the saved number of days (settings.email_gate.recheck_days, default 90).

One run at a time across the app; its progress is kept in memory (status, done, the result counts). It uses the connected
email verifier, else the free built-in checker without any mailbox (SMTP) step, so it adds no new SMTP traffic."""
import threading

from . import email_gate
from .lead_model import now_iso


class Busy(RuntimeError):
    """A re-check is already running."""


class Recheck:
    def __init__(self, store, transports: dict):
        self.store, self.transports, self._lock = store, transports or {}, threading.Lock()
        self.state = {"status": "idle"}

    def start(self, lead_ids: set, background: bool = True) -> dict:
        if not self._lock.acquire(blocking=False):
            raise Busy("A re-check is already running. Wait for it to finish.")
        cfg = email_gate.settings_of(self.store)
        pick = email_gate.stale_select(cfg)
        total = min(email_gate.RECHECK_MAX, sum(1 for l in self.store.read("leads") if l["id"] in lead_ids and pick(l)))
        self.state = {"status": "running", "done": 0, "total": total, "days": cfg["recheck_days"], "started_at": now_iso(),
                      "finished_at": "", "result": None, "error": ""}
        if background:
            threading.Thread(target=self._run, args=(lead_ids,), daemon=True, name="email-recheck").start()
        else:
            self._run(lead_ids)
        return dict(self.state)

    def _run(self, lead_ids: set) -> None:
        try:
            out = email_gate.recheck_stale(self.store, self.transports, lead_ids, progress=lambda n: self.state.update(done=n))
            self.state.update(status="done", result=out, done=out["checked"], error=out.get("error") or "")
        except Exception as exc:                       # noqa: BLE001 - the card must always get an answer
            self.state.update(status="failed", error=f"The re-check stopped: {str(exc)[:200]}")
        finally:
            self.state["finished_at"] = now_iso()
            self._lock.release()

    def view(self) -> dict:
        return dict(self.state)

