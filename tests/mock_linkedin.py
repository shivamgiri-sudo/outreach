"""A tiny stand-in for LinkedIn profile pages (same button names the agent looks for) to test the agent's logic locally.
It proves the agent's steps and safety stops, NOT that LinkedIn's real pages still match: LinkedIn changes its pages."""
import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

PAGES = {}
EVENTS = []
# Cloud-login stand-in: when LOGIN["required"], every page needs the li_at cookie the login sets. LOGIN["mode"] picks what
# comes after a right password: "direct" (feed), "code" (verification-code page), "app" (approve in the LinkedIn app),
# "captcha" (a puzzle page). LOGIN["attempts"] records every form post (the email only, never the password).
LOGIN = {}
LOGIN_DEFAULTS = {"required": False, "mode": "direct", "email": "sender@example.com", "password": "Correct-Horse-9", "code": "246810",
                  "approved": False, "by_hand": False, "attempts": [], "codes": [], "accounts": {}}
# Several LinkedIn accounts (QA multi-account tests): LOGIN["accounts"] = {email: {"password", "slug", "name", "mode"}}. Each
# logs in to its own session cookie (li_at=ok.<slug>), /in/me answers with that account, and every event records "by".

LOGIN_PAGE = """<html><head><title>LinkedIn Login</title></head><body><main>
<h1>Sign in</h1>{error}
<form method="post" action="/login">
  <input type="email" id="hidden-{r0}" name="decoy" style="display:none">
  <input type="email" id="{r1}" name="session_key" autocomplete="username">
  <input type="password" id="{r2}" name="session_password" autocomplete="current-password">
  <button type="submit">Sign in</button>
</form></main>
<script>setInterval(async () => {{ const r = await fetch('/_by_hand'); if ((await r.text()) === 'yes') location.href = '/checkpoint/by-hand'; }}, 700);</script>
</body></html>"""
CODE_PAGE = """<html><head><title>Security Verification | LinkedIn</title></head><body><main>
<h1>Let's do a quick security check</h1><p>Enter the 6-digit code we sent to s***@example.com</p>{error}
<form method="post" action="/checkpoint/challenge/verify">
  <input id="input__email_verification_pin" name="pin" autocomplete="one-time-code" maxlength="8">
  <button type="submit">Submit</button>
</form></main></body></html>"""
APP_PAGE = """<html><head><title>Security Verification | LinkedIn</title></head><body><main>
<h1>Check your LinkedIn app</h1><p>We sent a notification to your signed in devices. Tap Yes to approve the sign in.</p>
<script>setInterval(async () => {{ const r = await fetch('/_approved'); if ((await r.text()) === 'yes') location.href = '/checkpoint/approved'; }}, 700);</script>
</main></body></html>"""
CAPTCHA_PAGE = """<html><head><title>Security Verification | LinkedIn</title></head><body><main>
<h1>Let's do a quick security check</h1><p>Solve this puzzle to continue.</p><iframe title="captcha" src="about:blank"></iframe>
</main>
<script>setInterval(async () => { const r = await fetch('/_by_hand'); if ((await r.text()) === 'yes') location.href = '/checkpoint/by-hand'; }, 700);</script>
</body></html>"""

CONNECT_PAGE = """<html><body><main><h1>{name}</h1><span>{degree}</span>
<div id="actions">{buttons}</div></main>
<div id="dlg" role="dialog" style="display:none">
  <button id="addnote">Add a note</button>
  <textarea id="note" style="display:none" name="message"></textarea>
  <button id="send" aria-label="Send invitation">Send</button>
</div>
<script>
const rec = (o) => fetch('/_event', {{method:'POST', body: JSON.stringify(o)}});
const connect = document.querySelector('[aria-label*="to connect"]');
if (connect) connect.onclick = () => {{ document.getElementById('dlg').style.display = 'block'; }};
document.getElementById('addnote').onclick = () => {{ document.getElementById('note').style.display = 'block'; }};
document.getElementById('send').onclick = () => {{
  rec({{kind:'invite', note: document.getElementById('note').value}});
  document.getElementById('dlg').style.display = 'none';
  document.getElementById('actions').innerHTML = '<button>Pending</button>';
  localStorage.setItem('pending:' + location.pathname, '1');      // like LinkedIn: the profile shows Pending after a reload too
}};
if (localStorage.getItem('pending:' + location.pathname)) document.getElementById('actions').innerHTML = '<button>Pending</button>';
const msg = document.querySelector('[aria-label^="Message"]');
if (msg) msg.onclick = () => {{
  const bub = document.createElement('div'); bub.className = 'msg-overlay-conversation-bubble';
  const hd = document.createElement('header'); hd.className = 'msg-overlay-bubble-header'; hd.textContent = '{name}';
  const box = document.createElement('div'); box.setAttribute('role','textbox'); box.setAttribute('contenteditable','true'); box.style.minHeight='40px'; box.style.border='1px solid #999';
  const send = document.createElement('button'); send.textContent = 'Send';
  send.onclick = () => {{ rec({{kind:'message', text: box.innerText}}); box.innerText = ''; }};
  bub.append(hd, box, send); document.body.appendChild(bub);
}};
</script></body></html>"""


def profile(name, degree, buttons):
    return CONNECT_PAGE.format(name=name, degree=degree, buttons=buttons)


def install(slug, name, degree, kind):
    buttons = {
        "connect": f'<button aria-label="Invite {name} to connect">Connect</button><button>Follow</button>',
        "message": f'<button aria-label="Message {name}">Message</button>',
        "pending": "<button>Pending</button>",
    }[kind]
    PAGES[f"/in/{slug}"] = profile(name, degree, buttons)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _session(self):
        """The logged-in account's slug from the li_at cookie ("" = the default single account, None = not logged in)."""
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "li_at" and (v == "ok" or v.startswith("ok.")):
                return v[3:]
        return None

    def _cookie_value(self, cookie):
        return "ok" if cookie is True else f"ok.{cookie}; Max-Age=31536000"      # an account session survives a browser restart

    def _html(self, body, cookie=False):
        self.send_response(200); self.send_header("Content-Type", "text/html")
        if cookie:
            self.send_header("Set-Cookie", f"li_at={self._cookie_value(cookie)}; Path=/")
        self.end_headers(); self.wfile.write(body.encode())

    def _go(self, where, cookie=False):
        self.send_response(302); self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", f"li_at={self._cookie_value(cookie)}; Path=/")
        self.end_headers()

    def _login_page(self, error=""):
        r = [secrets.token_hex(4) for _ in range(3)]          # dynamic ids, like the real page
        self._html(LOGIN_PAGE.format(error=f'<div role="alert">{error}</div>' if error else "", r0=r[0], r1="ember" + r[1], r2="ember" + r[2]))

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/_approved":
            self._html("yes" if LOGIN["approved"] else "no"); return
        if path == "/_by_hand":                               # tests: "a person logged in / solved the puzzle in the window"
            self._html("yes" if LOGIN.get("by_hand") else "no"); return
        if path == "/checkpoint/by-hand" and LOGIN.get("by_hand"):
            self._go("/feed/", cookie=True); return
        if path == "/login":
            self._login_page(); return
        if path == "/checkpoint/approved" and LOGIN["approved"]:
            pending = (self.headers.get("Cookie") or "").split("pending_as=")[-1].split(";")[0] if "pending_as=" in (self.headers.get("Cookie") or "") else ""
            self._go("/feed/", cookie=pending or True); return
        session = self._session()
        if LOGIN["required"] and session is None:
            self._go("/login"); return
        if path in ("/in/me", "/in/me/"):                     # the agent asks who is logged in
            self.send_response(302); self.send_header("Location", f"/in/{session or 'test-sender'}/"); self.end_headers(); return
        acct = next((a for a in LOGIN.get("accounts", {}).values() if path == f"/in/{a['slug']}/"), None)
        if acct:
            self._html(f"<html><head><title>{acct['name']} | LinkedIn</title></head><body><main><h1>{acct['name']}</h1></main></body></html>"); return
        if path == "/in/test-sender/":
            body = "<html><head><title>Test Sender | LinkedIn</title></head><body><main><h1>Test Sender</h1></main></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode()); return
        body = PAGES.get(path) or {"/feed/": "<html><body><main>Your feed</main></body></html>",
                                   "/messaging/": "<html><body><main><h1>Messaging</h1><ul></ul></main></body></html>",
                                   "/in/blocked": "<html><body><main>Let's do a quick security check to continue</main></body></html>"}.get(path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        path = self.path.split("?")[0]
        if path == "/login":
            form = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            LOGIN["attempts"].append(form.get("session_key", ""))
            acct = LOGIN.get("accounts", {}).get(form.get("session_key", ""))
            if acct:
                if form.get("session_password") != acct["password"]:
                    self._login_page("Wrong email or password. Try again or create an account."); return
                mode = acct.get("mode", "direct")
                if mode == "direct":
                    self._go("/feed/", cookie=acct["slug"]); return
                body = {"code": CODE_PAGE.format(error=""), "app": APP_PAGE.format(), "captcha": CAPTCHA_PAGE}[mode]
                self.send_response(200); self.send_header("Content-Type", "text/html")
                self.send_header("Set-Cookie", f"pending_as={acct['slug']}; Path=/"); self.end_headers(); self.wfile.write(body.encode()); return
            if form.get("session_key") != LOGIN["email"] or form.get("session_password") != LOGIN["password"]:
                self._login_page("Wrong email or password. Try again or create an account."); return
            mode = LOGIN["mode"]
            if mode == "direct":
                self._go("/feed/", cookie=True); return
            self._html({"code": CODE_PAGE.format(error=""), "app": APP_PAGE.format(), "captcha": CAPTCHA_PAGE}[mode]); return
        if path == "/checkpoint/challenge/verify":
            pin = {k: v[0] for k, v in parse_qs(raw.decode()).items()}.get("pin", "")
            LOGIN["codes"].append(pin)
            if pin == LOGIN["code"]:
                cookie = self.headers.get("Cookie") or ""
                pending = cookie.split("pending_as=")[-1].split(";")[0] if "pending_as=" in cookie else ""
                self._go("/feed/", cookie=pending or True); return
            self._html(CODE_PAGE.format(error='<div role="alert">That code didn\'t work. Please enter a valid code.</div>')); return
        EVENTS.append({**json.loads(raw or b"{}"), "by": self._session()})
        self.send_response(200); self.end_headers()


def start():
    PAGES.clear(); EVENTS.clear()
    LOGIN.clear(); LOGIN.update(json.loads(json.dumps(LOGIN_DEFAULTS)))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
