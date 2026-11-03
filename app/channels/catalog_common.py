"""Building blocks for provider entries, plus the generic Custom HTTP and Custom webhook connectors."""

PLACEHOLDERS = ("to", "phone", "email", "subject", "text", "name", "company", "template", "title", "start", "end",
                "join_url")
FREE_PLAN_NOTE = "Free plan or trial available. Check the provider's site for current limits and prices."
PAID_NOTE = "Paid service. Check the provider's site for current prices."
WEBHOOK_SECRET_NOTE = "The address below contains a secret code. Treat it like a password and only paste it into the provider's dashboard."


def field(key, label, required=False, secret=False, placeholder="", help="", kind="text", options=None, default=None):
    out = {"key": key, "label": label, "required": required, "secret": secret, "placeholder": placeholder,
           "help": help, "kind": kind}
    if options is not None:
        out["options"] = options
    if default is not None:
        out["default"] = default
    return out


def provider(pid, channel, title, why, docs_url, fields, steps, test, capabilities, **extra):
    return {"id": pid, "channel": channel, "title": title, "why": why, "docs_url": docs_url, "fields": fields,
            "steps": steps, "test": test, "capabilities": capabilities, "recommended": False, "cost_note": "",
            "webhook": None, "oauth": False, "link_based": False, "custom": False, **extra}


def webhook(auth="token", where="", events=(), methods=("POST",)):
    """auth: token (secret in the URL / X-Webhook-Secret / basic-auth password) or a provider signature scheme."""
    return {"auth": auth, "where": where, "events": list(events), "methods": list(methods)}


def template_map_field():
    return field("template_map", "Template names", kind="textarea",
                 placeholder="first_after_yes = bmi_first_message\nnudge_demo_not_opened = bmi_demo_nudge",
                 help="One per line: the app's message name = the name (or ID) of the approved template at the "
                      "provider. Leave empty if the names are the same.")


def custom_http(channel: str, extra_fields=(), extra_placeholders=()) -> dict:
    fields = [
        field("method", "Method", True, kind="select", options=["POST", "GET", "PUT", "PATCH"], default="POST"),
        field("url", "Address (URL)", True, placeholder="https://api.yourprovider.com/v1/send", kind="url",
              help="Must start with https://. Private or internal addresses are blocked."),
        field("headers", "Headers", kind="headers",
              help="Usually the API key goes here, e.g. Authorization = Bearer <key>. Tick 'secret' to hide a value."),
        field("body_type", "Body format", kind="select", options=["json", "form", "none"], default="json"),
        field("body_template", "Body template", kind="textarea",
              placeholder='{"to": "{{phone}}", "message": "{{text}}"}',
              help="Use {{placeholders}}; they are filled in (and safely escaped for JSON) when sending."),
        field("success_path", "Success field", placeholder="status",
              help="Where the reply says it worked, e.g. status or data.ok. Empty = any 2xx reply counts as success."),
        field("success_value", "Success value", placeholder="ok",
              help="Optional. If set, the success field must equal this text."),
        field("id_path", "Message ID field", placeholder="data.id", help="Optional. Where the reply puts the message ID."),
        *extra_fields,
    ]
    return provider(
        "custom_http", channel, "Custom HTTP (any provider)",
        "Connect any provider with an HTTP API, even one not listed here.",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview", fields,
        ["Open your provider's API documentation and find the 'send' request (method and address).",
         "Copy the address into URL and pick the method (usually POST).",
         "Add the headers it needs, usually the API key. Tick 'secret' so the key is hidden afterwards.",
         "Write the body using the placeholders below; click a chip to insert one.",
         "Tell the app which reply field means success (and where the message ID is), then Save.",
         "Click Send test: you see the exact request (keys hidden) and the provider's reply."],
        "Renders the request with sample data, sends it, and shows the request (keys hidden) and the reply.",
        ["send"], custom=True, placeholders=[*PLACEHOLDERS, *extra_placeholders], cost_note="Depends on your provider.")


MAPPING_FIELDS = {
    "email": [field("from_path", "Sender email field", True, placeholder="data.from"),
              field("text_path", "Message text field", True, placeholder="data.text"),
              field("subject_path", "Subject field", placeholder="data.subject"),
              field("message_id_path", "Message ID field", placeholder="data.message_id"),
              field("in_reply_to_path", "In-Reply-To field", placeholder="data.in_reply_to")],
    "phone": [field("phone_path", "Sender phone field", True, placeholder="data.from"),
              field("text_path", "Message text field", True, placeholder="data.text"),
              field("message_id_path", "Message ID field", placeholder="data.id")],
    "events": [field("type_path", "Event type field", True, placeholder="event"),
               field("email_path", "Email address field", True, placeholder="email"),
               field("id_path", "Event or message ID field", placeholder="id"),
               field("bounce_value", "Value meaning 'bounced'", placeholder="bounce", default="bounce"),
               field("complaint_value", "Value meaning 'marked as spam'", placeholder="complaint", default="complaint"),
               field("delivered_value", "Value meaning 'delivered'", placeholder="delivered", default="delivered")],
}


def custom_webhook(channel: str, mapping: str) -> dict:
    return provider(
        "custom_webhook", channel, "Custom webhook (any provider)",
        "Receive messages or events from any provider that can call a web address.",
        "https://en.wikipedia.org/wiki/Webhook", MAPPING_FIELDS[mapping],
        ["Save this card once to get your private webhook address.",
         "Paste the address into your provider's webhook or callback setting.",
         "Ask the provider to send one sample (or copy a sample from its docs) and paste it in the sample box.",
         "Fill in which field holds the sender, the text and the ID; the preview shows what the app will read.",
         "Save. Every call must carry the secret (it is already inside the address)."],
        "Paste a sample payload: the app shows exactly what it would read from it.",
        ["receive"], custom=True, mapping=mapping, webhook=webhook("token", "Your provider's webhook / callback setting"),
        cost_note="No cost from this app.")
