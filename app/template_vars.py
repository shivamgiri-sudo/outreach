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
    """Brand placeholder values. One source of truth: app/brand.py (the Brand & offer settings, with the original
    business as the default). Kept here so older call sites keep working."""
    from .brand import brand_variables as from_brand
    return from_brand(settings or {})
