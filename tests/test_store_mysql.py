"""MySQL store. Needs a throwaway database: OUTREACH_TEST_MYSQL_URL=mysql://root:pw@127.0.0.1:33069/outreach (skipped otherwise)."""
import json
import os

import pytest

from app.store import Store
from app.store_mysql import MySqlStore, parse_url

URL = os.environ.get("OUTREACH_TEST_MYSQL_URL", "")
needs_db = pytest.mark.skipif(not URL, reason="OUTREACH_TEST_MYSQL_URL not set")


def test_parse_url():
    assert parse_url("mysql://u:p%40ss@10.0.0.5:3307/db") == {"host": "10.0.0.5", "port": 3307, "user": "u", "password": "p@ss", "database": "db"}
    with pytest.raises(ValueError):
        parse_url("postgres://u@h/db")


@pytest.fixture
def clean():
    import pymysql
    conn = pymysql.connect(**parse_url(URL))
    conn.cursor().execute("DROP TABLE IF EXISTS collections")
    conn.commit()
    conn.close()


@needs_db
def test_round_trip_and_partial_writes(tmp_path, clean):
    store = MySqlStore(tmp_path / "store.json", URL)
    store.add_leads([{"id": "l1", "dedupe_key": "a@x.in", "company": "Ünïcode Co", "timeline": []}])
    store.write("settings", {"a": 1})
    again = MySqlStore(tmp_path / "store.json", URL)                       # a second process sees it
    assert again.read("leads")[0]["company"] == "Ünïcode Co" and again.read("settings") == {"a": 1}
    store.update("tasks", lambda t: [*t, {"id": "t1"}])
    assert MySqlStore(tmp_path / "store.json", URL).read("tasks") == [{"id": "t1"}]
    assert store.update_lead("l1", lambda l: {**l, "company": "Changed"})["company"] == "Changed"
    assert MySqlStore(tmp_path / "store.json", URL).read("leads")[0]["company"] == "Changed"


@needs_db
def test_json_file_is_imported_once_and_left_alone(tmp_path, clean):
    old = Store(tmp_path / "store.json")
    old.add_leads([{"id": "l9", "dedupe_key": "z@y.in", "company": "Old", "timeline": []}])
    old.write("settings", {"server_secret": "s3"})
    before = (tmp_path / "store.json").read_text()
    store = MySqlStore(tmp_path / "store.json", URL)
    assert store.read("leads")[0]["id"] == "l9" and store.read("settings")["server_secret"] == "s3"
    assert (tmp_path / "store.json").read_text() == before
    store.write("settings", {"server_secret": "changed"})
    assert MySqlStore(tmp_path / "store.json", URL).read("settings")["server_secret"] == "changed"   # DB wins after the import


@needs_db
def test_replace_all_snapshot_and_erased_guard(tmp_path, clean):
    store = MySqlStore(tmp_path / "store.json", URL)
    store.write("settings", {"x": 1})
    store.replace_all(lambda cur: {**cur, "settings": {"x": 2}}, keep_copy_as=tmp_path / "copy.json")
    assert json.loads((tmp_path / "copy.json").read_text())["settings"] == {"x": 1}
    assert MySqlStore(tmp_path / "store.json", URL).read("settings") == {"x": 2}
    assert store.snapshot()["settings"] == {"x": 2}
