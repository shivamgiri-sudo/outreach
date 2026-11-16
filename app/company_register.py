"""The local Indian company register: MCA "Company Master Data" (data.gov.in, GODL licence) in one compact table.

Storage follows the prospect database (app/prospect_repo.py): a SQLite file next to the data file
(<data file>.register.sqlite, or OUTREACH_REGISTER_DB) by default, or the same table in MySQL (prefix mca_) when the
store is on MySQL. Create-if-not-exists only, never ALTER at boot, created lazily on first use.

Tables
  mca_companies  one row per CIN: name, name_key (normalised, app/company_match.py), tok_key (its most telling word),
                 status, cls, category, sub_category, incorporated, authorised, paid_up, state, roc, address, activity,
                 nic, email (only when the source file has it), last_return. Indexes: name_key, (tok_key, state).
                 SQLite keeps it WITHOUT ROWID (clustered on the CIN: no second copy of the key).
  mca_meta       k -> v (JSON): stats (counts per state, size), imports (last 20), pull cursors

Writes are batched upserts by CIN (BATCH rows per transaction). A value missing from a newer file keeps the older one.
What the free data does NOT contain: directors, phone numbers, websites.
"""
import json
import os
import threading
from pathlib import Path

from .company_register_csv import COLUMNS
from .prospect_repo import MySqlRepo, SqliteRepo

BATCH = 2000
SOURCE = "mca-master-data"
LICENCE = ("Company Master Data, Ministry of Corporate Affairs, Government of India, published on data.gov.in under the "
           "Government Open Data Licence - India (GODL). Reuse allowed with attribution.")
UPDATABLE = [c for c in COLUMNS if c != "cin"]

SQLITE_DDL = [
    "CREATE TABLE IF NOT EXISTS mca_meta (k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE IF NOT EXISTS mca_companies (cin TEXT PRIMARY KEY, name TEXT, name_key TEXT, tok_key TEXT, status TEXT, "
    "cls TEXT, category TEXT, sub_category TEXT, incorporated TEXT, authorised INTEGER, paid_up INTEGER, state TEXT, roc TEXT, "
    "address TEXT, activity TEXT, nic TEXT, email TEXT, last_return TEXT) WITHOUT ROWID",
]
_X = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
MYSQL_DDL = [
    "CREATE TABLE IF NOT EXISTS mca_meta (k VARCHAR(191) PRIMARY KEY, v MEDIUMTEXT)" + _X,
    "CREATE TABLE IF NOT EXISTS mca_companies (cin VARCHAR(25) PRIMARY KEY, name VARCHAR(255), name_key VARCHAR(191), "
    "tok_key VARCHAR(64), status VARCHAR(60), cls VARCHAR(40), category VARCHAR(80), sub_category VARCHAR(80), "
    "incorporated CHAR(10), authorised BIGINT, paid_up BIGINT, state VARCHAR(40), roc VARCHAR(40), address VARCHAR(400), "
    "activity VARCHAR(255), nic VARCHAR(12), email VARCHAR(120), last_return CHAR(10))" + _X,
]
INDEXES = [("mca_companies", "mca_name", "name_key"), ("mca_companies", "mca_tok", "tok_key, state")]
# No index on state alone: the per-state counts are taken once after an import (a full scan, ~1-2 s for 2M rows) and kept
# in mca_meta; the index would cost ~80 MB at 2M rows.


class RegisterMixin:
    """Schema, upsert and lookups shared by both dialects (the connection and transactions come from prospect_repo)."""
    ddl = SQLITE_DDL

    def ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            cur = self._cursor()
            for stmt in self.ddl:
                cur.execute(stmt)
            self._indexes(cur)
            self._commit()
            self._ready = True

    def _indexes(self, cur) -> None:
        for table, name, cols in INDEXES:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")

    # ---- writes
    def upsert_sql(self) -> str:
        cols = ", ".join(COLUMNS)
        marks = ", ".join("?" for _ in COLUMNS)
        sets = ", ".join(f"{c} = COALESCE(excluded.{c}, mca_companies.{c})" for c in UPDATABLE)
        return f"INSERT INTO mca_companies ({cols}) VALUES ({marks}) ON CONFLICT(cin) DO UPDATE SET {sets}"

    def upsert(self, rows: list) -> int:
        """Write rows (dicts over COLUMNS) in one transaction; the last copy of a CIN inside the batch wins."""
        if not rows:
            return 0
        by_cin = {r["cin"]: r for r in rows}
        self.many(self.upsert_sql(), [[r.get(c) for c in COLUMNS] for r in by_cin.values()])
        return len(by_cin)

    # ---- reads
    def get(self, cin: str):
        return self.q1("SELECT * FROM mca_companies WHERE cin = ?", (str(cin or "").strip().upper(),))

    def by_name(self, key: str, limit: int = 25) -> list:
        return self.q(f"SELECT * FROM mca_companies WHERE name_key = ? LIMIT {int(limit)}", (key,))

    def prefix_sql(self, prefix: str):
        return "name_key >= ? AND name_key < ?", [prefix, prefix + "\U0010ffff"]

    def by_prefix(self, prefix: str, limit: int = 50) -> list:
        if not prefix:
            return []
        where, params = self.prefix_sql(prefix)
        return self.q(f"SELECT * FROM mca_companies WHERE {where} LIMIT {int(limit)}", params)

    def by_tokens(self, toks: list, limit: int = 100) -> list:
        toks = [t for t in toks if t][:3]
        if not toks:
            return []
        marks = ", ".join("?" for _ in toks)
        return self.q(f"SELECT * FROM mca_companies WHERE tok_key IN ({marks}) LIMIT {int(limit)}", toks)

    def count(self) -> int:
        return int(self.scalar("SELECT COUNT(*) AS n FROM mca_companies") or 0)

    def per_state(self) -> list:
        rows = self.q("SELECT state, COUNT(*) AS n FROM mca_companies GROUP BY state ORDER BY n DESC")
        return [{"state": r["state"] or "Not stated", "count": int(r["n"])} for r in rows]

    # ---- meta (JSON values)
    def meta(self, key: str, default=None):
        raw = self.scalar("SELECT v FROM mca_meta WHERE k = ?", (key,))
        try:
            return json.loads(raw) if raw is not None else default
        except ValueError:
            return default

    def set_meta(self, key: str, value) -> None:
        with self.tx():
            self.x("DELETE FROM mca_meta WHERE k = ?", (key,))
            self.x("INSERT INTO mca_meta (k, v) VALUES (?, ?)", (key, json.dumps(value)))


class RegisterSqlite(RegisterMixin, SqliteRepo):
    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the file after a big import (frees the disk the log took)."""
        with self._lock:
            self._conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")


class RegisterMySql(RegisterMixin, MySqlRepo):
    ddl = MYSQL_DDL

    def _indexes(self, cur) -> None:
        """No CREATE INDEX IF NOT EXISTS in MySQL: add only the missing ones, online; a failure is logged, never fatal."""
        cur.execute("SELECT DISTINCT table_name, index_name FROM information_schema.statistics WHERE table_schema = DATABASE()")
        have = {(str(t).lower(), str(i).lower()) for t, i in cur.fetchall()}
        for table, name, cols in INDEXES:
            if (table, name) not in have:
                try:
                    cur.execute(f"CREATE INDEX {name} ON {table} ({cols}) ALGORITHM=INPLACE LOCK=NONE")
                except Exception as exc:             # noqa: BLE001 - lookups still work without it, only slower
                    import logging
                    logging.getLogger(__name__).warning("company register: index %s not created (%s)", name, exc)

    def upsert_sql(self) -> str:
        cols = ", ".join(COLUMNS)
        marks = ", ".join("?" for _ in COLUMNS)
        sets = ", ".join(f"{c} = COALESCE(VALUES({c}), {c})" for c in UPDATABLE)
        return f"INSERT INTO mca_companies ({cols}) VALUES ({marks}) ON DUPLICATE KEY UPDATE {sets}"

    def size_bytes(self) -> int:
        row = self.q1("SELECT COALESCE(SUM(data_length + index_length), 0) AS n FROM information_schema.tables "
                      "WHERE table_schema = DATABASE() AND table_name IN ('mca_companies', 'mca_meta')")
        return int((row or {}).get("n") or 0)

    def prefix_sql(self, prefix: str):
        escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "name_key LIKE ?", [escaped + "%"]


_REPOS = {}
_LOCK = threading.Lock()


def sqlite_path_for(store) -> Path:
    data = Path(getattr(store, "path", None) or "data/store.json")
    return Path(os.environ.get("OUTREACH_REGISTER_DB") or data.with_name(data.name + ".register.sqlite"))


def repo_for(store):
    """MySQL when the store itself is on MySQL, else the SQLite file next to the data file (one per path)."""
    repo = getattr(store, "_register_repo", None)
    if repo is not None:
        return repo
    with _LOCK:
        repo = getattr(store, "_register_repo", None)
        if repo is None:
            url = os.environ.get("OUTREACH_DATABASE_URL", "").strip()
            from .store_mysql import MySqlStore
            if url and isinstance(store, MySqlStore):
                repo = RegisterMySql(url)
            else:
                key = str(sqlite_path_for(store).resolve())
                repo = _REPOS.get(key) or _REPOS.setdefault(key, RegisterSqlite(key))
            try:
                store._register_repo = repo
            except AttributeError:
                pass
    return repo


def forget(store) -> None:
    repo = getattr(store, "_register_repo", None)
    if repo is not None:
        repo.close()
        if isinstance(repo, RegisterSqlite):
            _REPOS.pop(str(repo.path), None)
        try:
            del store._register_repo
        except AttributeError:
            pass


def refresh_stats(repo, now: str) -> dict:
    """Recount after an import (per state, total, size on disk) and keep it in mca_meta so the page reads it cheaply."""
    if hasattr(repo, "checkpoint"):
        repo.checkpoint()
    stats = {"total": repo.count(), "per_state": repo.per_state(), "size_bytes": repo.size_bytes(), "counted_at": now}
    repo.set_meta("stats", stats)
    return stats


def summary(store) -> dict:
    repo = repo_for(store)
    stats = repo.meta("stats", {}) or {}
    imports = repo.meta("imports", []) or []
    return {"total": stats.get("total", 0), "per_state": stats.get("per_state", []), "size_bytes": stats.get("size_bytes", 0),
            "counted_at": stats.get("counted_at", ""), "imports": imports[:20], "last_import": imports[0] if imports else None,
            "licence": LICENCE, "source": SOURCE, "backend": repo.name,
            "not_included": "Directors, phone numbers and websites are not in the free register data."}


def note_import(repo, entry: dict) -> None:
    rows = repo.meta("imports", []) or []
    repo.set_meta("imports", [entry, *rows][:20])
