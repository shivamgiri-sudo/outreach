"""Shared helpers for the free-experience flow."""
import secrets
from datetime import datetime

from .activation import new_activation

DEFAULT_OFFER = {
    "candidate_cap": 25, "booking_cap": 5, "trial_days": 30, "referral_bonus_candidates": 10,
    "mailboxes": 1, "per_mailbox_per_day": 20, "target_leads": 1000,
    "claim_url": "https://bookmyinterview.ai/register",
}
TOKEN_BYTES = 8


def get_offer(store) -> dict:
    """The free-experience numbers plus, under "offer", what is sold (app/brand.py). The booking link the owner sets
    for the offer is the claim link unless an older claim_url setting exists."""
    from .brand import brand_is_default, get_offer_profile
    settings = store.read("settings")
    profile = get_offer_profile(settings)
    claim = profile["booking_link"] or (DEFAULT_OFFER["claim_url"] if brand_is_default(settings) else "")
    return {**DEFAULT_OFFER, "claim_url": claim, **settings, "offer": profile}


def get_offer_profile(store) -> dict:
    """What is sold, to whom, the three value points, the call to action and the demo / booking link."""
    from .brand import get_offer_profile as profile
    return profile(store)


def offer_line(offer: dict) -> str:
    custom = str(((offer or {}).get("offer") or {}).get("offer_line") or "").strip()
    if custom:
        return custom
    return (f"Your first role is free for {offer['trial_days']} days (up to {offer['candidate_cap']} candidates "
            f"screened and {offer['booking_cap']} interviews booked). Paid plans only if you choose to continue.")


def ensure_demo(lead: dict, now: datetime) -> dict:
    """Return the lead with a private demo token, referral code and activation record."""
    return {
        **lead,
        "demo_token": lead.get("demo_token") or secrets.token_urlsafe(TOKEN_BYTES),
        "referral_code": lead.get("referral_code") or secrets.token_urlsafe(TOKEN_BYTES),
        "activation": lead.get("activation") or new_activation(now),
    }


def public_base_url(store, default: str) -> str:
    """The address recipients can reach, set in the app (falls back to the environment value)."""
    return (store.read("settings").get("public_base_url") or default).rstrip("/")


def webhook_secret(store, default: str) -> str:
    return store.read("settings").get("webhook_secret") or default
