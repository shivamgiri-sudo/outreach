"""Placeholders shared by every template: brand placeholders ({{my_company}} ...) and custom lead fields ({{custom.<key>}}).

Custom lead fields: the value saved on the lead under `custom.<key>` fills {{custom.<key>}}. An empty value renders as
nothing (a safe fallback), so a lead that lacks the detail is never held back or sent a raw {{...}}.

Brand placeholders belong to the brand / offer settings. Until those are saved, `brand_variables` falls back to the
older sender settings (sender_company, sender_website, sender_name) so a preset's emails can be previewed and sent.
"""
import re

BRAND_PLACEHOLDERS = ("my_company", "my_product", "my_tagline", "my_value_1", "my_value_2", "my_value_3", "my_cta",
                      "my_website", "sign_off")
CUSTOM_PREFIX = "custom."
# {{name}} or {{custom.key}}, no spaces inside (the same rule sequences.render has always had).
VAR = re.compile(r"\{\{(\w+(?:\.\w+)?)\}\}")
CUSTOM_KEY = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def is_custom_placeholder(name: str) -> bool:
    return name.startswith(CUSTOM_PREFIX) and bool(CUSTOM_KEY.match(name[len(CUSTOM_PREFIX):]))


def custom_variables(lead: dict) -> dict:
    """{"custom.<key>": text} for every custom value saved on the lead."""
    out = {}
    for key, value in ((lead or {}).get("custom") or {}).items():
        if value is None or value == "" or not CUSTOM_KEY.match(str(key)):
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        out[CUSTOM_PREFIX + key] = str(value)
    return out


# Where the brand / offer settings keep each value (settings "brand" and "offer", as the Brand page saves them).
_SOURCES = {"my_company": ("brand", "company_name"), "my_product": ("brand", "product_name"), "my_tagline": ("brand", "tagline"),
            "my_website": ("brand", "website"), "sign_off": ("brand", "sign_off"), "my_value_1": ("offer", "value_1"),
            "my_value_2": ("offer", "value_2"), "my_value_3": ("offer", "value_3"), "my_cta": ("offer", "cta")}


def _brand_value(settings: dict, name: str) -> str:
    group, key = _SOURCES[name]
    saved = settings.get(group) if isinstance(settings.get(group), dict) else {}
    for value in (saved.get(key), settings.get(name)):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def brand_variables(settings: dict) -> dict:
    """Brand placeholder values from the settings, with sender_* fallbacks. Empty ones are left out (so they show as
    missing and the engine holds the email instead of sending a gap)."""
    settings = settings or {}
    out = {name: _brand_value(settings, name) for name in BRAND_PLACEHOLDERS}
    out["my_company"] = out["my_company"] or str(settings.get("sender_company") or "").strip()
    out["my_website"] = out["my_website"] or str(settings.get("sender_website") or "").strip()
    if not out["sign_off"]:
        who = str(settings.get("sender_name") or "").strip()
        team = out["my_company"]
        out["sign_off"] = "Regards,\n" + (f"{who}\n{team}" if who and team else who or (f"Team {team}" if team else "")) if (who or team) else ""
    return {k: v for k, v in out.items() if v}
