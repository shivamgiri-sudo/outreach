"""Static check that the Chrome assistant stays an assistant: no LinkedIn automation calls anywhere in its source.

    python3 scripts/check_extension_rules.py        # exits 1 and lists every violation

Comments are ignored (the rule is explained in them); string contents are not, so a forbidden call cannot hide there.
"""
import json
import re
import sys
from pathlib import Path

EXT = Path(__file__).resolve().parent.parent / "extensions" / "linkedin-assistant"
SOURCE_SUFFIXES = (".js", ".html")

FORBIDDEN = [
    (r"\.click\s*\(", "clicks an element (a person must click)"),
    (r"\b(?:requestSubmit|submit)\s*\(", "submits a form"),
    (r"dispatchEvent\s*\(\s*new\s+(?:Mouse|Keyboard|Pointer|Touch|Wheel|Focus|Input)?Event\s*\(\s*['\"`](?:click|dblclick|mouse\w*|key\w*|pointer\w*|touch\w*|wheel|submit|focus\w*|contextmenu)['\"`]",
     "dispatches a mouse / keyboard style event"),
    (r"\bnew\s+(?:Mouse|Keyboard|Pointer|Touch|Wheel)Event\b", "builds a mouse / keyboard event"),
    (r"\b(?:initMouseEvent|initKeyboardEvent|createEvent)\s*\(", "builds a synthetic event"),
    (r"\bsetInterval\s*\(", "runs on a timer"),
    (r"\bsetTimeout\s*\(", "uses a timer (no delays, jitter or pacing)"),
    (r"\bMath\.random\b", "randomness (no human-like jitter)"),
    (r"\b(?:scrollBy|scrollTo|scrollIntoView|scrollIntoViewIfNeeded)\s*\(", "scrolls the page"),
    (r"\bscrollTop\s*=(?!=)", "scrolls the page"),
    (r"\bwindow\.scroll\s*\(", "scrolls the page"),
    (r"\bexecCommand\s*\(", "simulates typing"),
    (r"\bchrome\.scripting\b", "injects scripts"),
    (r"\bchrome\.webRequest\b", "intercepts requests"),
    (r"\bchrome\.tabs\.(?:create|update|remove)\s*\(", "opens, navigates or closes tabs for the person"),
    (r"\bwindow\.open\s*\(", "opens windows"),
]
CONTENT_ONLY = [
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon", "content script makes a network call (only the background talks to the server)"),
    (r"\blocation\.(?:href\s*=(?!=)|assign|replace)|\bhistory\.(?:pushState|replaceState|go|back|forward)", "content script navigates"),
]


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"(?<![:'\"\w\\])//[^\n]*", "", src)


def scan_text(name: str, src: str, content_script: bool = False) -> list:
    """Violations as 'file:line: what' for one file's text."""
    code = strip_comments(src)
    rules = FORBIDDEN + (CONTENT_ONLY if content_script else [])
    found = []
    for pattern, why in rules:
        for m in re.finditer(pattern, code):
            found.append(f"{name}:{code.count(chr(10), 0, m.start()) + 1}: {why}: {m.group(0).strip()}")
    return found


def check_manifest(manifest: dict) -> list:
    problems = []
    if manifest.get("manifest_version") != 3:
        problems.append("manifest: must be Manifest V3")
    if not set(manifest.get("permissions", [])) <= {"storage", "activeTab", "sidePanel"}:
        problems.append(f"manifest: permissions beyond storage/activeTab/sidePanel: {manifest.get('permissions')}")
    if manifest.get("host_permissions") != ["https://www.linkedin.com/*"]:
        problems.append(f"manifest: host_permissions must be only https://www.linkedin.com/*: {manifest.get('host_permissions')}")
    if "<all_urls>" in json.dumps(manifest):
        problems.append("manifest: <all_urls> is not allowed")
    for script in manifest.get("content_scripts", []):
        if script.get("matches") != ["https://www.linkedin.com/*"] or script.get("js") != ["content.js"]:
            problems.append(f"manifest: unexpected content script {script}")
        if script.get("all_frames") or script.get("world") == "MAIN":
            problems.append("manifest: content script must run in the isolated world, top frame only")
    for optional in manifest.get("optional_host_permissions", []):
        if optional not in ("https://*/*", "http://localhost/*", "http://127.0.0.1/*"):
            problems.append(f"manifest: unexpected optional host permission {optional}")
    return problems


def check_all(root: Path = EXT) -> list:
    problems = []
    manifest = root / "manifest.json"
    problems += check_manifest(json.loads(manifest.read_text(encoding="utf-8"))) if manifest.exists() else ["manifest.json is missing"]
    files = sorted(p for p in root.rglob("*") if p.suffix in SOURCE_SUFFIXES)
    for path in files:
        problems += scan_text(str(path.relative_to(root)), path.read_text(encoding="utf-8"), content_script=path.name == "content.js")
    content = root / "content.js"
    head = content.read_text(encoding="utf-8")[:2500] if content.exists() else ""
    if "forbids automation" not in head or "MUST NOT" not in head:
        problems.append("content.js: the 'LinkedIn forbids automation' rule must be stated in the comment at the top")
    return problems


if __name__ == "__main__":
    bad = check_all()
    print("\n".join(bad) if bad else "extension rules: ok")
    sys.exit(1 if bad else 0)
