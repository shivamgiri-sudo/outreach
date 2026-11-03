"""Full backup and restore of the data file, for the owner (web page and scripts/backup.py share this code).

A backup is one gzip-compressed JSON file: a header {format, version, created_at, app_commit, counts, ...} and `data`, the
whole store. By default connection keys, OAuth tokens, webhook secrets and the server secret are left out, so the file is
safe to keep on a laptop; connections are then entered again after a restore.

A restore checks the file first and shows what would change (dry run). Confirmed, it waits for any email run to finish,
keeps the current file as store.json.pre-restore-<time>, swaps the data in under the store lock and switches every kind
of real sending off, so nothing goes out until the owner has looked at the restored data.
"""
import gzip
import io
import json
import os
from datetime import datetime, timedelta, timezone
from email.parser import BytesParser
from email.policy import HTTP
from pathlib import Path

from . import sequence_runner
from .integrations import SECRET_WORDS
from .privacy import guard_leads
from .store import EMPTY

FORMAT = "outreach-os-backup"
VERSION = 1
IST = timezone(timedelta(hours=5, minutes=30))
MAX_UPLOAD_BYTES = 200 * 1024 * 1024           # compressed file
MAX_JSON_BYTES = 1024 * 1024 * 1024            # after unpacking: refuses "zip bombs"
SEND_RUN_WAIT_SECONDS = 30
LIVE_FLAGS = ("live_sending", "live_whatsapp", "live_sms")
DROPPED_KEYS = ("tokens", "webhook_tokens")    # OAuth tokens and webhook secrets inside integrations
SCRUBBED_COLLECTIONS = ("integrations", "users")
DROPPED_COLLECTIONS = ("sessions",)
NO_SECRETS_NOTE = ("Connection keys, passwords, OAuth tokens, webhook secrets and the server secret were left out of this "
                   "backup. After a restore, enter the connections again on the Integrations page (and the activity-event "
                   "secret under Setup).")
WITH_SECRETS_NOTE = ("This backup contains connection keys, passwords, tokens and the server secret in plain text. Keep it "
                     "as safe as a password: never email it or put it in a shared folder.")
SENDING_OFF_NOTE = ("Real sending (email, WhatsApp and SMS) is OFF after a restore. Check the data, then switch it on again "
                    "on the Outbox page (email) and the Integrations page (WhatsApp, SMS).")


class BackupError(ValueError):
    """Why a backup file was refused, in plain words; `status` is the HTTP code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def default_data_file() -> Path:
    """Where the app keeps its data: OUTREACH_DATA_FILE, else data/store.json in the project (as app/main.py)."""
    return Path(os.environ.get("OUTREACH_DATA_FILE") or Path(__file__).resolve().parent.parent / "data" / "store.json")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def counts(data: dict) -> dict:
    """Rows per collection (entries for the dict collections such as integrations and settings)."""
    return {key: len(value) if isinstance(value, (list, dict)) else 1 for key, value in data.items()}


def _hub_secret_keys() -> set:
    from .channels.registry import PROVIDERS
    return {f["key"] for plist in PROVIDERS.values() for p in plist for f in p["fields"] if f.get("secret")}


def _is_secret(key, extra: set) -> bool:
    name = str(key).lower()
    return name in extra or any(word in name for word in SECRET_WORDS)


def _scrub(value, extra: set):
    """A copy without secret fields: keys named like secrets, tokens, and the values of custom headers."""
    if isinstance(value, list):
        return [_scrub(v, extra) for v in value]
    if not isinstance(value, dict):
        return value
    out = {}
    for key, item in value.items():
        if key in DROPPED_KEYS or _is_secret(key, extra):
            continue
        if key == "headers" and isinstance(item, list):      # custom connector headers usually carry the API key
            out[key] = [{**h, "value": ""} if isinstance(h, dict) else h for h in item]
            continue
        out[key] = _scrub(item, extra)
    return out


def strip_secrets(data: dict) -> dict:
    extra = _hub_secret_keys()
    out = {k: v for k, v in data.items() if k not in DROPPED_COLLECTIONS}
    out["settings"] = {k: v for k, v in (data.get("settings") or {}).items() if not _is_secret(k, extra)}
    for key in SCRUBBED_COLLECTIONS:
        if key in out:
            out[key] = _scrub(out[key], extra)
    return out


def ist_label(when: datetime) -> str:
    return when.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def build_backup(store, include_secrets: bool = False, now: datetime = None) -> dict:
    now = now or now_utc()
    data = store.snapshot()
    if not include_secrets:
        data = strip_secrets(data)
    return {"format": FORMAT, "version": VERSION, "created_at": now.isoformat(timespec="seconds"),
            "created_at_ist": ist_label(now), "app_commit": os.environ.get("OUTREACH_COMMIT") or None,
            "includes_secrets": bool(include_secrets), "note": WITH_SECRETS_NOTE if include_secrets else NO_SECRETS_NOTE,
            "counts": counts(data), "data": data}


def to_bytes(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"), compresslevel=6)


def backup_filename(include_secrets: bool, now: datetime = None) -> str:
    stamp = (now or now_utc()).astimezone(IST).strftime("%Y-%m-%d-%H%M")
    return f"outreach-backup-{stamp}-IST{'-WITH-SECRETS' if include_secrets else ''}.json.gz"


def file_from_multipart(raw: bytes, content_type: str) -> bytes:
    """The uploaded file's bytes from a multipart/form-data body (the first part that is a file, or is named 'file')."""
    message = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + content_type.encode("latin-1") + b"\r\n\r\n" + raw)
    if not message.is_multipart():
        raise BackupError("The upload could not be read. Send the backup file as the request body or as a form field named 'file'.")
    for part in message.iter_parts():
        if part.get_filename() or part.get_param("name", header="content-disposition") == "file":
            return part.get_payload(decode=True) or b""
    raise BackupError("No file was attached. Choose the backup file (.json.gz) and try again.")


def read_payload(raw: bytes) -> dict:
    """Unpack (gzip or plain JSON) and validate a backup file. Raises BackupError for anything else."""
    if not raw:
        raise BackupError("The file is empty. Choose the backup file (.json.gz) and try again.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise BackupError("The file is too large to be an Outreach OS backup.", 413)
    if raw[:2] == b"\x1f\x8b":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
                raw = handle.read(MAX_JSON_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise BackupError(f"The file looks compressed but could not be unpacked ({exc}). It may be damaged.") from None
        if len(raw) > MAX_JSON_BYTES:
            raise BackupError("The unpacked file is too large to be an Outreach OS backup.", 413)
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise BackupError("This file is not an Outreach OS backup (it is not readable JSON). Use a file made with "
                          "'Download backup' or scripts/backup.py.") from None
    return validate(payload)


def validate(payload) -> dict:
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise BackupError("This file is not an Outreach OS backup. Use a file made with 'Download backup' or scripts/backup.py "
                          "(a raw store.json is copied into place instead; see docs/SELF_HOSTING.md).")
    version = payload.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BackupError("The backup has no valid version number, so it can't be restored safely.")
    if version > VERSION:
        raise BackupError(f"This backup was made by a newer version of Outreach OS (format {version}; this app reads up to "
                          f"{VERSION}). Update the app first, then restore.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BackupError("The backup has no data section, so there is nothing to restore.")
    wrong = [key for key, empty in EMPTY.items() if key in data and not isinstance(data[key], type(empty))]
    if wrong:
        raise BackupError(f"The backup is damaged: {', '.join(wrong)} has the wrong shape.")
    if any(not isinstance(lead, dict) or not isinstance(lead.get("id"), str) or not isinstance(lead.get("stage"), str)
           or not isinstance(lead.get("timeline", []), list) for lead in data.get("leads", [])):
        raise BackupError("The backup is damaged: some leads have no id, stage or history.")
    return payload


def _ordered(names) -> list:
    known = [k for k in EMPTY if k in names]
    return known + sorted(n for n in names if n not in EMPTY)


def summary(store, payload: dict, keep_secrets: bool = False) -> dict:
    """What a restore of this file would do; nothing is changed."""
    now_counts, after_counts = counts(store.snapshot()), counts(payload["data"])
    secrets = bool(payload.get("includes_secrets"))
    notes = [SENDING_OFF_NOTE,
             "The backup includes connection keys and secrets; they will be used." if secrets
             else "The backup has no connection keys: enter the connections again on the Integrations page afterwards.",
             "The server secret from the backup will be used." if secrets and keep_secrets
             else "This server keeps its own server secret (it signs calendar sign-in links); it is never taken from "
                  "another install unless the backup includes secrets and you ask for it."]
    return {"dry_run": True,
            "backup": {k: payload.get(k) for k in ("created_at", "created_at_ist", "app_commit", "version", "includes_secrets")},
            "collections": [{"name": n, "now": now_counts.get(n, 0), "after": after_counts.get(n, 0)}
                            for n in _ordered(set(now_counts) | set(after_counts))],
            "will_replace": "Everything in this app (leads, messages, tasks, campaigns, settings and connections) is replaced "
                            "by the backup. Nothing is merged.",
            "pre_restore_copy": f"The current data file is kept as {store.path.name}.pre-restore-<time> in the same folder.",
            "notes": notes, "message": "Nothing has been changed yet. Confirm to replace everything with this backup."}


def _copy_name(path: Path, now: datetime) -> Path:
    base = path.with_name(f"{path.name}.pre-restore-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    candidate, n = base, 1
    while candidate.exists():
        n += 1
        candidate = base.with_name(f"{base.name}-{n}")
    return candidate


def restore(store, payload: dict, keep_secrets: bool = False, now: datetime = None) -> dict:
    """Replace the whole store with the backup. Sending stays off afterwards; the old file is kept beside the new one."""
    payload = validate(payload)
    now = now or now_utc()
    if not sequence_runner._RUN_LOCK.acquire(timeout=SEND_RUN_WAIT_SECONDS):
        raise BackupError("Emails are being sent right now. Wait a minute and try the restore again.", 409)
    try:
        copy = _copy_name(store.path, now)
        use_backup_secret = bool(payload.get("includes_secrets") and keep_secrets and payload["data"].get("settings", {}).get("server_secret"))

        def swap(current: dict) -> dict:
            data = json.loads(json.dumps(payload["data"]))
            settings = dict(data.get("settings") or {})
            if not use_backup_secret:
                settings.pop("server_secret", None)
                if (current.get("settings") or {}).get("server_secret"):
                    settings["server_secret"] = current["settings"]["server_secret"]
            settings.update({flag: False for flag in LIVE_FLAGS})
            # an older backup must not bring back people erased or unsubscribed since it was made (DPDP; a no stays a no)
            erased = set(data.get("suppressed_hashes") or []) | set(current.get("suppressed_hashes") or [])
            data["suppressed_hashes"] = sorted(erased)
            data["suppressed"] = list(dict.fromkeys([*(data.get("suppressed") or []), *(current.get("suppressed") or [])]))
            data["leads"] = guard_leads([], data.get("leads") or [], erased)
            settings["restored_at"] = now.isoformat(timespec="seconds")
            return {**data, "settings": settings}
        saved = store.replace_all(swap, keep_copy_as=copy)
    finally:
        sequence_runner._RUN_LOCK.release()
    notes = [SENDING_OFF_NOTE]
    if not payload.get("includes_secrets"):
        notes.append("The backup had no connection keys: enter the connections again on the Integrations page.")
    return {"restored": True, "counts": counts(saved), "pre_restore_copy": copy.name,
            "live_sending": False, "live_whatsapp": False, "live_sms": False,
            "server_secret": "from the backup" if use_backup_secret else "kept from this server",
            "message": f"Restored the backup from {payload.get('created_at_ist') or payload.get('created_at')}. "
                       f"The previous data was kept as {copy.name}. " + SENDING_OFF_NOTE,
            "notes": notes}
