"""Storage for the prospect database: a small repository over SQL, not the JSON collections.

A collection in the JSON store is one document, so 100k prospects cannot live there. This module keeps them in real
tables with indexes and full-text search:

  SQLite (default)  a file next to the data file: <data file>.prospects.sqlite, WAL mode, FTS5 for name/title/company/
                    keywords (external-content tables kept in step by triggers).
  MySQL             when OUTREACH_DATABASE_URL is mysql://…, the same tables (prefix pdb_) with FULLTEXT indexes.

Schema changes are create-if-not-exists only (no ALTER at boot), and the schema is created lazily on first use, so a
start never waits on this database. `repo_for(store)` returns the one repository for a store.

Tables
  pdb_companies   one row per company: id, domain, name, filter columns (industry_key, emp_min/emp_max, revenue_key,
                  founded, city_l/state_l/country_l, tech_text, kw_text, source_text), signal_score, people_count,
                  suppressed, first_seen/last_seen/last_verified, doc (JSON: every field with provenance)
  pdb_people      one row per person: id, company_id, company_domain, name_key, title, seniority, department, email,
                  email_status, email_confidence, has_email/has_phone, location keys, source_text, rank_score,
                  suppressed, first_seen/last_seen/last_verified, doc (JSON)
  pdb_keys        identity keys -> row id (e:<email>, l:<linkedin>, n:<name>|<domain> for people; d:<domain>,
                  n:<name>|<city> for companies)
  pdb_signals     company signals (hiring, funding, news, tech_change, expansion, leadership_change)
  pdb_lookups     one row per provider lookup (provider, field, hit/miss, cached, cost, month) for spend tracking
  pdb_suppressions / pdb_erased / pdb_email_marks / pdb_crm_links / pdb_saved_searches / pdb_meta
"""
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = "1"

DDL = [
    "CREATE TABLE IF NOT EXISTS pdb_meta (k {K} PRIMARY KEY, v {T})",
    """CREATE TABLE IF NOT EXISTS pdb_companies (
        id {K} PRIMARY KEY, domain {K}, name {T}, name_key {K}, city_key {K},
        industry {T}, industry_key {K}, employees {I}, emp_min {I}, emp_max {I}, revenue_range {T}, revenue_key {K},
        founded {I}, hq_city {T}, hq_state {T}, hq_country {T}, city_l {K}, state_l {K}, country_l {K},
        tech_text {T}, kw_text {T}, source_text {T}, search_all {T},
        signal_score {R} DEFAULT 0, last_signal_at {K}, people_count {I} DEFAULT 0, suppressed {I} DEFAULT 0,
        first_seen {K}, last_seen {K}, last_verified {K}, doc {L}){X}""",
    """CREATE TABLE IF NOT EXISTS pdb_people (
        id {K} PRIMARY KEY, company_id {K}, company_domain {K}, full_name {K}, name_key {K}, title {T},
        seniority {K}, department {K}, email {K}, email_status {K}, email_confidence {I} DEFAULT 0,
        has_email {I} DEFAULT 0, has_phone {I} DEFAULT 0, linkedin_url {T}, city {T}, state {T}, country {T},
        city_l {K}, state_l {K}, country_l {K}, source_text {T}, search_all {T}, rank_score {R} DEFAULT 0,
        c_signal {R} DEFAULT 0, c_employees {I}, suppressed {I} DEFAULT 0, first_seen {K}, last_seen {K}, last_verified {K}, doc {L}){X}""",
    "CREATE TABLE IF NOT EXISTS pdb_keys (k {KK} PRIMARY KEY, ref {K}){X}",
    """CREATE TABLE IF NOT EXISTS pdb_signals (id {AUTO}, company_id {K}, kind {K}, title {T}, url {T}, at {K},
        weight {R} DEFAULT 1, source {K}, dedupe {K}){X}""",
    "CREATE TABLE IF NOT EXISTS pdb_suppressions (value {KK} PRIMARY KEY, kind {K}, reason {T}, at {K}){X}",
    "CREATE TABLE IF NOT EXISTS pdb_erased (h {K} PRIMARY KEY, at {K}){X}",
    "CREATE TABLE IF NOT EXISTS pdb_email_marks (email {KK} PRIMARY KEY, status {K}, reason {T}, at {K}){X}",
    """CREATE TABLE IF NOT EXISTS pdb_crm_links (lead_id {K} PRIMARY KEY, person_id {K}, company_id {K}, workspace {K},
        fp {K}, synced_at {K}){X}""",
    """CREATE TABLE IF NOT EXISTS pdb_saved_searches (id {K} PRIMARY KEY, owner {K}, name {T}, kind {K}, filters {L},
        created_at {K}){X}""",
    """CREATE TABLE IF NOT EXISTS pdb_lookups (id {AUTO}, at {K}, month {K}, provider {K}, field {K}, target {T},
        hit {I} DEFAULT 0, cached {I} DEFAULT 0, cost {R} DEFAULT 0){X}""",
]
INDEXES = [
    ("pdb_companies", "c_domain", "domain"), ("pdb_companies", "c_name_city", "name_key, city_key"),
    ("pdb_companies", "c_industry", "industry_key"), ("pdb_companies", "c_emp", "emp_min, emp_max"),
    ("pdb_companies", "c_country", "country_l"), ("pdb_companies", "c_city", "city_l"), ("pdb_companies", "c_seen", "last_seen"),
    ("pdb_companies", "c_signal", "signal_score"), ("pdb_companies", "c_founded", "founded"), ("pdb_companies", "c_revenue", "revenue_key"),
    ("pdb_people", "p_company", "company_id"), ("pdb_people", "p_domain", "company_domain"), ("pdb_people", "p_email", "email"),
    ("pdb_people", "p_seniority", "seniority"), ("pdb_people", "p_department", "department"),
    ("pdb_people", "p_status", "email_status"), ("pdb_people", "p_country", "country_l"), ("pdb_people", "p_city", "city_l"),
    ("pdb_people", "p_seen", "last_seen"), ("pdb_people", "p_rank", "rank_score, last_seen"), ("pdb_people", "p_csignal", "c_signal, rank_score"),
    ("pdb_people", "p_cemp", "c_employees, rank_score"), ("pdb_people", "p_fullname", "full_name"), ("pdb_people", "p_name", "name_key"),
    ("pdb_keys", "k_ref", "ref"), ("pdb_signals", "s_company", "company_id"), ("pdb_signals", "s_kind_at", "kind, at"),
    ("pdb_signals", "s_dedupe", "company_id, dedupe"), ("pdb_crm_links", "l_person", "person_id"),
    ("pdb_crm_links", "l_company", "company_id"), ("pdb_saved_searches", "ss_owner", "owner"),
    ("pdb_lookups", "lk_month", "month, provider"),
]
SQLITE_FTS = [
    "CREATE VIRTUAL TABLE IF NOT EXISTS pdb_people_fts USING fts5(title, search_all, content='pdb_people', "
    "content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')",
    "CREATE VIRTUAL TABLE IF NOT EXISTS pdb_companies_fts USING fts5(name, search_all, content='pdb_companies', "
    "content_rowid='rowid', tokenize='unicode61 remove_diacritics 2')",
]
for _t, _cols in (("pdb_people", ("title", "search_all")), ("pdb_companies", ("name", "search_all"))):
    _c = ", ".join(_cols)
    _new = ", ".join(f"new.{c}" for c in _cols)
    _old = ", ".join(f"old.{c}" for c in _cols)
    SQLITE_FTS += [
        f"CREATE TRIGGER IF NOT EXISTS {_t}_ai AFTER INSERT ON {_t} BEGIN "
        f"INSERT INTO {_t}_fts(rowid, {_c}) VALUES (new.rowid, {_new}); END",
        f"CREATE TRIGGER IF NOT EXISTS {_t}_ad AFTER DELETE ON {_t} BEGIN "
        f"INSERT INTO {_t}_fts({_t}_fts, rowid, {_c}) VALUES ('delete', old.rowid, {_old}); END",
        f"CREATE TRIGGER IF NOT EXISTS {_t}_au AFTER UPDATE ON {_t} BEGIN "
        f"INSERT INTO {_t}_fts({_t}_fts, rowid, {_c}) VALUES ('delete', old.rowid, {_old}); "
        f"INSERT INTO {_t}_fts(rowid, {_c}) VALUES (new.rowid, {_new}); END",
    ]

_WORD = re.compile(r"[0-9a-zÀ-ɏऀ-ॿ]+", re.I)


def fts_terms(phrase: str) -> list:
    """The words of a typed keyword, lowercased; punctuation (and anything FTS would read as syntax) is dropped."""
    return [w.lower() for w in _WORD.findall(str(phrase or ""))][:8]


class SqlRepo:
    """Shared SQL work. Subclasses give the connection and the dialect bits (placeholder, DDL types, text match)."""
    ph = "?"
    types = {}
    name = "sql"

    def __init__(self):
        self._lock = threading.RLock()
        self._depth = 0
        self._ready = False

    # ---- connection hooks
    def _conn(self):
        raise NotImplementedError

    def _cursor(self):
        return self._conn().cursor()

    def _sql(self, sql: str) -> str:
        return sql if self.ph == "?" else sql.replace("?", self.ph)

    # ---- schema (create-if-not-exists only; lazy)
    def ensure(self) -> None:
        if self._ready:
            return
        with self._lock:
            if self._ready:
                return
            cur = self._cursor()
            for stmt in DDL:
                cur.execute(stmt.format(**self.types))
            self._indexes(cur)
            self._extra_schema(cur)
            cur.execute(self._sql("SELECT v FROM pdb_meta WHERE k = ?"), ("schema",))
            if not cur.fetchone():
                cur.execute(self._sql("INSERT INTO pdb_meta (k, v) VALUES (?, ?)"), ("schema", SCHEMA_VERSION))
            self._commit()
            self._ready = True

    def _indexes(self, cur) -> None:
        for table, name, cols in INDEXES:
            cur.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({cols})")

    def _extra_schema(self, cur) -> None:
        return

    def _commit(self) -> None:
        self._conn().commit()

    def _rollback(self) -> None:
        try:
            self._conn().rollback()
        except Exception:                     # noqa: BLE001 - the connection may already be gone
            pass

    # ---- transactions: nested calls join the outer one
    @contextmanager
    def tx(self):
        self.ensure()
        with self._lock:
            self._depth += 1
            try:
                yield self
                if self._depth == 1:
                    self._commit()
            except BaseException:
                if self._depth == 1:
                    self._rollback()
                raise
            finally:
                self._depth -= 1

    # ---- queries
    def q(self, sql: str, params=()) -> list:
        self.ensure()
        with self._lock:
            cur = self._cursor()
            cur.execute(self._sql(sql), tuple(params))
            names = [d[0] for d in cur.description or []]
            rows = [dict(zip(names, r)) for r in cur.fetchall()]
            if self._depth == 0:
                self._commit()                  # end the read snapshot (MySQL REPEATABLE READ, SQLite WAL)
            return rows

    def q1(self, sql: str, params=()):
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql: str, params=()):
        row = self.q1(sql, params)
        return next(iter(row.values())) if row else None

    def x(self, sql: str, params=()) -> int:
        with self.tx():
            cur = self._cursor()
            cur.execute(self._sql(sql), tuple(params))
            return cur.rowcount

    def many(self, sql: str, rows: list) -> None:
        if not rows:
            return
        with self.tx():
            self._cursor().executemany(self._sql(sql), [tuple(r) for r in rows])

    def save(self, table: str, row: dict, new: bool, pk: str = "id") -> None:
        cols = [c for c in row if c != pk]
        with self.tx():
            if new:
                names = [pk, *cols]
                self.x(f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
                       [row[pk], *[row[c] for c in cols]])
            else:
                self.x(f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in cols)} WHERE {pk} = ?", [*[row[c] for c in cols], row[pk]])

    def insert_ignore(self, table: str, row: dict) -> None:
        raise NotImplementedError

    def bulk_insert(self, table: str, rows: list) -> None:
        if not rows:
            return
        cols = list(rows[0])
        self.many(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [[r[c] for c in cols] for r in rows])

    # ---- full-text match (dialect)
    def text_match(self, kind: str, alias: str, column: str, phrases: list, all_words: bool = False):
        """(sql, params) true when the row's `column` matches any of the phrases (all_words: every word of one phrase)."""
        raise NotImplementedError

    def size_bytes(self) -> int:
        return 0

    def close(self) -> None:
        return


class SqliteRepo(SqlRepo):
    name = "sqlite"
    types = {"K": "TEXT", "KK": "TEXT", "T": "TEXT", "L": "TEXT", "I": "INTEGER", "R": "REAL", "X": "",
             "AUTO": "INTEGER PRIMARY KEY AUTOINCREMENT"}

    def __init__(self, path):
        super().__init__()
        self.path = Path(path)
        self._db = None

    def _conn(self):
        if self._db is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30, isolation_level="DEFERRED")
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA cache_size=-32000")
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
            self._db = db
        return self._db

    def _extra_schema(self, cur) -> None:
        for stmt in SQLITE_FTS:
            cur.execute(stmt)

    def insert_ignore(self, table: str, row: dict) -> None:
        cols = list(row)
        self.x(f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])

    def text_match(self, kind, alias, column, phrases, all_words=False):
        parts = []
        for phrase in phrases:
            words = fts_terms(phrase)
            if not words:
                continue
            if all_words:
                parts += [f'"{w}"*' for w in words]
            else:
                parts.append('"' + " ".join(words) + '"*')
        if not parts:
            return "", []
        expr = (" AND " if all_words else " OR ").join(parts)
        table = f"pdb_{kind}_fts"
        return f"{alias}.rowid IN (SELECT rowid FROM {table} WHERE {table} MATCH ?)", [f"{column} : ({expr})"]

    def size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += os.path.getsize(str(self.path) + suffix)
            except OSError:
                pass
        return total

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                self._db.close()
                self._db = None


class MySqlRepo(SqlRepo):
    """The same tables in MySQL 8 (InnoDB, utf8mb4) with FULLTEXT indexes; `connect` is injectable for tests."""
    name = "mysql"
    ph = "%s"
    types = {"K": "VARCHAR(255)", "KK": "VARCHAR(700)", "T": "TEXT", "L": "MEDIUMTEXT", "I": "BIGINT", "R": "DOUBLE",
             "X": " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", "AUTO": "BIGINT PRIMARY KEY AUTO_INCREMENT"}

    def __init__(self, url: str, connect=None):
        super().__init__()
        from .store_mysql import parse_url
        self._args = parse_url(url)
        self._connect = connect
        self._db = None

    def _conn(self):
        if self._db is None:
            if self._connect:
                self._db = self._connect(**self._args)
            else:
                import pymysql
                self._db = pymysql.connect(**self._args, charset="utf8mb4", autocommit=False, connect_timeout=10,
                                           read_timeout=60, write_timeout=60)
        elif self._depth == 0:
            self._db.ping(reconnect=True)
        return self._db

    def _indexes(self, cur) -> None:
        """MySQL has no CREATE INDEX IF NOT EXISTS: read what exists and add only what is missing (new tables only)."""
        cur.execute("SELECT DISTINCT table_name, index_name FROM information_schema.statistics WHERE table_schema = DATABASE()")
        have = {(t.lower(), i.lower()) for t, i in cur.fetchall()}
        for table, name, cols in INDEXES:
            if (table, name) not in have:
                cur.execute(f"CREATE INDEX {name} ON {table} ({cols})")
        for table, name, col in (("pdb_people", "ft_title", "title"), ("pdb_people", "ft_all", "search_all"),
                                 ("pdb_companies", "ft_name", "name"), ("pdb_companies", "ft_all", "search_all")):
            if (table, name) not in have:
                cur.execute(f"CREATE FULLTEXT INDEX {name} ON {table} ({col})")

    def insert_ignore(self, table: str, row: dict) -> None:
        cols = list(row)
        self.x(f"INSERT IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", [row[c] for c in cols])

    MIN_TOKEN = 3                              # InnoDB FULLTEXT ignores shorter words ("hr"): those use LIKE instead

    def text_match(self, kind, alias, column, phrases, all_words=False):
        parts, likes = [], []
        for phrase in phrases:
            words = fts_terms(phrase)
            if not words:
                continue
            if any(len(w) < self.MIN_TOKEN for w in words):
                likes += [f"%{w}%" for w in words] if all_words else ["%" + " ".join(words) + "%"]
            elif all_words:
                parts += [f"+{w}*" for w in words]
            else:
                parts.append('"' + " ".join(words) + '"' if len(words) > 1 else f"{words[0]}*")
        sqls, params = [], []
        if parts:
            sqls.append(f"MATCH({alias}.{column}) AGAINST (? IN BOOLEAN MODE)")
            params.append(" ".join(parts))
        for like in likes:
            sqls.append(f"LOWER({alias}.{column}) LIKE ?")
            params.append(like)
        if not sqls:
            return "", []
        return "(" + (" AND " if all_words else " OR ").join(sqls) + ")", params

    def close(self) -> None:
        with self._lock:
            if self._db is not None:
                try:
                    self._db.close()
                finally:
                    self._db = None


_REPOS = {}
_REPOS_LOCK = threading.Lock()


def sqlite_path_for(store) -> Path:
    data = Path(getattr(store, "path", None) or "data/store.json")
    return Path(os.environ.get("OUTREACH_PROSPECT_DB") or data.with_name(data.name + ".prospects.sqlite"))


def repo_for(store) -> SqlRepo:
    """The repository for this store: MySQL when the store itself is on MySQL, else the SQLite file next to the data file."""
    repo = getattr(store, "_prospect_repo", None)
    if repo is not None:
        return repo
    with _REPOS_LOCK:
        repo = getattr(store, "_prospect_repo", None)
        if repo is None:
            url = os.environ.get("OUTREACH_DATABASE_URL", "").strip()
            from .store_mysql import MySqlStore
            if url and isinstance(store, MySqlStore):
                repo = MySqlRepo(url)
            else:
                key = str(sqlite_path_for(store).resolve())
                repo = _REPOS.get(key)
                if repo is None:
                    repo = _REPOS[key] = SqliteRepo(key)
            try:
                store._prospect_repo = repo
            except AttributeError:
                pass
    return repo


def forget(store) -> None:
    """Close and drop the cached repository (used by tests and restores)."""
    repo = getattr(store, "_prospect_repo", None)
    if repo is not None:
        repo.close()
        if isinstance(repo, SqliteRepo):
            _REPOS.pop(str(repo.path), None)
        try:
            del store._prospect_repo
        except AttributeError:
            pass
