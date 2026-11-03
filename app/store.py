"""Tiny JSON-file store. Swap for MongoDB later; the interface stays the same.

Saves go to a temporary file first and are swapped in with one rename, so a crash can never leave a half-written
file. `update` changes a collection while holding the lock, so two changes at once cannot overwrite each other.
"""
import json
import os
import threading
from pathlib import Path

EMPTY = {"leads": [], "integrations": {}, "stories": [], "templates": [], "tasks": [], "outbox": [], "settings": {}, "suppressed": [], "jobs": [], "partners": [], "whatsapp_outbox": [], "workflows": []}


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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + ".tmp")
        temp.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self._path)

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
