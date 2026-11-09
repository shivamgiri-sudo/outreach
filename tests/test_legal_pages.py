"""Public legal pages (Terms, Privacy, DPA): routes, versions, required sections, the DRAFT banner, no secrets.
The pages are static React content (web/src/legal/*.js) shown by the SPA before sign-in; the web server falls back to the
app shell for /terms, /privacy and /dpa. The content modules are loaded with node (skipped if node is missing)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import signup
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
LEGAL = ROOT / "web" / "src" / "legal"
NODE = shutil.which("node")


def docs():
    code = ("import('" + (LEGAL / "index.js").as_uri() + "').then(m => console.log(JSON.stringify("
            "Object.fromEntries(Object.entries(m.DOCS).map(([k, d]) => [k, {doc: d, ph: m.placeholdersOf(d)}])))))")
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True, cwd=str(ROOT)).stdout
    return json.loads(out)


def flat(doc):
    out = [doc["lede"]]
    for s in doc["sections"]:
        out.append(s["title"])
        for b in s["blocks"]:
            if isinstance(b, str):
                out.append(b)
            elif isinstance(b, list):
                out.extend(b)
            else:
                out.extend(c for r in b["rows"] for c in r)
    return "\n".join(out)


def test_versions_are_served_by_the_public_config(tmp_path):
    c = TestClient(create_app(data_file=tmp_path / "s.json", admin_password="admin-pass-123"))
    cfg = c.get("/api/signup/config")                         # no sign-in needed
    assert cfg.status_code == 200
    assert cfg.json()["terms"] == {"terms": signup.TERMS_VERSION, "privacy": signup.PRIVACY_VERSION, "dpa": signup.DPA_VERSION}
    assert cfg.json()["signup"] is False                      # sign-up stays closed by default


def test_web_server_falls_back_to_the_app_shell_for_the_legal_paths():
    caddy = (ROOT / "deploy" / "Caddyfile.template").read_text()
    api = re.search(r"@api path ([^\n]+)", caddy).group(1).split()
    for p in ("/terms", "/privacy", "/dpa"):                  # not claimed by the API matcher...
        assert not any(p.startswith(a.rstrip("*")) for a in api)
    assert "try_files {path} /index.html" in caddy            # ...so they fall to the SPA index
    proxy = (ROOT / "web" / "e2e" / "static_proxy.mjs").read_text()
    assert 'path.join(dist, "index.html")' in proxy           # the local test proxy does the same
    csp = re.search(r'header Content-Security-Policy "([^"]+)"', caddy).group(1)
    assert "script-src 'self'" in csp and "unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_sign_up_links_point_at_the_pages():
    src = (ROOT / "web" / "src" / "pages" / "Signup.jsx").read_text()
    for p in ("/terms", "/privacy", "/dpa"):
        assert f'href="{p}" target="_blank"' in src
    app = (ROOT / "web" / "src" / "App.jsx").read_text()
    assert "legalFromLocation" in app and app.index("legalFromLocation") < app.index("<Shell />")


def test_pages_use_no_api_calls_but_the_config_and_no_secrets():
    code = (ROOT / "web" / "src" / "pages" / "Legal.jsx").read_text()
    assert re.findall(r'fetch\("([^"]+)"', code) == ["/api/signup/config"]
    for f in list(LEGAL.glob("*.js")) + [ROOT / "web" / "src" / "pages" / "Legal.jsx"]:
        text = f.read_text()
        assert not re.search(r"sk-[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{12,}|password\s*[:=]\s*[\"'][^\"']{6,}|BEGIN [A-Z ]*PRIVATE KEY|Bearer [A-Za-z0-9]{12,}", text), f.name
        assert len(text.splitlines()) < 500


@pytest.mark.skipif(not NODE, reason="node is needed to load the content modules")
class TestContent:
    def test_each_document_has_the_required_sections(self):
        d = docs()
        need = {
            "terms": ["acceptable use", "limits", "fees", "export", "availability", "intellectual property", "confidentiality",
                      "limitation of liability", "indemnity", "termination", "governing law", "contact", "DND", "LinkedIn", "hash chain"],
            "privacy": ["Digital Personal Data Protection Act", "data processor", "data fiduciary", "sub-processors", "Oracle Cloud",
                        "Resend", "Hostinger", "Apollo", "Hunter", "Firecrawl", "Anthropic", "Google", "Meta", "\\[TBD", "5000", "session storage",
                        "Grievance Officer", "children", "breach", "international transfers", "retention"],
            "dpa": ["data fiduciary", "processor", "Annex 1", "Annex 2", "sub-processor", "objection|object", "48", "audit pack",
                    "change log", "deletion", "instructions", "DPIA|impact assessment"],
        }
        for key, words in need.items():
            text = flat(d[key]["doc"])
            for w in words:
                assert re.search(w, text, re.I), f"{key}: missing {w}"
            assert 1000 <= len(text.split()) <= 2600, (key, len(text.split()))

    def test_every_placeholder_is_listed_for_the_banner_and_the_expected_ones_exist(self):
        d = docs()
        all_ph = set()
        for key, v in d.items():
            body = set(re.findall(r"\[[^\]]+\]", flat(v["doc"])))
            assert body == set(v["ph"]) and v["ph"], key
            all_ph |= body
        for want in ("[REGISTERED ADDRESS]", "[CIN]", "[SUPPORT EMAIL]", "[GRIEVANCE OFFICER NAME]", "[GRIEVANCE OFFICER EMAIL]"):
            assert want in all_ph

    def test_banner_and_version_label_are_rendered_by_the_component(self):
        code = (ROOT / "web" / "src" / "pages" / "Legal.jsx").read_text()
        assert "DRAFT - for legal review" in code and "legal-draft" in code and "Version {version" in code and "effective [EFFECTIVE DATE" in code
        assert 'aria-label="Contents"' in code and "legal-skip" in code
