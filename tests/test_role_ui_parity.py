"""Viewer and caller refusals: the server refuses every write route for viewers, and the web pages that call a refused
route hide the control behind the same role check (canWrite / isOwner), so the UI matches the server."""
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.routes_auth import ANYONE, needed_role

ADMIN = "admin-pass-123"
WEB = Path(__file__).resolve().parent.parent / "web" / "src"
API_FILES = ("api.js", "opsApi.js", "prospectsApi.js")
OPEN_WRITES = {("POST", "/api/login"), ("POST", "/api/linkedin-runner/pair"),   # no user yet: sign in, swap a one-time runner code
               ("POST", "/api/password/forgot"), ("POST", "/api/password/reset"), ("POST", "/api/password/reset/info"),   # forgot password: no login by design
               ("POST", "/api/login/code"), ("POST", "/api/login/setup/start"), ("POST", "/api/login/setup/confirm")}   # two-step sign-in: the ticket is the key
CALL = re.compile(r"""(?:request|apiRequest|upload)\(\s*([`"'])(/api[^`"']*)\1(.{0,200})""")


def _routes(app):
    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                yield from walk(r.original_router.routes)
            elif getattr(r, "methods", None):
                yield r
    return [(m, r.path) for r in walk(app.routes) for m in sorted(r.methods) if m not in ("GET", "HEAD", "OPTIONS")]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _fill(path):
    return re.sub(r"\{[^}]+\}", "x1", path)


def test_viewer_is_refused_every_write_route(tmp_path):
    app = create_app(data_file=tmp_path / "d.json", admin_password=ADMIN)
    c = TestClient(app, client=("203.0.113.9", 50000))
    owner = c.post("/api/login", json={"password": ADMIN}).json()["token"]
    assert c.post("/api/users", json={"email": "v@example.com", "role": "viewer", "password": "long-password-1"},
                  headers=_bearer(owner)).status_code == 200
    viewer = c.post("/api/login", json={"email": "v@example.com", "password": "long-password-1"}).json()["token"]
    writes = [(m, p) for m, p in _routes(app) if p.startswith("/api/") and (m, p) not in ANYONE | OPEN_WRITES]
    assert len(writes) > 150
    let_through = []
    for method, path in writes:
        if needed_role(method, path) == "viewer":
            let_through.append((method, path, "needed_role"))
            continue
        r = c.request(method, _fill(path), headers=_bearer(viewer), json={})
        if r.status_code != 403:
            let_through.append((method, path, r.status_code))
    assert let_through == []


def _write_calls():
    """{function name: needed role} for every non-GET call the web app makes through its API helpers."""
    out = {}
    for name in API_FILES:
        for line in (WEB / name).read_text(encoding="utf-8").splitlines():
            for m in CALL.finditer(line):
                method = re.search(r'method:\s*"([A-Z]+)"', m.group(3))
                method = method.group(1) if method else ("POST" if m.group(0).startswith("upload") else "GET")
                fn = re.match(r"\s*(?:async\s+)?([A-Za-z_]\w*)\s*[:(]", line)
                if method == "GET" or not fn or fn.group(1) in ("request", "login", "logout", "changeOwnPassword"):
                    continue
                path = re.sub(r"\$\{[^}]*\}", "{x}", m.group(2).split("?")[0])
                out[fn.group(1)] = path, method
    return out


def test_every_page_calling_a_refused_write_checks_the_role():
    calls = _write_calls()
    assert len(calls) > 120
    names = "|".join(sorted(calls, key=len, reverse=True))
    use = re.compile(r"\b(?:api|ops|prospects)\.(" + names + r")\(")
    unguarded = []
    for page in sorted((WEB / "pages").rglob("*.jsx")) + [WEB / "App.jsx"]:
        text = page.read_text(encoding="utf-8")
        used = set(use.findall(text))
        if used and not re.search(r"\b(canWrite|isOwner|getRole)\(\)|\breadOnly\b|\brole\b", text):
            unguarded.append((page.name, sorted(used)))
    assert unguarded == [], "pages calling write routes without any role check"


def test_can_write_matches_the_server_roles():
    text = (WEB / "api.js").read_text(encoding="utf-8")
    assert 'export const canWrite = () => role === "owner" || role === "caller" || role === "admin";' in text
    from app.routes_auth import allowed
    assert allowed("admin", "caller") and not allowed("admin", "owner")
    assert needed_role("POST", "/api/stories") == "caller" and needed_role("PUT", "/api/settings") == "owner"
