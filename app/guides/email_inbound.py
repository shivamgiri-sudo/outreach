"""Guides: reply capture (IMAP, Resend / SendGrid / Postmark inbound), bounce events, and the custom connectors."""
from .common import (ERR_NOT_SAVED, ERR_WEBHOOK_NOTHING, ERR_WEBHOOK_OLD, KEY_SAFETY, NO_COST, PASSWORD_SAFETY,
                     WEBHOOK_SAFETY, guide, hint)

REPLY = "Reply capture"
BOUNCE = "Bounces"
WEBHOOK_TEST = ("Save the card, paste the address at the provider, then send yourself an email and reply to it. The reply "
                "shows on the Replies page within a few minutes.")
EVENT_TEST = "Events arrive as emails are sent. After your first real sends, bounced addresses are stopped automatically."

IMAP = guide(
    "imap", "Reply capture from your mailbox (IMAP)", REPLY,
    "Reads replies from the mailbox your emails come from, so the sequence stops when someone answers.",
    ["Use the mailbox that receives replies (the From address of your emails).",
     "Gmail / Google Workspace: Gmail > Settings (gear) > See all settings > Forwarding and POP/IMAP > Enable IMAP > Save. "
     "Then make an app password (myaccount.google.com/apppasswords; needs 2-Step Verification).",
     "Zoho Mail: server imap.zoho.in (India) or imap.zoho.com, port 993. Make an app password if 2-factor sign-in is on.",
     "Hostinger: server imap.hostinger.com, port 993. GoDaddy Workspace Email: imap.secureserver.net, port 993.",
     "Outlook / Microsoft 365: outlook.office365.com, port 993. Microsoft is switching off password logins, so this may "
     "not work; use a provider's inbound option instead.",
     "In this app: Integrations > Reply capture > Mailbox (IMAP) > Set up. Fill in server, port 993, your email address "
     "and the (app) password. Keep folder INBOX. Save.",
     "Press Run test, then 'Check for replies now'. When automatic running is on, the mailbox is checked every few minutes."],
    cost="No extra cost.", time="10 minutes",
    paste=[("IMAP server", "e.g. imap.gmail.com"), ("Port", "993"), ("Mailbox username", "Your full email address"),
           ("Password or app password", "An app password"), ("Folder", "INBOX")],
    test="Run test counts unread emails without changing anything. Then reply to one of your own test emails and press "
         "'Check for replies now'.",
    errors=[["'Invalid credentials' / login failed", "Use an app password, not your normal password, and the full email "
             "address as username."], ["Gmail says IMAP is disabled", "Turn on IMAP in Gmail settings (step 2)."],
            ["Nothing shows on the Replies page", "Only replies from known leads are matched. Check the folder name, and that "
             "automatic running is on, or press 'Check for replies now'."], ERR_NOT_SAVED],
    safety=PASSWORD_SAFETY, links=[["Gmail: turn on IMAP", "https://support.google.com/mail/answer/7126229"]],
    attach=["hub:inbound_email:imap", "setup:reply_capture"],
    fields={"imap_host": hint("A server name like imap.gmail.com (no https://).", r"^(?!https?:)[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
                              "Type only the server name, like imap.gmail.com."),
            "imap_port": hint("Almost always 993.", r"^(993|143)$", "IMAP uses port 993 (secure). Try 993."),
            "username": hint("Your full email address.", r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", "Type the full email address."),
            "password": hint("An app password works best. Gmail app passwords are 16 letters.")},
    related=["gmail_smtp"], keywords="imap replies inbox gmail zoho outlook app password")


def _inbound(gid, title, summary, steps, attach, *, fields=None, cost="Included in the provider's plans.", category=REPLY,
             test=WEBHOOK_TEST, errors=(), keywords=""):
    return guide(gid, title, category, summary, steps, cost=cost, time="15 minutes",
                 paste=[("Webhook address", "Copy it from step 3 of the card and paste it at the provider")], test=test,
                 errors=[ERR_WEBHOOK_NOTHING, ERR_WEBHOOK_OLD, *errors], safety=WEBHOOK_SAFETY, attach=attach,
                 fields=fields, keywords=keywords)


SIGNING = {"signing_secret": hint("Starts with whsec_ (from Resend > Webhooks > your endpoint).", r"^whsec_[A-Za-z0-9+/=_-]{8,}$",
                                  "Resend signing secrets start with whsec_. Copy it from the webhook endpoint page in Resend.")}
GUIDES = [
    IMAP,
    _inbound("resend_inbound", "Resend inbound (reply capture)", "Resend receives replies and forwards them to this app.",
             ["In Resend set up receiving for your reply domain. It shows MX records: add them at your DNS host (see the DNS "
              "records guide).", "In this app open Integrations > Reply capture > Resend inbound > Set up and press Save.",
              "Step 3 shows the webhook address. Copy it.", "In Resend: Webhooks > Add endpoint. Paste the address and tick "
              "'email.received'.", "Open the new endpoint in Resend, copy its Signing secret (starts with whsec_), paste it "
              "into step 2 here and Save again."],
             ["hub:inbound_email:resend_inbound"], fields=SIGNING,
             errors=[["'Signature check failed'", "The signing secret here must be the one from this exact endpoint in Resend."]],
             keywords="resend inbound receive replies whsec"),
    _inbound("sendgrid_inbound", "SendGrid Inbound Parse (reply capture)", "SendGrid receives replies and posts them here.",
             ["Pick a reply subdomain, e.g. reply.mail.yourbrand.in. At your DNS host add an MX record for it pointing to "
              "mx.sendgrid.net (priority 10).", "In this app open the card, press Save, then copy the address in step 3.",
              "SendGrid: Settings > Inbound Parse > Add Host & URL. Choose the reply subdomain and paste the address. Leave "
              "'POST the raw, full MIME message' unticked.", "Send a reply to an address on the reply subdomain to try it."],
             ["hub:inbound_email:sendgrid_inbound"], keywords="sendgrid inbound parse mx"),
    _inbound("postmark_inbound", "Postmark inbound (reply capture)", "Postmark receives replies and posts them here.",
             ["In this app open the card, press Save, then copy the address in step 3.",
              "Postmark: your Server > Default Inbound Stream > Settings. Paste the address as Webhook URL and save.",
              "Forward replies to the inbound address Postmark shows, or set up an inbound domain with an MX record."],
             ["hub:inbound_email:postmark_inbound"], keywords="postmark inbound"),
    _inbound("resend_events", "Resend bounce events", "Bounces and spam complaints from Resend stop further sending to that address.",
             ["In this app open Integrations > Bounces > Resend events > Set up, press Save and copy the address in step 3.",
              "Resend: Webhooks > Add endpoint. Paste the address and tick email.bounced, email.complained and email.delivered.",
              "Copy the endpoint's Signing secret (whsec_...) into step 2 here and Save again."],
             ["hub:email_events:resend_events"], fields=SIGNING, category=BOUNCE, test=EVENT_TEST, keywords="resend bounce complaint"),
    _inbound("sendgrid_events", "SendGrid bounce events", "Bounces and spam reports from SendGrid stop further sending.",
             ["In this app open the card, press Save and copy the address in step 3.",
              "SendGrid: Settings > Mail Settings > Event Webhook. Paste the address as HTTP Post URL.",
              "Tick Bounced, Spam Reports and Delivered, switch the webhook on and save."],
             ["hub:email_events:sendgrid_events"], category=BOUNCE, test=EVENT_TEST, keywords="sendgrid event webhook bounce"),
    _inbound("postmark_events", "Postmark bounce events", "Bounces and spam complaints from Postmark stop further sending.",
             ["In this app open the card, press Save and copy the address in step 3.",
              "Postmark: your Server > Outbound stream > Webhooks > Add webhook. Paste the address.",
              "Tick Bounce, Spam Complaint and Delivery and save."],
             ["hub:email_events:postmark_events"], category=BOUNCE, test=EVENT_TEST, keywords="postmark bounce webhook"),
    guide("custom_http", "Custom HTTP (any provider not listed)", "Custom",
          "Connect any email, WhatsApp, SMS, calling or calendar service that has an HTTP API. Best done with your tech person.",
          ["Open your provider's API documentation and find the 'send message' request. Note the method (usually POST) and "
           "the address (starts with https://).", "Find where the API key goes (usually a header such as Authorization: "
           "Bearer <key>).", "In this app open the channel's 'Custom HTTP' card. Paste the address, pick the method, add the "
           "header and tick 'secret' next to the key.", "Write the body using the placeholders, for example "
           '{"to": "{{phone}}", "message": "{{text}}"}. Click a chip to insert one.',
           "Tell the app which reply field means success (for example status = ok). Save.",
           "Press Send test. You see the exact request (keys hidden) and the provider's reply."],
          cost="Depends on your provider.", time="30 minutes",
          paste=[("Address (URL)", "The provider's send address"), ("Headers", "Usually Authorization = Bearer <your key>"),
                 ("Body template", "The JSON the provider expects, with {{placeholders}}")],
          test="The Test step sends one request with sample data and shows the provider's reply.",
          errors=[["Reply says 401 or 403", "The key header is wrong. Check the header name and the word Bearer if needed."],
                  ["'Private or internal addresses are blocked'", "The address must be a public https:// address."],
                  ["Reply says 400 Bad Request", "The body does not match what the provider expects. Compare it with an "
                   "example in their documentation."]],
          safety=KEY_SAFETY + " Always tick 'secret' next to header values that hold keys, so they are hidden afterwards.",
          attach=[f"hub:{c}:custom_http" for c in ("email_send", "whatsapp", "sms", "calls", "calendar")],
          fields={"url": hint("Must start with https://", r"^https://\S+$", "The address must start with https://")},
          keywords="custom http api any provider webhook header bearer"),
    guide("custom_webhook", "Custom webhook (receive from any provider)", "Custom",
          "Receive replies, messages or bounce events from any provider that can call a web address.",
          ["In this app open the 'Custom webhook' card and press Save once. Step 3 shows your private address.",
           "Paste the address into your provider's webhook or callback setting.",
           "Get one sample message from the provider (send yourself one, or copy an example from its docs) and paste it "
           "into the sample box.", "Fill in which field holds the sender, the text and the ID. The preview shows exactly "
           "what the app will read.", "Save."],
          cost=NO_COST, time="20 minutes", paste=[("Webhook address", "From step 3, into the provider's webhook setting")],
          test="Paste a sample and press 'Preview what the app reads'.",
          errors=[ERR_WEBHOOK_NOTHING, ERR_WEBHOOK_OLD, ["The preview shows empty values", "The field names are case-sensitive "
                  "and use dots for nesting, e.g. data.from. Copy them exactly from the sample."]],
          safety=WEBHOOK_SAFETY,
          attach=[f"hub:{c}:custom_webhook" for c in ("inbound_email", "email_events", "whatsapp", "sms")],
          keywords="custom webhook callback receive json mapping"),
]
