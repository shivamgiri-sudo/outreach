"""The Chrome assistant must stay an assistant: a person clicks everything on LinkedIn. These tests read its source."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_extension_rules", ROOT / "scripts" / "check_extension_rules.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)
EXT = ROOT / "extensions" / "linkedin-assistant"


def test_extension_source_has_no_forbidden_calls():
    assert rules.check_all() == []


def test_files_exist():
    for name in ("manifest.json", "background.js", "content.js", "popup.html", "popup.js", "sidepanel.html", "sidepanel.js", "README.md",
                 "icons/icon16.png", "icons/icon48.png", "icons/icon128.png"):
        assert (EXT / name).is_file(), name


def test_manifest_is_narrow_and_every_referenced_file_exists():
    m = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert rules.check_manifest(m) == []
    assert m["manifest_version"] == 3 and set(m["permissions"]) == {"storage", "activeTab", "sidePanel"}
    assert m["host_permissions"] == ["https://www.linkedin.com/*"]
    assert "<all_urls>" not in json.dumps(m)
    refs = [m["background"]["service_worker"], m["action"]["default_popup"], m["side_panel"]["default_path"],
            *m["icons"].values(), *[j for c in m["content_scripts"] for j in c["js"]]]
    for ref in refs:
        assert (EXT / ref).is_file(), ref


def test_readme_and_content_script_state_the_rule():
    readme = (EXT / "README.md").read_text(encoding="utf-8")
    assert "forbids automation" in readme and "does NOT" in readme
    assert "forbids automation" in (EXT / "content.js").read_text(encoding="utf-8")[:2500]


@pytest.mark.parametrize("snippet", [
    "document.querySelector('button').click();",
    "el.dispatchEvent(new MouseEvent('click', {bubbles: true}));",
    "el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}));",
    "el.dispatchEvent(new Event('mousedown'));",
    "setInterval(() => next(), 1000);",
    "setTimeout(go, 500 + Math.random() * 2000);",
    "window.scrollBy(0, 400);",
    "list.scrollTo({top: 0});",
    "form.submit();",
    "document.execCommand('insertText', false, t);",
    "chrome.scripting.executeScript({});",
    "window.open(url);",
])
def test_checker_catches_forbidden_calls(snippet):
    assert rules.scan_text("x.js", snippet), snippet


def test_checker_ignores_comments_but_not_strings():
    assert rules.scan_text("x.js", "// never call .click( or setInterval(\n/* scrollBy( */ const a = 1;") == []
    assert rules.scan_text("x.js", "const s = 'https://x.y'; const t = a.click();")


def test_checker_flags_network_and_navigation_in_content_script_only():
    assert rules.scan_text("content.js", "fetch('/x')", content_script=True)
    assert not rules.scan_text("background.js", "fetch('/x')")
    assert rules.scan_text("content.js", "location.href = 'https://x'", content_script=True)


def test_checker_flags_a_wide_manifest():
    base = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert rules.check_manifest({**base, "permissions": ["storage", "scripting"]})
    assert rules.check_manifest({**base, "host_permissions": ["<all_urls>"]})
    assert rules.check_manifest({**base, "optional_host_permissions": ["<all_urls>"]})
