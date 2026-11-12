"""tools/linkedin_agent_inbox.mjs: a row without a profile link is opened only for a from-them row that names exactly one
waiting lead (max 3 per read); the link is the thread header's, and a group header gives nothing."""
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PW = ROOT / "web" / "node_modules" / "playwright" / "index.mjs"


@pytest.mark.skipif(not PW.exists(), reason="playwright not installed")
def test_thread_header_link_is_read_only_where_safe():
    p = subprocess.run(["node", str(ROOT / "tests/agent_sim/inbox_read_check.mjs"), str(ROOT / "tools/linkedin_agent_inbox.mjs")],
                       capture_output=True, text=True, timeout=90, env={**os.environ, "PLAYWRIGHT_ENTRY": PW.as_uri()})
    out = json.loads(p.stdout.strip().splitlines()[-1]), p.stderr[-500:]
    got = {n: u for n, u in out[0]["items"]}
    assert got["Ann Aa"].endswith("/in/ann-aa")                       # the row's own link is untouched
    assert got["Bob Bb"].endswith("/in/bob-bb-real")                  # one waiting lead of that name: header link read
    assert got["Cat Cc"] == "" and got["Dan Dd"] == ""                # not watched / from us: never opened
    assert got["Eve Ee"] == ""                                        # two leads of that name: left to the portal
    assert got["Fay Ff"] == ""                                        # the header shows a group: no link given
    assert got["Gus Gg"].endswith("/in/gus-gg-real") and got["Hal Hh"] == ""    # the third open is the last of this read
    assert len(out[0]["opened"]) <= 3 and "Cat Cc" not in out[0]["opened"] and "Dan Dd" not in out[0]["opened"], out
