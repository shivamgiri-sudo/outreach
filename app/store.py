"""Tiny JSON-file store. Swap for MongoDB later; the interface stays the same.

Saves go to a temporary file first and are swapped in with one rename, so a crash can never leave a half-written
file. `update` changes a collection while holding the lock, so two changes at once cannot overwrite each other.
"""
import json
import os
import shutil
import threading
import time
from pathlib import Path

EMPTY = {"leads": [], "integrations": {}, "stories": [], "templates": [], "tasks": [], "outbox": [], "settings": {}, "suppressed": [], "jobs": [], "partners": [], "whatsapp_outbox": [], "workflows": [], "inbox": [], "delivery_events": []}
BACKUPS_KEPT = 5
BACKUP_EVERY_SECONDS = 60
# Flush every save to disk so a power cut can't lose it. Tests switch this off for speed.
FSYNC = os.environ.get("OUTREACH_FSYNC", "1") != "0"


class Store:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict:
        if not self._path.exists():
            return json.loads(json.dumps(EMPTY))
        try:
            return {**EMPTY, **json.loads(self._path.read_text(encoding="utf-8"))}
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Data file {self._path} is corrupt ({exc}). Restore it from a backup; it was not overwritten.") from exc

    def _save(self) -> None:
        """Write to a private temp file, flush it to disk, keep the previous version as a backup, then swap in."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(self._data, ensure_ascii=False, indent=1))
            handle.flush()
            if FSYNC:
                os.fsync(handle.fileno())
        self._rotate_backup()
        os.replace(temp, self._path)

    def _rotate_backup(self) -> None:
        """Keep the last few saved versions (store.json.1 is the newest) at most once a minute, for a quick restore."""
        if not self._path.exists():
            return
        newest = self._path.with_name(self._path.name + ".1")
        if newest.exists() and time.time() - newest.stat().st_mtime < BACKUP_EVERY_SECONDS:
            return
        for n in range(BACKUPS_KEPT - 1, 0, -1):
            older = self._path.with_name(f"{self._path.name}.{n}")
            if older.exists():
                os.replace(older, self._path.with_name(f"{self._path.name}.{n + 1}"))
        shutil.copy2(self._path, newest)
        os.chmod(newest, 0o600)

    def read(self, key: str):
        with self._lock:
            return json.loads(json.dumps(self._data[key]))

    def write(self, key: str, value) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, key: str, change):
        """Replace a collection with change(current) atomically; `change` must not call the store."""
        with self._lock:
            current = json.loads(json.dumps(self._data[key]))
            self._data[key] = change(current)
            self._save()
            return json.loads(json.dumps(self._data[key]))

    def update_lead(self, lead_id: str, change):
        """Change one lead on its latest saved copy, atomically. `change(lead)` returns the new lead (or None to keep it).

        Returns the saved lead, or None when no lead has that id. Use this instead of reading every lead, editing one
        and writing them all back, which would undo changes the sending engine made in the meantime.
        """
        result = {}

        def apply(leads: list) -> list:
            out = []
            for lead in leads:
                if lead["id"] == lead_id:
                    changed = change(json.loads(json.dumps(lead)))
                    lead = changed if changed is not None else lead
                    result["lead"] = lead
                out.append(lead)
            return out
        self.update("leads", apply)
        return result.get("lead")

    def add_leads(self, new_leads: list) -> list:
        """Append leads whose dedupe key is not already taken, atomically. Returns the leads actually added."""
        added = []

        def apply(leads: list) -> list:
            seen = {lead.get("dedupe_key") for lead in leads}
            for lead in new_leads:
                if lead.get("dedupe_key") not in seen:
                    seen.add(lead.get("dedupe_key"))
                    added.append(lead)
            return [*leads, *added]
        self.update("leads", apply)
        return added

    def patch_leads(self, patches: dict) -> None:
        """Apply small changes to leads by id on top of their latest saved state.

        patches = {lead_id: {"set": {...}, "events": [...]}}. Used after slow work (sending, website lookups) so
        a reply or unsubscribe saved in the meantime is not overwritten. Email and phone are only filled if empty.
        """
        def apply(leads: list) -> list:
            out = []
            for lead in leads:
                patch = patches.get(lead["id"])
                if not patch:
                    out.append(lead)
                    continue
                changes = {k: v for k, v in patch.get("set", {}).items() if k not in ("email", "phone", "dedupe_key") or not lead.get(k if k != "dedupe_key" else "email")}
                out.append({**lead, **changes, "timeline": [*lead["timeline"], *patch.get("events", [])]})
            return out
        self.update("leads", apply)
