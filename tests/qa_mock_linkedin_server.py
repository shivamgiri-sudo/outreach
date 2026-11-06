"""The LinkedIn stand-in (tests/mock_linkedin.py) as a process on a fixed port, for web/e2e/office_runner_qa.mjs.

    python tests/qa_mock_linkedin_server.py 8076

Several accounts (LOGIN["accounts"]) and a few control addresses that only the test uses (they stand in for what happens on
LinkedIn's side, e.g. the owner tapping Yes in the LinkedIn app): GET /_qa/approve, /_qa/events, /_qa/install?slug=&name=&kind=,
/_qa/requests (every path asked for, to prove the agents only talk to this stand-in). Never touches linkedin.com."""
import json
import sys
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import mock_linkedin as m  # noqa: E402

REQUESTS = []
ACCOUNTS = {
    "asha@example.com": {"password": "Asha-Pass-1", "slug": "asha-sender", "name": "Asha Sender", "mode": "code"},
    "bala@example.com": {"password": "Bala-Pass-2", "slug": "bala-sender", "name": "Bala Sender", "mode": "app"},
    "chitra@example.com": {"password": "Chitra-Pass-3", "slug": "chitra-sender", "name": "Chitra Sender", "mode": "captcha"},
}


class Handler(m.Handler):
    def _json(self, data):
        body = json.dumps(data).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path.startswith("/_qa/"):
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/_qa/approve":
                m.LOGIN["approved"] = True
            elif url.path == "/_qa/install":
                m.install(q["slug"], q["name"], "2nd", q.get("kind", "connect"))
            elif url.path == "/_qa/events":
                return self._json(m.EVENTS)
            elif url.path == "/_qa/requests":
                return self._json(REQUESTS)
            elif url.path == "/_qa/login":
                return self._json({k: v for k, v in m.LOGIN.items() if k != "accounts"})
            return self._json({"ok": True})
        REQUESTS.append({"method": "GET", "path": url.path, "host": self.headers.get("Host", "")})
        return super().do_GET()

    def do_POST(self):
        REQUESTS.append({"method": "POST", "path": urlparse(self.path).path, "host": self.headers.get("Host", "")})
        return super().do_POST()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8076
    m.PAGES.clear(); m.EVENTS.clear(); m.LOGIN.clear(); m.LOGIN.update(json.loads(json.dumps(m.LOGIN_DEFAULTS)))
    m.LOGIN.update(required=True, accounts=ACCOUNTS)
    print(f"mock LinkedIn on 127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
