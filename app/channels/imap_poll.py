"""Reply capture by IMAP: read new messages from the mailbox and hand each one to the inbound processor.

Messages are fetched with BODY.PEEK (so reading does not mark them), then marked as read only after the
processor accepted them (when 'mark as read' is on). The highest processed UID is remembered so nothing is
handled twice; the very first check only looks back a few days so an old mailbox is not replayed.
"""
import imaplib
import re
import ssl
from datetime import datetime, timedelta, timezone

from .config import config_for, set_state
from .inbound_parse import email_from_mime

FIRST_RUN_DAYS = 3
DEFAULT_LIMIT = 50
FOLDER = re.compile(r'^[^"\r\n\\]{1,100}$')


def default_imap_factory(host: str, port: int):
    return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(), timeout=30)


def _criteria(mark_seen: bool, last_uid: int, today) -> list:
    criteria = ["UNSEEN"] if mark_seen else ["ALL"]
    if last_uid:
        criteria += ["UID", f"{last_uid + 1}:*"]
    else:
        criteria += ["SINCE", (today - timedelta(days=FIRST_RUN_DAYS)).strftime("%d-%b-%Y")]
    return criteria


def _raw(parts) -> bytes:
    for part in parts or []:
        if isinstance(part, tuple) and len(part) > 1 and isinstance(part[1], (bytes, bytearray)):
            return bytes(part[1])
    return b""


def open_mailbox(values: dict, imap_factory=None):
    folder = values.get("folder") or "INBOX"
    if not FOLDER.match(folder):
        raise ValueError("The folder name has characters that are not allowed.")
    client = (imap_factory or default_imap_factory)(values["imap_host"], int(values.get("imap_port") or 993))
    try:
        client.login(values["username"], values["password"])
    except imaplib.IMAP4.error as exc:
        _logout(client)
        raise PermissionError("The mailbox rejected the username or password (Gmail needs an app password).") from exc
    typ, _ = client.select(f'"{folder}"', readonly=values.get("mark_seen") == "no")
    if typ != "OK":
        _logout(client)
        raise OSError(f"Could not open the folder {folder}.")
    return client


def _logout(client) -> None:
    try:
        client.logout()
    except (imaplib.IMAP4.error, OSError):
        pass


def check_mailbox(store, imap_factory=None) -> dict:
    """Test connection: log in and count unread messages. Changes nothing."""
    cfg = config_for(store, "inbound_email", "imap")
    if not cfg:
        return {"ok": False, "message": "Save the mailbox details first."}
    try:
        client = open_mailbox({**cfg["values"], "mark_seen": "no"}, imap_factory)
    except PermissionError as exc:
        return {"ok": False, "message": str(exc)}
    except (OSError, ValueError, imaplib.IMAP4.error) as exc:
        return {"ok": False, "message": f"Could not connect to the mailbox: {exc}"}
    try:
        typ, data = client.uid("search", None, "UNSEEN")
        count = len(data[0].split()) if typ == "OK" and data and data[0] else 0
    finally:
        _logout(client)
    return {"ok": True, "message": f"Connected. {count} unread message(s) in {cfg['values'].get('folder') or 'INBOX'}."}


def poll_imap(store, processor, imap_factory=None, limit: int = DEFAULT_LIMIT, now: datetime = None) -> dict:
    cfg = config_for(store, "inbound_email", "imap")
    if not cfg:
        return {"ok": False, "message": "Set up the mailbox first.", "fetched": 0, "processed": 0}
    values = cfg["values"]
    mark_seen = values.get("mark_seen") != "no"
    last_uid = int((cfg["record"].get("state") or {}).get("last_uid") or 0)
    now = now or datetime.now(timezone.utc)
    try:
        client = open_mailbox(values, imap_factory)
    except (PermissionError, OSError, ValueError, imaplib.IMAP4.error) as exc:
        return {"ok": False, "message": str(exc), "fetched": 0, "processed": 0}
    processed, highest, failed, uids = 0, last_uid, "", []
    try:
        typ, data = client.uid("search", None, *_criteria(mark_seen, last_uid, now))
        uids = sorted(int(u) for u in (data[0].split() if typ == "OK" and data and data[0] else []) if int(u) > last_uid)[:limit]
        for uid in uids:
            typ, parts = client.uid("fetch", str(uid), "(BODY.PEEK[])")
            raw = _raw(parts) if typ == "OK" else b""
            if not raw:
                continue
            try:
                processor(store, email_from_mime(raw, "imap", str(uid)))
            except Exception as exc:  # stop here; this message is retried on the next check
                failed = f"Message {uid} could not be processed ({type(exc).__name__})."
                break
            if mark_seen:
                client.uid("store", str(uid), "+FLAGS", "(\\Seen)")
            processed, highest = processed + 1, uid
    except (imaplib.IMAP4.error, OSError) as exc:
        failed = f"Mailbox error ({exc})."
    finally:
        _logout(client)
    set_state(store, "inbound_email", "imap", last_uid=highest, last_poll_at=now.isoformat(timespec="seconds"))
    message = f"Checked the mailbox: {processed} new message(s) handled." + (f" {failed}" if failed else "")
    return {"ok": not failed, "message": message, "fetched": len(uids), "processed": processed}
