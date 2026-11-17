"""The local company register: CSV parsing (app/company_register_csv.py), storage and upsert (app/company_register.py),
streaming import (app/company_register_jobs.py). No network."""
import io
import os
import random
import time
import tracemalloc

import pytest

from app import company_register as R
from app import company_register_csv as C
from app import company_register_jobs as J
from app.store import Store

MYSQL_URL = os.environ.get("OUTREACH_TEST_MYSQL_URL", "")
HEADER = ("CORPORATE_IDENTIFICATION_NUMBER,COMPANY_NAME,COMPANY_STATUS,COMPANY_CLASS,COMPANY_CATEGORY,COMPANY_SUB_CATEGORY,"
          "DATE_OF_REGISTRATION,REGISTERED_STATE,AUTHORIZED_CAP,PAIDUP_CAPITAL,INDUSTRIAL_CLASS,"
          "PRINCIPAL_BUSINESS_ACTIVITY_AS_PER_CIN,REGISTERED_OFFICE_ADDRESS,REGISTRAR_OF_COMPANIES,EMAIL_ADDR,LATEST_YEAR_ANNUAL_RETURN")


@pytest.fixture(params=["sqlite", "mysql"])
def store(tmp_path, request):
    s = Store(tmp_path / "d.json")
    if request.param == "mysql":
        if not MYSQL_URL:
            pytest.skip("set OUTREACH_TEST_MYSQL_URL to run the MySQL backend")
        repo = R.RegisterMySql(MYSQL_URL)
        cur = repo._conn().cursor()
        for t in ("mca_companies", "mca_meta"):
            cur.execute(f"DROP TABLE IF EXISTS {t}")
        repo._conn().commit()
        s._register_repo = repo
    yield s
    R.forget(s)


def read(text: str, stats=None):
    stats = {} if stats is None else stats
    return list(C.read_csv(io.BytesIO(text.encode("utf-8")), stats)), stats


def test_parses_the_rocwise_header_with_bom_quotes_and_capital():
    text = ("﻿" + HEADER + "\n"
            'U72200MH2010PTC123456,"ACME TECHNOLOGIES PRIVATE LIMITED",Active,Private,Company limited by Shares,'
            'Non-govt company,05-01-2010,Maharashtra,"10,00,000",500000.00,72200,Business Services,'
            '"12, MG Road, Andheri (E)\nMumbai 400069",RoC-Mumbai,info@acme.example,31-03-2024\n')
    rows, stats = read(text)
    assert len(rows) == 1 and stats["rows"] == 1 and stats["skipped"] == 0
    r = rows[0]
    assert r["cin"] == "U72200MH2010PTC123456" and r["name"] == "ACME TECHNOLOGIES PRIVATE LIMITED"
    assert r["name_key"] == "acme technologies" and r["tok_key"] == "acme"
    assert r["incorporated"] == "2010-01-05" and r["last_return"] == "2024-03-31"
    assert r["authorised"] == 1000000 and r["paid_up"] == 500000
    assert r["state"] == "Maharashtra" and r["roc"] == "RoC-Mumbai" and r["cls"] == "Private"
    assert r["address"] == "12, MG Road, Andheri (E) Mumbai 400069"         # newline inside quotes, spaces collapsed
    assert r["email"] == "info@acme.example" and r["activity"] == "Business Services" and r["nic"] == "72200"


def test_api_style_lowercase_keys_and_other_spellings():
    rec = {"cin": "u74999dl2015ptc111111", "company_name": "Zeta Foods (India) Pvt. Ltd.", "status": "ACTIVE",
           "date_of_incorporation": "2015-07-09T00:00:00", "company_state_code": "dl", "authorised_capital": "Rs. 1,00,000",
           "paid_up_capital": "NA", "registered_address": "Delhi", "roc_code": "RoC-Delhi", "some_new_column": "x"}
    r = C.map_record(rec)
    assert r["cin"] == "U74999DL2015PTC111111" and r["name_key"] == "zeta foods"
    assert r["incorporated"] == "2015-07-09" and r["state"] == "Delhi" and r["authorised"] == 100000 and r["paid_up"] is None
    assert r["email"] is None                                       # no email column: never invented


@pytest.mark.parametrize("raw, want", [
    ("05-01-2010", "2010-01-05"), ("05/01/2010", "2010-01-05"), ("2010-01-05", "2010-01-05"), ("05-Jan-2010", "2010-01-05"),
    ("05-JAN-10", "2010-01-05"), ("5 January 2010", "2010-01-05"), ("05.01.2010", "2010-01-05"), ("2010/01/05", "2010-01-05"),
    ("2010-01-05 00:00:00", "2010-01-05"), ("40183", "2010-01-05"), ("NA", ""), ("31-31-2010", ""), ("", ""),
])
def test_dates_in_several_formats(raw, want):
    assert C.parse_date(raw) == want


def test_state_from_cin_when_the_column_is_missing_and_bad_rows_are_skipped():
    text = ("Company Name;CIN;Email\n"
            "Brightpath Advisors LLP;U74140KA2019PTC222222;not-an-email\n"
            ";U74140KA2019PTC333333;\n"
            "No Cin Ltd;;\n"
            "Bad Id Ltd;??;\n")
    rows, stats = read(text)
    assert [r["name"] for r in rows] == ["Brightpath Advisors LLP"] and rows[0]["state"] == "Karnataka"
    assert rows[0]["email"] is None and stats["rows"] == 4 and stats["skipped"] == 3
    assert stats["unknown"] == ["Email"] or "email" in stats["columns"]


def test_a_file_without_cin_and_name_is_refused():
    with pytest.raises(ValueError, match="CIN and company name"):
        read("Name of person,Phone\nRavi,98765\n")


def test_upsert_by_cin_keeps_older_values_a_newer_file_lacks(store):
    repo = R.repo_for(store)
    first, _ = read(HEADER + "\nU72200MH2010PTC123456,ACME TECHNOLOGIES PRIVATE LIMITED,Active,Private,,,05-01-2010,Maharashtra,"
                    "100000,50000,,,Mumbai,RoC-Mumbai,info@acme.example,\n")
    assert repo.upsert(first) == 1
    second, _ = read("CIN,COMPANY_NAME,COMPANY_STATUS\nU72200MH2010PTC123456,ACME TECHNOLOGIES PRIVATE LIMITED,Strike Off\n")
    repo.upsert(second + second)                                    # the same CIN twice in one batch is fine
    got = repo.get("u72200mh2010ptc123456")
    assert repo.count() == 1 and got["status"] == "Strike Off"
    assert got["email"] == "info@acme.example" and got["paid_up"] == 50000 and got["incorporated"] == "2010-01-05"
    assert [r["cin"] for r in repo.by_name("acme technologies")] == ["U72200MH2010PTC123456"]
    assert [r["cin"] for r in repo.by_prefix("acme")] == ["U72200MH2010PTC123456"]
    assert repo.by_prefix("acme_") == [] and repo.by_prefix("") == []
    assert [r["cin"] for r in repo.by_tokens(["acme", "zz"])] == ["U72200MH2010PTC123456"]


def test_storage_is_its_own_sqlite_file_with_meta(tmp_path):
    s = Store(tmp_path / "d.json")
    repo = R.repo_for(s)
    repo.upsert([C.map_record({"CIN": "U1234567890XX", "COMPANY_NAME": "Acme Ltd", "REGISTERED_STATE": "Goa"})])
    stats = R.refresh_stats(repo, "2026-11-16T00:00:00+00:00")
    R.note_import(repo, {"at": "x", "name": "goa.csv"})
    assert (tmp_path / "d.json.register.sqlite").exists() and "mca_companies" not in s.snapshot()
    summary = R.summary(s)
    assert summary["total"] == 1 and summary["per_state"] == [{"state": "Goa", "count": 1}] and stats["size_bytes"] > 0
    assert summary["last_import"]["name"] == "goa.csv" and "GODL" in summary["licence"] and "Directors" in summary["not_included"]
    R.forget(s)


STATES = ["Maharashtra", "Karnataka", "Delhi", "Tamil Nadu", "Gujarat", "Uttar Pradesh", "West Bengal", "Telangana"]
WORDS = "sunrise apex blue lotus vertex nova prime orbit zenith quantum metro silver green royal crystal".split()


def write_big(path, n: int) -> None:
    rnd = random.Random(7)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER + "\n")
        for i in range(n):
            name = f"{rnd.choice(WORDS).upper()} {rnd.choice(WORDS).upper()} {i} PRIVATE LIMITED"
            state = STATES[i % len(STATES)]
            f.write(f'U72200MH20{i % 20:02d}PTC{i:06d},{name},Active,Private,Company limited by Shares,Non-govt company,'
                    f'{1 + i % 28:02d}-0{1 + i % 9}-20{i % 20:02d},{state},1000000,{100000 + i},72200,Business Services,'
                    f'"{i} Some Road, {state}",RoC-{state},,31-03-2024\n')


def test_streaming_50k_rows_is_batched_memory_safe_and_quick(tmp_path, monkeypatch):
    s = Store(tmp_path / "d.json")
    path = tmp_path / "big.csv"
    write_big(path, 50_000)
    repo = R.repo_for(s)
    calls = []
    real = repo.upsert
    monkeypatch.setattr(repo, "upsert", lambda rows: calls.append(len(rows)) or real(rows))
    state = {}
    tracemalloc.start()
    started = time.monotonic()
    got = J.import_file(repo, path, state)
    took = time.monotonic() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert got["written"] == 50_000 and repo.count() == 50_000 and got["skipped"] == 0
    assert max(calls) == R.BATCH and len(calls) == 26                 # 25 full batches + the empty tail call
    assert state["bytes_done"] == state["bytes_total"] == path.stat().st_size
    assert peak < 40 * 1024 * 1024, f"peak {peak} bytes: the file must not be held in memory"
    assert took < 90, f"took {took:.1f}s"
    size = R.refresh_stats(repo, "now")["size_bytes"]
    print(f"\n50k rows: {took:.1f}s, peak {peak / 1e6:.1f} MB, on disk {size / 1e6:.1f} MB -> 2M rows ~ {size * 40 / 1e9:.2f} GB")
    assert size < 25 * 1024 * 1024                                    # compact: about 2x the CSV at most
    assert R.summary(s)["per_state"][0]["count"] == 6250
    R.forget(s)


def test_import_queue_runs_files_in_turn_and_deletes_them(tmp_path):
    s = Store(tmp_path / "d.json")
    jobs = J.Jobs(s)
    folder = J.upload_dir(s)
    folder.mkdir()
    good, bad = folder / "a.csv", folder / "b.csv"
    write_big(good, 10)
    bad.write_text("Name,Phone\nRavi,1\n")
    jobs.enqueue(good, "maharashtra.csv", "Owner", background=False)
    jobs.enqueue(bad, "wrong.csv", "Owner", background=False)
    view = jobs.view()
    assert view["status"] == "done" and view["files"][0]["status"] == "failed" and "CIN" in view["files"][0]["error"]
    assert not good.exists() and not bad.exists()
    summary = R.summary(s)
    assert summary["total"] == 10 and summary["imports"][0]["name"] == "maharashtra.csv" and summary["imports"][0]["added"] == 10
    R.forget(s)


def test_mysql_sql_shape_without_a_server():
    """Fake MySQL connection: the upsert keeps old values (COALESCE(VALUES())), prefix search escapes LIKE wildcards,
    missing indexes are built online and an index failure never blocks."""
    sql = []

    class Cur:
        def execute(self, stmt, params=()):
            sql.append(stmt)
            if "CREATE INDEX mca_tok" in stmt:
                raise RuntimeError("lock wait timeout")

        def fetchall(self):
            return [("MCA_COMPANIES", "MCA_NAME")]

    class Conn:
        def cursor(self):
            return Cur()

        def commit(self):
            pass

        def ping(self, reconnect=True):
            pass

    repo = R.RegisterMySql("mysql://u:p@127.0.0.1:3306/x", connect=lambda **kw: Conn())
    repo.ensure()
    assert repo._ready and any("CREATE TABLE IF NOT EXISTS mca_companies" in s and "ENGINE=InnoDB" in s for s in sql)
    made = [s for s in sql if s.startswith("CREATE INDEX")]
    assert len(made) == 1 and all(s.endswith("ALGORITHM=INPLACE LOCK=NONE") for s in made)
    assert not any("ALTER" in s for s in sql)
    up = repo.upsert_sql()
    assert "ON DUPLICATE KEY UPDATE" in up and "status = COALESCE(VALUES(status), status)" in up and "cin = " not in up
    where, params = repo.prefix_sql("a_b%")
    assert where == "name_key LIKE ?" and params == ["a\\_b\\%%"]
