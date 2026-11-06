"""Building blocks for the plain-English "How do I get this?" guides.

A guide is a plain dict (see `guide`). Each guide lists the screens it belongs to in `attach`:
  hub:<channel>:<provider>   a card / wizard on the Integrations page
  integration:<id>           a connector card on Setup > Connections (and the Data sources page)
  capture:<type>             a Lead capture connector type
  setup:<item>               a row of the Go-live checklist
  app:<name>                 another form in the app (LinkedIn agent, Chrome assistant, team logins ...)
`fields` gives field-level help for the matching form field key: hint (what the value looks like), an optional
`pattern` the value should match (a friendly `message` is shown when it does not) and optional `warn` rules
[{pattern, message}] shown when the value DOES match (e.g. a normal password typed where an app password belongs).
Patterns are written so they work the same in Python and JavaScript.
"""

KEY_SAFETY = ("Treat this key like a password. Never send it on WhatsApp or email, never paste it into a website you do "
              "not trust, and never put it in a shared sheet. If it leaks, delete (revoke) it at the provider and make a "
              "new one; the old one stops working at once.")
PASSWORD_SAFETY = ("Never use your main password here. Use an app password (a separate password made only for this app). "
                   "You can delete it at any time from your account's security page, and that stops this app without "
                   "changing your real password.")
WEBHOOK_SAFETY = ("The web address contains a secret code. Paste it only into the provider's own dashboard. If you think "
                  "someone else has it, press 'Make a new secret' in the app and paste the new address.")
NO_COST = "No cost from this app."
CHECK_PRICES = "Check the provider's own pricing page for current prices and free allowances; they change often."

ERR_KEY_REJECTED = ["The test says the key was rejected",
                    "Copy the key again with the provider's own Copy button (no space at the start or end). Make sure "
                    "you copied the key itself, not its name or ID, and that it has not been deleted or expired."]
ERR_NOT_SAVED = ["The Test button is greyed out", "Press Save (step 2 'Your details') first. Testing uses the saved details."]
ERR_MASKED = ["I see dots instead of my key", "That is on purpose: saved keys are hidden. Leave the box empty to keep the "
              "saved key, or paste a new key to replace it."]
ERR_NETWORK = ["The test says it could not reach the provider",
               "Usually a short outage or a typo in an address. Wait a minute and press Run test again. If it keeps "
               "failing, check the provider's status page."]
ERR_CREDITS = ["It worked yesterday but now fails", "Your free credits or plan limit may be used up. Log in to the "
               "provider and look at usage or billing."]
ERR_WEBHOOK_NOTHING = ["Nothing arrives after I pasted the address",
                       "Check the whole address was pasted (it is long), that this card is saved, and that the right "
                       "events are ticked at the provider. Your public web address (Setup) must be a real https address, "
                       "not localhost."]
ERR_WEBHOOK_OLD = ["It stopped working after I made a new secret",
                   "Making a new secret changes the address. Copy the new address and paste it at the provider again."]

TEST_WIZARD = "Open the card, go to the last step 'Test' and press Run test. A green tick means it works."
TEST_SETUP = "On Setup > Connections press 'Test connection' on this card. A green tick means it works."
TEST_SOURCE = ("On the Integrations page (Lead data tab) open the card and press Run test. Then open Data sources to try "
               "it on a few leads.")
TEST_CAPTURE = "Open the connector and press 'Send a test lead'. It shows up on the Leads page marked as a test."


def guide(gid, title, category, summary, steps, *, cost="", time="", paste=(), test="", errors=(), safety="",
          links=(), attach=(), fields=None, related=(), keywords=""):
    """One guide. `paste` = [(field label in the app, what to paste)], `errors` = [(problem, fix)], `links` = [(label, url)]."""
    return {"id": gid, "title": title, "category": category, "summary": summary, "cost": cost, "time": time,
            "steps": list(steps), "paste": [list(p) for p in paste], "test": test,
            "errors": [list(e) for e in errors], "safety": safety, "links": [list(link) for link in links],
            "attach": list(attach), "fields": dict(fields or {}), "related": list(related), "keywords": keywords}


def hint(text, pattern="", message="", warn=()):
    out = {"hint": text}
    if pattern:
        out["pattern"] = pattern
        out["message"] = message
    if warn:
        out["warn"] = [{"pattern": p, "message": m} for p, m in warn]
    return out


def api_key_guide(gid, title, summary, signup, where, *, attach, label="API key", looks="", pattern="", category="Lead data",
                  cost=CHECK_PRICES, time="5 minutes", test=TEST_SOURCE, extra_steps=(), extra_errors=(), links=(),
                  field_key="api_key", related=(), keywords=""):
    """The common shape: sign up, find the key, paste it, test it."""
    steps = [f"Go to {signup} and create a free account (or log in).", *extra_steps,
             f"Find your key: {where}. Press its Copy button.",
             f"In this app open Integrations > Lead data > {title}, press Set up, paste the key into '{label}' and press "
             "Save and continue.",
             "Press Run test. A green tick means the key works."]
    fields = {field_key: hint(looks or "A long mix of letters and numbers.", pattern,
                              f"This does not look like a {title} key. Copy it again from {where}." if pattern else "")}
    return guide(gid, title, category, summary, steps, cost=cost, time=time, paste=[(label, f"The key from {where}")],
                 test=test, errors=[ERR_KEY_REJECTED, ERR_CREDITS, ERR_MASKED, *extra_errors], safety=KEY_SAFETY,
                 links=links, attach=attach, fields=fields, related=related, keywords=keywords)
