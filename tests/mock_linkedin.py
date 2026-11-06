"""A tiny stand-in for LinkedIn profile pages (same button names the agent looks for) to test the agent's logic locally.
It proves the agent's steps and safety stops, NOT that LinkedIn's real pages still match: LinkedIn changes its pages."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGES = {}
EVENTS = []

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
}};
const msg = document.querySelector('[aria-label^="Message"]');
if (msg) msg.onclick = () => {{
  const box = document.createElement('div'); box.setAttribute('role','textbox'); box.setAttribute('contenteditable','true'); box.style.minHeight='40px'; box.style.border='1px solid #999';
  const send = document.createElement('button'); send.textContent = 'Send';
  send.onclick = () => {{ rec({{kind:'message', text: box.innerText}}); box.innerText = ''; }};
  document.body.appendChild(box); document.body.appendChild(send);
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

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/in/me", "/in/me/"):                     # the agent asks who is logged in
            self.send_response(302); self.send_header("Location", "/in/test-sender/"); self.end_headers(); return
        if path == "/in/test-sender/":
            body = "<html><head><title>Test Sender | LinkedIn</title></head><body><main><h1>Test Sender</h1></main></body></html>"
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode()); return
        body = PAGES.get(path) or {"/feed/": "<html><body><main>Your feed</main></body></html>",
                                   "/in/blocked": "<html><body><main>Let's do a quick security check to continue</main></body></html>"}.get(path)
        if body is None:
            self.send_response(404); self.end_headers(); return
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body.encode())

    def do_POST(self):
        EVENTS.append(json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}"))
        self.send_response(200); self.end_headers()


def start():
    PAGES.clear(); EVENTS.clear()
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"
