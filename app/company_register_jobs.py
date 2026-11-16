"""Filling the company register: uploaded CSV files and the optional data.gov.in API pull, one job at a time in the
background, with progress.

Upload: the route streams the request body to a file under <data dir>/register-uploads (never held in memory), then
queues it here. The importer reads it row by row (app/company_register_csv.py) and upserts BATCH rows per transaction,
then deletes the file. Several RoC-wise files queue up and run one after another.

Pull: pages through the data.gov.in resource API with the owner's key and resource id (Integrations > data.gov.in),
PULL_PAGE records a page, one page every few seconds (the source's rate limit), at most max_pages pages a run. The offset
is kept in mca_meta after every page, so the next run carries on where the last stopped.
"""
import io
import os
import threading
import time
from pathlib import Path

from . import company_register as R
from .company_register_csv import Counting, map_record, read_csv
from .lead_model import now_iso

MAX_UPLOAD_BYTES = 1536 * 1024 * 1024        # one RoC-wise file; the largest states are a few hundred MB
MIN_FREE_BYTES = 300 * 1024 * 1024
PULL_PAGE = 500
PULL_MAX_PAGES = 400


def upload_dir(store) -> Path:
    data = Path(getattr(store, "path", None) or "data/store.json")
    return data.with_name("register-uploads")


def import_file(repo, path: Path, state: dict, batch: int = R.BATCH) -> dict:
    """Stream one CSV file into the register. `state` is updated as it goes (bytes read, rows, written, skipped)."""
    stats = {"rows": 0, "skipped": 0}
    written, rows = 0, []
    state.update(bytes_total=path.stat().st_size, bytes_done=0, rows=0, written=0, skipped=0)
    with open(path, "rb", buffering=0) as raw:
        counter = Counting(raw)
        for row in read_csv(io.BufferedReader(counter, 1 << 16), stats):
            rows.append(row)
            if len(rows) >= batch:
                written += repo.upsert(rows)
                rows = []
                state.update(bytes_done=counter.read_bytes, rows=stats["rows"], written=written, skipped=stats["skipped"])
        written += repo.upsert(rows)
    state.update(bytes_done=state["bytes_total"], rows=stats["rows"], written=written, skipped=stats["skipped"],
                 columns=stats.get("columns", []), unknown=stats.get("unknown", []))
    return {"rows": stats["rows"], "written": written, "skipped": stats["skipped"], "columns": stats.get("columns", []),
            "unknown": stats.get("unknown", [])}


class Jobs:
    """The import queue and the API pull for one store. Only one job writes to the register at a time."""

    def __init__(self, store, transports=None, sleep=time.sleep):
        self.store, self.transports, self.sleep = store, transports or {}, sleep
        self._write = threading.Lock()
        self._queue_lock = threading.Lock()
        self.queue = []
        self.state = {"status": "idle", "queue": [], "files": []}
        self.pull_state = {"status": "idle"}

    # ---- CSV files
    def enqueue(self, path: Path, name: str, by: str, background: bool = True) -> dict:
        with self._queue_lock:
            self.queue.append((Path(path), name, by))
            running = self.state.get("status") == "running"
            if not running:
                self.state = {"status": "running", "queue": [], "files": [], "current": None, "started_at": now_iso(), "finished_at": ""}
            self.state["queue"] = [n for _, n, _ in self.queue]
        if not running:
            if background:
                threading.Thread(target=self._drain, daemon=True).start()
            else:
                self._drain()
        return self.view()

    def view(self) -> dict:
        return {**self.state, "files": list(self.state.get("files") or []), "queue": list(self.state.get("queue") or [])}

    def _drain(self) -> None:
        repo = R.repo_for(self.store)
        with self._write:
            while True:
                with self._queue_lock:
                    if not self.queue:
                        self.state.update(status="done", current=None, finished_at=now_iso())
                        return
                    path, name, by = self.queue.pop(0)
                    self.state["queue"] = [n for _, n, _ in self.queue]
                current = {"name": name, "started_at": now_iso()}
                self.state["current"] = current
                try:
                    before = repo.count()
                    got = import_file(repo, path, current)
                    stats = R.refresh_stats(repo, now_iso())
                    entry = {"at": now_iso(), "kind": "file", "name": name, "by": by, **got,
                             "added": max(0, stats["total"] - before), "total_after": stats["total"]}
                    R.note_import(repo, entry)
                    self.state["files"].append({**entry, "status": "done"})
                except Exception as exc:              # noqa: BLE001 - a bad file is reported, the next one still runs
                    self.state["files"].append({"name": name, "status": "failed", "error": str(exc)[:300], "at": now_iso()})
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    # ---- data.gov.in API pull
    def start_pull(self, values: dict, by: str, max_pages: int = 50, background: bool = True) -> dict:
        from .sources import data_gov_in
        rid = str(values.get("resource_id") or "").strip()
        if not values.get("api_key") or not data_gov_in.ID_RE.match(rid):
            raise ValueError("Add your data.gov.in API key and the Company Master Data resource id under Integrations > Lead data > "
                             "data.gov.in company master data first.")
        if self.pull_state.get("status") == "running":
            raise RuntimeError("A data.gov.in pull is already running.")
        max_pages = max(1, min(int(max_pages), PULL_MAX_PAGES))
        self.pull_state = {"status": "running", "resource_id": rid, "pages": 0, "max_pages": max_pages, "written": 0,
                           "skipped": 0, "started_at": now_iso(), "finished_at": "", "error": ""}
        args = (values, rid, max_pages, by)
        if background:
            threading.Thread(target=self._pull, args=args, daemon=True).start()
        else:
            self._pull(*args)
        return dict(self.pull_state)

    def _pull(self, values: dict, rid: str, max_pages: int, by: str) -> None:
        from .sources import data_gov_in, registry
        st = self.pull_state
        repo = R.repo_for(self.store)
        ctx = registry.make_ctx(self.store, self.transports).bind(data_gov_in.SPEC, values)
        ctx.sleep = self.sleep
        cursor_key = f"pull:{rid}"
        cursor = repo.meta(cursor_key, {}) or {}
        offset = int(cursor.get("offset") or 0) if not cursor.get("complete") else 0
        st["offset_start"] = offset
        try:
            with self._write:
                before = repo.count()
                for _ in range(max_pages):
                    reply = ctx.get(data_gov_in.BASE + rid, params={"api-key": values["api_key"], "format": "json",
                                                                    "limit": PULL_PAGE, "offset": offset})
                    records = [r for r in (reply.get("records") or []) if isinstance(r, dict)]
                    rows = [m for m in (map_record(r) for r in records) if m]
                    st["written"] += repo.upsert(rows)
                    st["skipped"] += len(records) - len(rows)
                    offset += len(records)
                    st.update(pages=st["pages"] + 1, offset=offset, total=reply.get("total"))
                    complete = len(records) < PULL_PAGE
                    repo.set_meta(cursor_key, {"offset": offset, "total": reply.get("total"), "complete": complete, "at": now_iso()})
                    if complete:
                        st["complete"] = True
                        break
                stats = R.refresh_stats(repo, now_iso())
                R.note_import(repo, {"at": now_iso(), "kind": "api", "name": f"data.gov.in {rid}", "by": by, "rows": st["written"] + st["skipped"],
                                     "written": st["written"], "skipped": st["skipped"], "added": max(0, stats["total"] - before),
                                     "total_after": stats["total"]})
            st["status"] = "done"
        except Exception as exc:                      # noqa: BLE001 - shown on the card; the cursor keeps the progress
            st.update(status="failed", error=str(exc)[:300])
        finally:
            st["finished_at"] = now_iso()


def jobs_for(store, transports=None) -> Jobs:
    jobs = getattr(store, "_register_jobs", None)
    if jobs is None:
        jobs = Jobs(store, transports)
        try:
            store._register_jobs = jobs
        except AttributeError:
            pass
    return jobs
