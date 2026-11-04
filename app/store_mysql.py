"""MySQL-backed store: the same interface as the JSON-file Store (read / write / update / snapshot / replace_all),
so nothing else in the app changes. Each collection is one row in `collections` (name, doc JSON text); only the
collections that changed are written, inside one transaction. Reads come from memory, as before (one worker).

Set OUTREACH_DATABASE_URL=mysql://user:password@host:3306/dbname (works with Oracle MySQL HeatWave Always Free).
On the first start with an empty table, an existing JSON data file is imported once and left untouched.
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import unquote, urlparse

from .secretbox import SecretBox
from .store import EMPTY, Store

TABLE_SQL = ("CREATE TABLE IF NOT EXISTS collections ("
             "name VARCHAR(64) NOT NULL PRIMARY KEY, doc LONGTEXT NOT NULL, "
             "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
             ") CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")


def parse_url(url: str) -> dict:
    parts = urlparse(url)
    if parts.scheme not in ("mysql", "mysql+pymysql") or not parts.hostname or not parts.path.strip("/"):
        raise ValueError("OUTREACH_DATABASE_URL must look like mysql://user:password@host:3306/database")
    return {"host": parts.hostname, "port": parts.port or 3306, "user": unquote(parts.username or ""),
            "password": unquote(parts.password or ""), "database": parts.path.strip("/")}


class MySqlStore(Store):
    def __init__(self, path: Path, url: str, box: SecretBox = None, connect=None):
        self._conn_args = parse_url(url)
        self._connect_fn = connect
        self._conn = None
        self._written = {}                      # collection -> hash of the text last written
        super().__init__(path, box)

    # ---- connection
    def _connection(self):
        if self._conn is None:
            if self._connect_fn:
                self._conn = self._connect_fn(**self._conn_args)
            else:
                import pymysql
                self._conn = pymysql.connect(**self._conn_args, charset="utf8mb4", autocommit=False,
                                             connect_timeout=10, read_timeout=60, write_timeout=60)
        else:
            self._conn.ping(reconnect=True)
        return self._conn

    def _run(self, work):
        """Run work(cursor) in one transaction; one retry on a dropped connection."""
        for attempt in (1, 2):
            try:
                conn = self._connection()
                with conn.cursor() as cur:
                    result = work(cur)
                conn.commit()
                return result
            except Exception:
                try:
                    if self._conn:
                        self._conn.rollback()
                except Exception:
                    pass
                self._conn = None
                if attempt == 2:
                    raise

    # ---- Store hooks
    def _load(self) -> dict:
        def read(cur):
            cur.execute(TABLE_SQL)
            cur.execute("SELECT name, doc FROM collections")
            return {name: doc for name, doc in cur.fetchall()}
        rows = self._run(read)
        if not rows and self._path.exists():                # first start on MySQL: import the JSON file once
            data = super()._load()
            self._data = data
            self._save()
            return data
        data = {**json.loads(json.dumps(EMPTY)), **{name: json.loads(doc) for name, doc in rows.items()}}
        self._written = {name: hashlib.sha256(doc.encode()).hexdigest() for name, doc in rows.items()}
        return self._box.unseal(data)

    def _save(self) -> None:
        sealed = self._box.seal(self._data)
        changed = {}
        for name, value in sealed.items():
            text = json.dumps(value, ensure_ascii=False)
            digest = hashlib.sha256(text.encode()).hexdigest()
            if self._written.get(name) != digest:
                changed[name] = (text, digest)
        if not changed:
            return

        def write(cur):
            for name, (text, _digest) in changed.items():
                cur.execute("REPLACE INTO collections (name, doc) VALUES (%s, %s)", (name, text))
        self._run(write)
        self._written.update({name: digest for name, (_t, digest) in changed.items()})

    def _rotate_backup(self) -> None:         # the database keeps its own backups
        return

    def _seal_backups(self) -> None:
        return
