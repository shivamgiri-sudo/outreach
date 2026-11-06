"""The designed (HTML) version of an outreach email. Same words as the plain text, laid out with the company logo.

Every sequence email goes out as multipart: the plain text stays exactly as written (and is what the outbox keeps),
and this HTML is the alternative part most mail apps show. Built only from the text, so nothing can differ between
the two: paragraphs, "- Label: detail" lines become a styled list, links become links (the demo link a button), the
sign-off and the unsubscribe line go to the footer. Email-safe: tables, inline styles, no scripts, no web fonts.
"""
import re
from html import escape

BRAND = "#0369a1"        # same blue as the app
BRAND_DARK = "#075985"
INK = "#0f172a"
MUTED = "#475569"
SOFT = "#e0f2fe"
FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
LOGO_PATH = "/bmi-logo.png"

_URL = re.compile(r"https?://[^\s<>()]+[^\s<>().,;:!?'\"]")
_UNSUB = re.compile(r"^\s*not relevant\?\s*unsubscribe:\s*(\S+)\s*$", re.I)
_BULLET = re.compile(r"^\s*[-•]\s+(.*)$")


def _inline(text: str, demo_url: str = "") -> str:
    """Escape, then turn URLs into links (the demo link gets its own button, so it is linked plainly here)."""
    out, last = [], 0
    for m in _URL.finditer(text):
        out.append(escape(text[last:m.start()]))
        url = m.group(0)
        out.append(f'<a href="{escape(url, quote=True)}" style="color:{BRAND};text-decoration:underline">{escape(url)}</a>')
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out)


def _bullets(lines: list) -> str:
    rows = []
    for line in lines:
        item = _BULLET.match(line).group(1)
        label, sep, rest = item.partition(":")
        body = (f'<b style="color:{INK}">{escape(label.strip())}</b>{escape(sep)}{_inline(rest)}' if sep and len(label) <= 60
                else _inline(item))
        rows.append(f'<tr><td valign="top" style="padding:6px 10px 6px 0;width:14px"><div style="width:8px;height:8px;'
                    f'border-radius:4px;background:{BRAND};margin-top:7px"></div></td>'
                    f'<td style="padding:6px 0;font:15px/1.6 {FONT};color:{MUTED}">{body}</td></tr>')
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:4px 0 18px;background:{SOFT};'
            f'border-radius:10px;padding:10px 16px;width:100%"><tr><td><table role="presentation" cellpadding="0" '
            f'cellspacing="0">{"".join(rows)}</table></td></tr></table>')


def render(text: str, base_url: str = "", demo_url: str = "") -> str:
    """The HTML alternative for one email body (plain text in, full HTML document out)."""
    text = str(text or "").replace("\r\n", "\n").strip()
    lines = text.split("\n")
    unsub = ""
    keep = []
    for line in lines:
        m = _UNSUB.match(line)
        if m:
            unsub = m.group(1)
        else:
            keep.append(line)
    blocks = re.split(r"\n\s*\n", "\n".join(keep).strip())
    # the sign-off ("Regards,\nTeam …") and anything after it (the sender's address footer) go below the card
    sign_at = next((i for i, b in enumerate(blocks) if re.match(r"^(regards|best|thanks|thank you|warm regards|cheers)\b", b.strip(), re.I)), None)
    body_blocks, tail = (blocks, []) if sign_at is None else (blocks[:sign_at], blocks[sign_at:])
    parts = []
    for i, block in enumerate(body_blocks):
        block_lines = [l for l in block.split("\n") if l.strip()]
        if not block_lines:
            continue
        bullets = [l for l in block_lines if _BULLET.match(l)]
        lead_in = [l for l in block_lines if not _BULLET.match(l)]
        if lead_in:
            para = "<br>".join(_inline(l) for l in lead_in)
            is_greeting = i == 0 and len(para) < 60
            is_question = i == len(body_blocks) - 1 and para.rstrip().endswith("?")
            if is_question:
                parts.append(f'<p style="margin:6px 0 18px;padding:12px 16px;border-left:4px solid {BRAND};background:#f8fafc;'
                             f'font:600 16px/1.6 {FONT};color:{INK}">{para}</p>')
            else:
                parts.append(f'<p style="margin:0 0 16px;font:{"600 " if is_greeting else ""}15px/1.7 {FONT};'
                             f'color:{INK if is_greeting else MUTED}">{para}</p>')
        if bullets:
            parts.append(_bullets(bullets))
    if demo_url:
        parts.append(f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 6px"><tr><td style="border-radius:8px;'
                     f'background:{BRAND}"><a href="{escape(demo_url, quote=True)}" style="display:inline-block;padding:12px 22px;'
                     f'font:600 15px {FONT};color:#ffffff;text-decoration:none;border-radius:8px">See your sample screen</a></td></tr></table>')
    sign = ""
    if tail:
        first = tail[0].split("\n")
        sign = (f'<p style="margin:22px 0 0;font:15px/1.6 {FONT};color:{MUTED}">{escape(first[0])}<br>'
                f'<b style="color:{INK}">{"<br>".join(escape(x) for x in first[1:])}</b></p>')
    extra = "".join(f'<p style="margin:6px 0 0;font:12px/1.6 {FONT};color:#94a3b8">{"<br>".join(_inline(x) for x in b.split(chr(10)))}</p>'
                    for b in tail[1:])
    logo = (f'<img src="{escape(base_url.rstrip("/") + LOGO_PATH, quote=True)}" width="190" alt="BookMyInterview.ai" '
            f'style="display:block;border:0;height:auto;max-width:190px">' if base_url else
            f'<span style="font:700 20px {FONT};color:{INK}">BookMyInterview<span style="color:{BRAND}">.ai</span></span>')
    footer_unsub = (f'Not relevant? <a href="{escape(unsub, quote=True)}" style="color:#64748b;text-decoration:underline">Unsubscribe</a>'
                    if unsub else "")
    preheader = escape(re.sub(r"\s+", " ", " ".join(body_blocks[1:2]))[:140])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light only"><title>BookMyInterview.ai</title></head>
<body style="margin:0;padding:0;background:#eef2f7">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{preheader}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#eef2f7"><tr><td align="center" style="padding:28px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px">
<tr><td style="background:#ffffff;border-radius:14px 14px 0 0;padding:22px 32px;border-bottom:3px solid {BRAND}">{logo}</td></tr>
<tr><td style="background:linear-gradient(90deg,{BRAND},{BRAND_DARK} 55%,#e8552b);background-color:{BRAND};height:6px;font-size:0;line-height:0">&nbsp;</td></tr>
<tr><td style="background:#ffffff;padding:30px 32px 26px;border-radius:0 0 14px 14px">
{"".join(parts)}{sign}{extra}
</td></tr>
<tr><td style="padding:18px 32px 0;text-align:center;font:12px/1.6 {FONT};color:#64748b">
<b style="color:{MUTED}">BookMyInterview.ai</b><br>{footer_unsub}
</td></tr>
</table></td></tr></table>
</body></html>"""
