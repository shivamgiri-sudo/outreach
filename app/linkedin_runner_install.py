"""Office runner installers and bundle, served by the portal (routes_linkedin_runner).

The one-line commands embed only the portal's public address and the short-lived pairing code; the scripts
(deploy/office-runner/install.ps1 and install.sh) are templates with __PORTAL__ and __CODE__. The bundle holds only the
runner's own files: the agent, the runner, the cloud-login helper, the shared agent pool and the minimal Playwright
package (deploy/linkedin-runtime/package*.json)."""
import io
import os
import re
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "deploy" / "office-runner"
BUNDLE = (                                       # (path inside the bundle, source)
    ("tools/linkedin_agent.mjs", ROOT / "tools" / "linkedin_agent.mjs"),
    ("tools/linkedin_runner.mjs", ROOT / "tools" / "linkedin_runner.mjs"),
    ("tools/linkedin_cloud_login.mjs", ROOT / "tools" / "linkedin_cloud_login.mjs"),
    ("tools/linkedin_agent_pool.mjs", ROOT / "tools" / "linkedin_agent_pool.mjs"),
    ("package.json", ROOT / "deploy" / "linkedin-runtime" / "package.json"),
    ("package-lock.json", ROOT / "deploy" / "linkedin-runtime" / "package-lock.json"),
)
SAFE_URL = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d{1,5})?(/[A-Za-z0-9._~\-/]*)?$")
SAFE_CODE = re.compile(r"^[A-Z0-9]{4,32}$")


def portal_base(store, request) -> str:
    """The address the office computer uses: the portal's public address (Settings), else OUTREACH_BASE_URL, else the
    address this request came in on."""
    from .growth_core import public_base_url
    fallback = os.environ.get("OUTREACH_BASE_URL", "").strip() or str(request.base_url)
    return public_base_url(store, fallback).rstrip("/")


def _check(portal: str, code: str) -> None:
    if not SAFE_URL.match(portal or ""):
        raise ValueError("The portal's public address is not a plain http(s) address; set it in Settings first.")
    if not SAFE_CODE.match(code or ""):
        raise ValueError("Bad pairing code.")


# Windows PowerShell 5.1 offers only SSL3/TLS 1.0 by default, so the pasted line itself must switch TLS 1.2 on (3072)
# before its first HTTPS request, or `irm` fails against a TLS 1.2+ server before the installer can do it.
TLS12 = "[Net.ServicePointManager]::SecurityProtocol=[Net.ServicePointManager]::SecurityProtocol -bor 3072; "


def commands(portal: str, code: str) -> dict:
    _check(portal, code)
    return {
        "windows": f"powershell -NoProfile -ExecutionPolicy Bypass -Command \"{TLS12}irm '{portal}/api/linkedin-runner/install.ps1?code={code}' | iex\"",
        "mac": f"curl -fsSL '{portal}/api/linkedin-runner/install.sh?code={code}' | bash",
        "linux": f"curl -fsSL '{portal}/api/linkedin-runner/install.sh?code={code}' | bash",
    }


UNINSTALL = {
    "windows": 'powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\\MCN-LinkedIn-Runner\\uninstall.ps1"',
    "mac": 'bash "$HOME/Library/Application Support/MCN-LinkedIn-Runner/uninstall.sh"',
    "linux": 'bash "${XDG_DATA_HOME:-$HOME/.local/share}/mcn-linkedin-runner/uninstall.sh"',
}


def script(kind: str, portal: str, code: str) -> str:
    _check(portal, code)
    name = {"ps1": "install.ps1", "sh": "install.sh"}[kind]
    text = (TEMPLATES / name).read_text(encoding="utf-8")
    return text.replace("__PORTAL__", portal).replace("__CODE__", code)


def refusal(kind: str, message: str) -> str:
    """A script that only explains why it cannot install (an expired or wrong code), so the pasted line says so clearly."""
    message = re.sub(r"[^A-Za-z0-9 .,:()/'-]", "", message)[:300].replace("'", "")
    if kind == "ps1":
        return f"Write-Host '[MCN] {message}' -ForegroundColor Red\n"
    return f"#!/usr/bin/env bash\necho '[MCN] {message}' >&2\nexit 1\n"


def bundle(kind: str) -> bytes:
    buf = io.BytesIO()
    now = time.time()
    if kind == "zip":
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, src in BUNDLE:
                z.writestr(name, src.read_bytes())
    else:
        with tarfile.open(fileobj=buf, mode="w:gz") as t:
            for name, src in BUNDLE:
                data = src.read_bytes()
                info = tarfile.TarInfo(name)
                info.size, info.mtime, info.mode = len(data), int(now), 0o644
                t.addfile(info, io.BytesIO(data))
    return buf.getvalue()
