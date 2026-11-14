"""Full backup and restore of the data file, for the owner (web page and scripts/backup.py share this code).

A backup is one gzip-compressed JSON file: a header {format, version, created_at, app_commit, counts, ...} and `data`, the
whole store. By default connection keys, OAuth tokens, webhook secrets and the server secret are left out, so the file is
safe to keep on a laptop; connections are then entered again after a restore.

A restore checks the file first and shows what would change (dry run). Confirmed, it waits for any email run to finish,
keeps the current file as store.json.pre-restore-<time>, swaps the data in under the store lock and switches every kind
of real sending off, so nothing goes out until the owner has looked at the restored data.

Security (downloading with secrets and restoring are for the super admin only, see routes_exports):
  - the logins (settings.users with password hashes and two-step data), the server secret, the two-step switch and the
    pairing tokens are NEVER taken from an uploaded file: this server keeps its own, and the result says what was skipped.
  - a backup made with secrets carries an HMAC signature keyed with this server's secret. A file whose signature is
    missing or wrong (older backups, another server's, or an edited file) is restored WITHOUT any of its secrets.
"""
import gzip
import hashlib
import hmac
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
SCRUBBED_COLLECTIONS = ("integrations", "users", "ct_connections")
DROPPED_COLLECTIONS = ("sessions",)
# settings that hold login credentials: password hashes, runner token hashes, the pending runner pairing code
LOGIN_HASH_FIELDS = {"users": ("password_hash", "two_step_secret", "two_step_pending", "two_step_recovery"),
                     "linkedin_runners": ("hash",)}
DROPPED_SETTINGS = ("linkedin_runner_pairing", "password_resets", "signup_requests", "oidc_pending", "magic_links")
NO_SECRETS_NOTE = ("Connection keys, login password hashes, pairing tokens, OAuth tokens, webhook secrets and the server "
                   "secret were left out of this backup. After a restore, enter the connections again on the Integrations "
                   "page (and the activity-event secret under Setup). Team logins that already exist keep their current "
                   "password; logins only in the backup need a new password from the owner (Team page).")
WITH_SECRETS_NOTE = ("This backup contains connection keys, login password hashes, tokens and the server secret in plain text. "
                     "Keep it as safe as a password: never email it or put it in a shared folder.")
# settings an upload never replaces: this server's logins (password hashes, two-step), secret and pairing tokens
KEPT_FROM_SERVER = ("users", "server_secret", "require_two_step", "extension_tokens", "password_resets", "signup_requests",
                    "oidc_pending", "magic_links")
UNSIGNED_NOTE = ("This backup says it includes secrets, but it is not signed by this server (an older backup, one made on "
                 "another server, or a changed file), so it is restored WITHOUT its connection keys and secrets.")
KEPT_NOTE = ("Team logins (passwords and two-step login), the server secret and pairing tokens always stay as they are on "
             "this server; they are never taken from a backup file.")
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
    settings = {k: v for k, v in (data.get("settings") or {}).items() if not _is_secret(k, extra) and k not in DROPPED_SETTINGS}
    for key, fields in LOGIN_HASH_FIELDS.items():
        if isinstance(settings.get(key), list):
            settings[key] = [{k: v for k, v in row.items() if k not in fields} if isinstance(row, dict) else row
                             for row in settings[key]]
    out["settings"] = settings
    for key in SCRUBBED_COLLECTIONS:
        if key in out:
            out[key] = _scrub(out[key], extra)
    return out


def ist_label(when: datetime) -> str:
    return when.astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def _signing_key(store) -> bytes:
    from .channels.security import server_secret
    return hashlib.sha256(("backup-signature:" + server_secret(store)).encode()).digest()


def signature(store, payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "signature"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "v1:" + hmac.new(_signing_key(store), text.encode("utf-8"), hashlib.sha256).hexdigest()


def signed_by_this_server(store, payload: dict) -> bool:
    given = payload.get("signature")
    return isinstance(given, str) and hmac.compare_digest(given, signature(store, payload))


def trusted(store, payload: dict) -> tuple:
    """(payload to use, note or ''). A backup claiming secrets without this server's valid signature loses them."""
    if not payload.get("includes_secrets") or signed_by_this_server(store, payload):
        return payload, ""
    return {**payload, "includes_secrets": False, "data": strip_secrets(payload["data"])}, UNSIGNED_NOTE


def build_backup(store, include_secrets: bool = False, now: datetime = None) -> dict:
    now = now or now_utc()
    data = store.snapshot()
    if not include_secrets:
        data = strip_secrets(data)
    payload = {"format": FORMAT, "version": VERSION, "created_at": now.isoformat(timespec="seconds"),
               "created_at_ist": ist_label(now), "app_commit": os.environ.get("OUTREACH_COMMIT") or None,
               "includes_secrets": bool(include_secrets), "note": WITH_SECRETS_NOTE if include_secrets else NO_SECRETS_NOTE,
               "counts": counts(data), "data": data}
    if include_secrets:
        payload = json.loads(json.dumps(payload, ensure_ascii=False, default=str))    # sign exactly what the file will hold
        payload["signature"] = signature(store, payload)
    return payload


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
    """What a restore of this file would do; nothing is changed. `keep_secrets` is accepted for old callers and ignored:
    the server secret is never taken from a file."""
    payload, unsigned = trusted(store, validate(payload))
    now_counts, after_counts = counts(store.snapshot()), counts(payload["data"])
    secrets = bool(payload.get("includes_secrets"))
    logins = login_plan(store.read("settings"), payload["data"].get("settings") or {})
    notes = [SENDING_OFF_NOTE, *([unsigned] if unsigned else []),
             "The backup includes connection keys and secrets; they will be used." if secrets
             else "The backup has no connection keys: enter the connections again on the Integrations page afterwards.",
             KEPT_NOTE, login_note(logins)]
    return {"dry_run": True, "logins": logins, "skipped": skipped_settings(payload["data"].get("settings") or {}),
            "backup": {**{k: payload.get(k) for k in ("created_at", "created_at_ist", "app_commit", "version", "includes_secrets")},
                       "signed": not unsigned and secrets},
            "collections": [{"name": n, "now": now_counts.get(n, 0), "after": after_counts.get(n, 0)}
                            for n in _ordered(set(now_counts) | set(after_counts))],
            "will_replace": "Everything in this app (leads, messages, tasks, campaigns, settings and connections) is replaced "
                            "by the backup, except the logins and the server secret. Nothing is merged.",
            "pre_restore_copy": f"The current data file is kept as {store.path.name}.pre-restore-<time> in the same folder.",
            "notes": notes, "message": "Nothing has been changed yet. Confirm to replace everything with this backup."}


def _rows(settings: dict, key: str) -> list:
    return [r for r in (settings or {}).get(key) or [] if isinstance(r, dict)]


def skipped_settings(data: dict) -> list:
    """Settings present in the file that the restore leaves as they are on this server."""
    return [k for k in KEPT_FROM_SERVER if k in (data or {})]


def login_plan(current: dict, data: dict) -> dict:
    """Logins are never restored: those on this server stay exactly as they are; those only in the backup are skipped."""
    now_users = [r.get("email") for r in _rows(current, "users") if r.get("email")]
    skipped = [r["email"] for r in _rows(data, "users") if r.get("email") and r["email"] not in now_users]
    return {"kept": now_users, "skipped": skipped}


def login_note(plan: dict) -> str:
    text = f"Team logins on this server stay as they are ({len(plan['kept'])})"
    if plan["skipped"]:
        text += (f". Logins only in the backup are not restored ({len(plan['skipped'])}: {', '.join(plan['skipped'][:5])}"
                 f"{'…' if len(plan['skipped']) > 5 else ''}); add them again on the Team page if they still need access")
    return text + "."


def keep_current_logins(current: dict, settings: dict, secrets: bool = False) -> dict:
    """This server's logins (with password hashes and two-step data), server secret, two-step switch and pairing tokens
    replace whatever the file had. Runner token hashes come from the file only in a signed backup with secrets; else they
    are kept from this server by runner id."""
    current = current or {}
    out = {k: v for k, v in settings.items() if k not in KEPT_FROM_SERVER}
    out.update({k: current[k] for k in KEPT_FROM_SERVER if k in current})
    if "linkedin_runners" in settings and not secrets:
        runner_hash = {r.get("id"): r.get("hash") for r in _rows(current, "linkedin_runners") if r.get("hash")}
        out["linkedin_runners"] = [{**r, "hash": runner_hash[r.get("id")]} if r.get("id") in runner_hash
                                   else {k: v for k, v in r.items() if k != "hash"} for r in _rows(settings, "linkedin_runners")]
    return out


def _copy_name(path: Path, now: datetime) -> Path:
    base = path.with_name(f"{path.name}.pre-restore-{now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    candidate, n = base, 1
    while candidate.exists():
        n += 1
        candidate = base.with_name(f"{base.name}-{n}")
    return candidate


def restore(store, payload: dict, keep_secrets: bool = False, now: datetime = None) -> dict:
    """Replace the whole store with the backup. Sending stays off afterwards; the old file is kept beside the new one.
    Logins, the server secret and pairing tokens stay this server's (`keep_secrets` is ignored)."""
    payload, unsigned = trusted(store, validate(payload))
    now = now or now_utc()
    if not sequence_runner._RUN_LOCK.acquire(timeout=SEND_RUN_WAIT_SECONDS):
        raise BackupError("Emails are being sent right now. Wait a minute and try the restore again.", 409)
    try:
        copy = _copy_name(store.path, now)
        logins = login_plan(store.read("settings"), payload["data"].get("settings") or {})
        skipped = skipped_settings(payload["data"].get("settings") or {})

        def swap(current: dict) -> dict:
            data = json.loads(json.dumps(payload["data"]))
            settings = keep_current_logins(current.get("settings") or {}, dict(data.get("settings") or {}),
                                           bool(payload.get("includes_secrets")))
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
    notes = [SENDING_OFF_NOTE, *([unsigned] if unsigned else [])]
    if not payload.get("includes_secrets"):
        notes.append("The backup had no connection keys: enter the connections again on the Integrations page.")
    notes += [KEPT_NOTE, login_note(logins)]
    return {"logins": logins, "skipped": skipped, "restored": True, "counts": counts(saved), "pre_restore_copy": copy.name,
            "live_sending": False, "live_whatsapp": False, "live_sms": False, "server_secret": "kept from this server",
            "secrets_used": bool(payload.get("includes_secrets")),
            "message": f"Restored the backup from {payload.get('created_at_ist') or payload.get('created_at')}. "
                       f"The previous data was kept as {copy.name}. " + SENDING_OFF_NOTE,
            "notes": notes}
