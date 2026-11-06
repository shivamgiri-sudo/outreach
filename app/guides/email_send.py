"""Guides: sending email (Resend, SendGrid, Postmark, Amazon SES, Gmail, any SMTP mailbox) and the DNS records."""
from .common import (CHECK_PRICES, ERR_KEY_REJECTED, ERR_MASKED, ERR_NETWORK, ERR_NOT_SAVED, KEY_SAFETY, PASSWORD_SAFETY,
                     TEST_WIZARD, guide, hint)

CAT = "Email sending"
SENDER_FIELDS = {
    "domain": hint("Only the domain, no https:// and no email. Example: mail.yourbrand.in",
                   r"^(?!https?:)(?!.*@)[A-Za-z0-9.-]+\.[A-Za-z]{2,}$",
                   "Type only the domain, like mail.yourbrand.in (no https://, no @, no spaces)."),
    "from_name": hint("The name people see in their inbox, e.g. Priya from YourBrand."),
    "from_email": hint("A full email address on your sending domain, e.g. priya@mail.yourbrand.in",
                       r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", "This is not a full email address. It needs a name, @ and a domain."),
}
SMTP_FIELDS = {
    "smtp_host": hint("A server name such as smtp.zoho.in. Not a web address: no https://.",
                      r"^(?!https?:)[A-Za-z0-9.-]+\.[A-Za-z]{2,}$", "Type only the server name, like smtp.zoho.in (no https://)."),
    "smtp_port": hint("A number: 587 (with starttls) or 465 (with ssl). Never 25.", r"^(587|465|2525)$",
                      "Use 587 (security: starttls) or 465 (security: ssl). Port 25 is blocked on most servers."),
    "smtp_username": hint("Usually your full email address."),
    "smtp_password": hint("The mailbox password or, better, an app password made only for this app."),
}
SENDER_PASTE = [("Sending domain", "Your separate cold-email domain, e.g. mail.yourbrand.in"),
                ("From name", "The name people see"), ("From email", "An address on that domain")]
WARMUP = ("Start slowly: about 20 emails a day for the first two weeks, then increase a little each week. Sending a "
          "lot from a new domain on day one lands you in spam.")
ERR_SPAM = ["My test email went to spam", "Normal for a new domain. Check the DNS records are verified (see the DNS "
            "guide), send a few emails to friends who reply, and warm up slowly."]
ERR_DOMAIN = ["'Domain not verified' or 'sender not allowed'", "The From email must be on the domain you verified at the "
              "provider. Open the DNS records guide and check every record shows as verified."]
REL = ["dns_records", "imap"]

DNS = guide(
    "dns_records", "DNS records for email: SPF, DKIM and DMARC", CAT,
    "Prove to Gmail and Outlook that your emails are really from you, so they reach the inbox instead of spam.",
    ["Your email provider (Resend, SendGrid, Google, Zoho ...) shows a short list of records to add. Keep that page open.",
     "In plain words: SPF says which services may send for your domain. DKIM is a signature on every email. DMARC "
     "tells inboxes what to do with emails that fail the checks.",
     "Open the website where you bought the domain (or where its DNS is managed) in another tab:",
     "GoDaddy: log in, My Products, find your domain, press DNS (or Manage DNS). Press Add New Record.",
     "Hostinger: hPanel, Domains, Manage next to the domain, then DNS / Nameservers. Use 'Manage DNS records'.",
     "Cloudflare: log in, pick the domain, then DNS, Records, Add record. For mail records keep the cloud orange "
     "'Proxy' off (grey 'DNS only').",
     "Namecheap: Domain List, Manage next to the domain, Advanced DNS tab, Add New Record.",
     "Add each record exactly as the provider shows it: Type (TXT, CNAME or MX), Name / Host, and Value. Copy and "
     "paste; do not type by hand.",
     "Name / Host tip: many hosts add your domain at the end by themselves. If the provider says "
     "'resend._domainkey.mail.yourbrand.in', you may only need to type 'resend._domainkey.mail'.",
     "Only one SPF record is allowed per domain. If a TXT record starting with v=spf1 already exists, add the new "
     "include: part into that same record instead of making a second one.",
     "DMARC: if the provider does not give one, add a TXT record with Name _dmarc and Value v=DMARC1; p=none; "
     "(this only watches, it never blocks).",
     "Go back to your email provider and press Verify. It can take from a few minutes up to 48 hours."],
    cost="Free. DNS changes cost nothing.", time="15 minutes, then up to 48 hours waiting",
    test="Your email provider's domain page shows Verified next to each record. Then send a test from the app.",
    errors=[["Still 'pending' after an hour", "DNS can take up to 48 hours. Check for typos, and that the Name did not get "
             "your domain added twice (mail.yourbrand.in.yourbrand.in)."],
            ["'Multiple SPF records' warning", "Join them into one TXT record: v=spf1 include:first include:second ~all"],
            ["Cloudflare record not working", "Switch the proxy cloud to grey (DNS only) for mail records."],
            ["I cannot find DNS settings", "Your domain may point to another company's nameservers. The DNS page usually "
             "says 'managed by ...'. Add the records there instead."]],
    safety="Do not delete existing MX records for a domain that already receives email; you would stop your own mail. "
           "That is one more reason to use a separate domain for cold email.",
    links=[["Google: email sender guidelines", "https://support.google.com/a/answer/81126"]],
    attach=["integration:sending_domain"], keywords="spf dkim dmarc mx txt cname godaddy hostinger cloudflare namecheap dns verify domain")

RESEND = guide(
    "resend", "Resend (email sending)", CAT, "Sends your sequence emails from your own domain. Easiest to set up.",
    ["Buy a separate look-alike domain for cold email (for example yourbrand-mail.in or mail.yourbrand.in). Never "
     "send cold email from your main company domain.",
     "Go to resend.com and sign up.", "Open Domains, press Add Domain and type your sending domain.",
     "Resend shows DNS records. Add them at your domain host (see the DNS records guide) and wait until Resend says Verified.",
     "Open API Keys, press Create API Key, give it a name such as Outreach and choose 'Sending access'. Copy the key now: "
     "Resend shows it only once.",
     "In this app: Integrations > Email sending > Resend > Set up. Fill in the boxes and press Save and continue.",
     "Send yourself a test email. " + WARMUP],
    cost="Has a free plan with a monthly email allowance; paid plans for more. " + CHECK_PRICES, time="20 minutes plus DNS waiting",
    paste=[*SENDER_PASTE, ("Resend API key", "The key from API Keys (starts with re_)")], test=TEST_WIZARD,
    errors=[ERR_KEY_REJECTED, ERR_DOMAIN, ERR_SPAM, ERR_NOT_SAVED, ERR_MASKED], safety=KEY_SAFETY,
    links=[["Resend", "https://resend.com"]], related=REL,
    attach=["hub:email_send:resend", "integration:sending_domain", "setup:sending_domain", "setup:test_email"],
    fields={**SENDER_FIELDS, "api_key": hint("Starts with re_ followed by letters and numbers.", r"^re_[A-Za-z0-9_]{8,}$",
                                             "Resend keys start with re_. Copy it again from Resend > API Keys.")},
    keywords="resend api key email sending domain")

SENDGRID = guide(
    "sendgrid", "SendGrid (email sending)", CAT, "A large, long-running email sending service owned by Twilio.",
    ["Go to sendgrid.com and create an account.", "Settings > Sender Authentication > Authenticate Your Domain. Pick your DNS "
     "host, type the sending domain and add the records it shows (see the DNS records guide). Press Verify.",
     "Settings > API Keys > Create API Key. Choose Restricted Access and switch on only 'Mail Send'. Copy the key: it is "
     "shown only once.", "In this app: Integrations > Email sending > SendGrid > Set up. Paste and Save.",
     "Send yourself a test. " + WARMUP],
    cost="Free trial or small free plan; paid plans for volume. " + CHECK_PRICES, time="20 minutes plus DNS waiting",
    paste=[*SENDER_PASTE, ("SendGrid API key", "The key from Settings > API Keys (starts with SG.)")], test=TEST_WIZARD,
    errors=[ERR_KEY_REJECTED, ["'The from address does not match a verified Sender Identity'", "Finish Domain Authentication "
             "for the From email's domain, or verify that single sender under Sender Authentication."], ERR_SPAM, ERR_MASKED],
    safety=KEY_SAFETY + " Give the key only 'Mail Send' access, nothing more.", links=[["SendGrid", "https://sendgrid.com"]],
    related=REL, attach=["hub:email_send:sendgrid"],
    fields={**SENDER_FIELDS, "api_key": hint("Starts with SG. and is about 69 characters long.", r"^SG\.[A-Za-z0-9_\-.]{20,}$",
                                             "SendGrid keys start with SG. (capital S, capital G, then a dot).")},
    keywords="sendgrid twilio api key")

POSTMARK = guide(
    "postmark", "Postmark (replies and follow-ups only)", CAT,
    "Very fast delivery for replies to people who wrote back. Postmark does not allow cold or bulk email.",
    ["Use Postmark only for people who already replied. For cold email use Resend, SendGrid or a mailbox.",
     "Go to postmarkapp.com, create an account and a Server.", "Sender Signatures > Add Domain. Add the DKIM and "
     "Return-Path records it shows at your DNS host (see the DNS records guide).",
     "Open your Server > API Tokens. Copy the Server API token (not the Account token).",
     "In this app: Integrations > Email sending > Postmark > Set up. Paste and Save. Leave Message stream as outbound."],
    cost="Free developer allowance for testing; paid after. " + CHECK_PRICES, time="20 minutes plus DNS waiting",
    paste=[*SENDER_PASTE, ("Server API token", "From your Server > API Tokens"), ("Message stream", "outbound (leave as is)")],
    test=TEST_WIZARD, errors=[["'Account token' error or rejected", "Use the Server API token, found inside the Server, "
                               "not the Account API token."], ERR_DOMAIN, ERR_MASKED,
                              ["Account under review / pending approval", "New Postmark accounts are reviewed before sending "
                               "to outside addresses. Reply to their email and wait for approval."]],
    safety=KEY_SAFETY, links=[["Postmark", "https://postmarkapp.com"]], related=REL, attach=["hub:email_send:postmark"],
    fields={**SENDER_FIELDS, "server_token": hint("Looks like 8-4-4-4-12 letters and numbers with dashes.",
                                                  r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                                                  "A Postmark server token looks like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f.")},
    keywords="postmark server token")

SES = guide(
    "ses_smtp", "Amazon SES (email sending)", CAT, "Low-cost sending from Amazon Web Services. More steps than Resend.",
    ["Log in to the AWS console and choose the Mumbai region (ap-south-1) at the top right.",
     "Open Amazon SES > Verified identities > Create identity > Domain. Add the DKIM records it shows (see the DNS guide).",
     "New accounts are in a 'sandbox' and can only send to verified addresses. On the SES Account dashboard press "
     "'Request production access' and describe your use honestly.",
     "SMTP settings > Create SMTP credentials. AWS makes a special SMTP username and password. Download or copy both "
     "now; the password is shown once. This is NOT your AWS login.",
     "In this app: Integrations > Email sending > Amazon SES > Set up. Server: email-smtp.ap-south-1.amazonaws.com, port "
     "587, security starttls. Paste the SMTP username and password. Save."],
    cost="Pay per email, usually very cheap. " + CHECK_PRICES, time="30 minutes plus approval (often a day)",
    paste=[*SENDER_PASTE, ("SMTP server", "email-smtp.ap-south-1.amazonaws.com"), ("Port", "587"),
           ("SMTP username / password", "The SMTP credentials from SES (not your AWS login)")],
    test=TEST_WIZARD, errors=[["'Email address is not verified'", "You are still in the sandbox. Request production access, or "
                               "verify the address you are testing with."],
                              ["Login fails", "Use the SMTP credentials made under SES > SMTP settings, and the same region "
                               "in the server name as where you made them."], ERR_NETWORK],
    safety=KEY_SAFETY, links=[["Amazon SES console", "https://console.aws.amazon.com/ses/"]], related=REL,
    attach=["hub:email_send:ses_smtp"], fields={**SENDER_FIELDS, **SMTP_FIELDS}, keywords="aws amazon ses smtp")

GMAIL = guide(
    "gmail_smtp", "Gmail / Google Workspace (app password)", CAT,
    "Send from a Google mailbox. Needs a 16-letter app password, not your normal Gmail password.",
    ["Use a separate Google Workspace mailbox on your cold-email domain. Do not use your main company mailbox.",
     "Log in to that Google account and open myaccount.google.com > Security.",
     "Turn on 2-Step Verification (Google requires it before app passwords).",
     "Open myaccount.google.com/apppasswords (or search 'App passwords' in the account's search box).",
     "Type a name such as Outreach and press Create. Google shows a 16-letter password in four groups. Copy it.",
     "In this app: Integrations > Email sending > Gmail / Google Workspace > Set up. Paste your Gmail address and the "
     "app password. Spaces do not matter. Save.",
     "Send yourself a test. Google limits how many emails one mailbox may send per day; keep volumes low. " + WARMUP],
    cost="Included in Gmail / Google Workspace.", time="10 minutes",
    paste=[*SENDER_PASTE, ("Gmail address", "The full mailbox address"), ("App password (16 letters)", "The app password from Google")],
    test=TEST_WIZARD,
    errors=[["'Username and Password not accepted' (535)", "You used your normal password or the app password was deleted. "
             "Make a new app password and paste that."],
            ["I cannot find App passwords", "Turn on 2-Step Verification first. On a work account, your Google Workspace "
             "admin may have switched app passwords off: ask them, or use Resend instead."],
            ["Sending stops with a daily limit error", "Google limits each mailbox per day. Lower your daily sending or add "
             "a second mailbox."], ERR_SPAM],
    safety=PASSWORD_SAFETY + " To remove access later, delete the app password at myaccount.google.com/apppasswords.",
    links=[["Google: sign in with app passwords", "https://support.google.com/accounts/answer/185833"],
           ["Google app passwords page", "https://myaccount.google.com/apppasswords"]], related=REL,
    attach=["hub:email_send:gmail_smtp"],
    fields={**SENDER_FIELDS, "smtp_username": hint("Your full Gmail or Workspace address.", r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$",
                                                   "Type the full email address, like name@yourbrand.in."),
            "app_password": hint("16 letters, often shown as 4 groups of 4 (abcd efgh ijkl mnop).",
                                 r"^\s*([A-Za-z]{4}\s?){3}[A-Za-z]{4}\s*$",
                                 "This does not look like a Google app password. Your normal Gmail password will not work "
                                 "here. Make an app password at myaccount.google.com/apppasswords: it is 16 letters.")},
    keywords="gmail google workspace app password smtp 2-step")

SMTP_COMMON_ERR = [["Login failed / authentication error", "Check the username is the full email address, and use an app "
                    "password if your provider has them."],
                   ["Connection timed out", "Use port 587 with starttls or 465 with ssl. Port 25 is blocked on most servers."],
                   ["'Sender address rejected'", "The From email must be the same mailbox (or an alias of it) you log in with."]]


def _mailbox(gid, title, summary, steps, host, port, security, *, extra_err=(), links=(), keywords=""):
    return guide(gid, title, CAT, summary, [*steps, "In this app: Integrations > Email sending > Any SMTP server > Set up. "
                                            f"Server {host}, port {port}, security {security}. Paste your email address as "
                                            "SMTP username and the (app) password. Save, then send yourself a test."],
                 cost="Included with your mailbox plan.", time="10 minutes",
                 paste=[*SENDER_PASTE, ("SMTP server", host), ("Port", str(port)), ("Security", security),
                        ("SMTP username", "Your full email address"), ("SMTP password", "Your app password")],
                 test=TEST_WIZARD, errors=[*extra_err, *SMTP_COMMON_ERR], safety=PASSWORD_SAFETY, links=links,
                 related=REL, attach=["hub:email_send:smtp"], fields={**SENDER_FIELDS, **SMTP_FIELDS}, keywords=keywords)


SMTP = guide(
    "smtp", "Any SMTP mailbox", CAT, "Send through the mailbox you already have (Zoho, Hostinger, GoDaddy, Outlook ...).",
    ["Search the web for '<your provider> SMTP settings' and open the provider's own help page.",
     "Write down: server name, port, and whether it uses starttls (port 587) or ssl (port 465).",
     "If your provider offers app passwords, make one for this app.",
     "In this app: Integrations > Email sending > Any SMTP server > Set up. Fill in the boxes and Save.",
     "Send yourself a test. Ready-made settings for Zoho, Hostinger, GoDaddy and Outlook are in their own guides."],
    cost="Depends on your mailbox provider.", time="10 minutes",
    paste=[*SENDER_PASTE, ("SMTP server / Port / Security", "From the provider's SMTP help page"),
           ("SMTP username / password", "Your email address and (app) password")],
    test=TEST_WIZARD, errors=SMTP_COMMON_ERR, safety=PASSWORD_SAFETY, related=["smtp_zoho", "smtp_hostinger", "smtp_godaddy",
                                                                               "smtp_outlook", *REL],
    attach=["hub:email_send:smtp"], fields={**SENDER_FIELDS, **SMTP_FIELDS},
    keywords="smtp mailbox server port")

ZOHO = _mailbox("smtp_zoho", "Zoho Mail (SMTP)", "Send from a Zoho Mail mailbox.",
                ["Log in to Zoho Mail. Indian accounts use the .in servers (smtp.zoho.in); others use smtp.zoho.com.",
                 "If 2-factor sign-in is on, make an app password: Zoho Accounts > Security > App Passwords > Generate.",
                 "In Zoho Mail settings check that IMAP/SMTP access is allowed for the mailbox."],
                "smtp.zoho.in", 465, "ssl", links=[["Zoho Mail SMTP settings", "https://www.zoho.com/mail/help/zoho-smtp.html"]],
                keywords="zoho mail smtp")
HOSTINGER = _mailbox("smtp_hostinger", "Hostinger email (SMTP)", "Send from a Hostinger business email box.",
                     ["Log in to hPanel, open Emails, pick your domain and the mailbox.",
                      "Note the mailbox password (reset it there if you do not know it)."],
                     "smtp.hostinger.com", 465, "ssl", keywords="hostinger email smtp")
GODADDY = _mailbox("smtp_godaddy", "GoDaddy email (SMTP)", "Send from a GoDaddy mailbox.",
                   ["Log in to GoDaddy and open Email & Office to see which email you have.",
                    "Microsoft 365 from GoDaddy: use the Outlook / Microsoft 365 guide (server smtp.office365.com).",
                    "Older GoDaddy Workspace Email: server smtpout.secureserver.net, port 465, ssl."],
                   "smtpout.secureserver.net", 465, "ssl", keywords="godaddy workspace email smtp secureserver")
OUTLOOK = _mailbox("smtp_outlook", "Outlook / Microsoft 365 (SMTP)", "Send from a Microsoft 365 work mailbox.",
                   ["Warning: Microsoft is switching off password logins for SMTP. If this stops working, use Resend or "
                    "another provider instead.",
                    "Your Microsoft 365 admin must allow 'Authenticated SMTP' for the mailbox: Microsoft 365 admin center > "
                    "Users > Active users > the user > Mail > Manage email apps > tick Authenticated SMTP.",
                    "If the account uses multi-factor sign-in, an app password may be needed (if your admin allows them)."],
                   "smtp.office365.com", 587, "starttls",
                   extra_err=[["'SmtpClientAuthentication is disabled'", "Ask your admin to tick Authenticated SMTP for the "
                               "mailbox (see step 2)."]], keywords="outlook office 365 microsoft smtp hotmail")

GUIDES = [DNS, RESEND, SENDGRID, POSTMARK, SES, GMAIL, SMTP, ZOHO, HOSTINGER, GODADDY, OUTLOOK]
