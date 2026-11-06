"""Guides: calendars and meeting links (Google, Microsoft, Zoom, Cal.com, Calendly, any booking link)."""
from .common import CHECK_PRICES, ERR_KEY_REJECTED, ERR_MASKED, ERR_NOT_SAVED, KEY_SAFETY, TEST_WIZARD, WEBHOOK_SAFETY, guide, hint

CAT = "Calendar & meetings"
REDIRECT = ("Copy the 'Redirect URI' shown in step 3 of the card in this app (it is based on your public web address, so "
            "set that in Setup first).")
OAUTH_ERR = [["'redirect_uri_mismatch' after pressing Connect", "The redirect address at the provider must match the one "
              "in step 3 exactly, letter for letter, including https:// and no extra slash at the end."],
             ["'Access blocked: app not verified' or 'access_denied'", "Add your own email as a test user on the consent "
              "screen (Google), or ask your admin to allow the app (Microsoft)."], ERR_NOT_SAVED, ERR_MASKED]
BOOKING_URL = hint("Starts with https://", r"^https://\S+$", "Paste the full link starting with https://")

GUIDES = [
    guide("google_calendar", "Google Calendar + Meet", CAT, "Creates the meeting in your Google Calendar with a Meet link.",
          ["Open console.cloud.google.com and create a project (any name, e.g. Outreach).",
           "APIs & Services > Library > search 'Google Calendar API' > Enable.",
           "APIs & Services > OAuth consent screen: choose Internal (if you have Google Workspace) or External. Fill in the "
           "app name and your email. For External, add your own email under Test users.",
           "APIs & Services > Credentials > Create credentials > OAuth client ID > Application type 'Web application'.",
           REDIRECT + " Paste it under 'Authorized redirect URIs' and press Create.",
           "Copy the Client ID (ends with .apps.googleusercontent.com) and the Client secret.",
           "In this app: Integrations > Calendar & meetings > Google Calendar + Meet > Set up. Paste both and Save.",
           "Step 3: press 'Connect Google', choose your account and allow access."],
          cost="Free with a Google account.", time="20 minutes",
          paste=[("OAuth client ID", "Ends with .apps.googleusercontent.com"), ("OAuth client secret", "From the same screen"),
                 ("Calendar", "Leave as primary")],
          test=TEST_WIZARD + " It reads one upcoming event.", errors=OAUTH_ERR, safety=KEY_SAFETY +
          " To disconnect later, remove the app at myaccount.google.com > Security > Third-party connections.",
          links=[["Google Cloud console: credentials", "https://console.cloud.google.com/apis/credentials"]],
          attach=["hub:calendar:google", "setup:calendar"],
          fields={"client_id": hint("Ends with .apps.googleusercontent.com", r"\.apps\.googleusercontent\.com$",
                                    "A Google client ID ends with .apps.googleusercontent.com. Copy it from Credentials."),
                  "client_secret": hint("Usually starts with GOCSPX-")},
          keywords="google calendar meet oauth client id redirect"),
    guide("microsoft_calendar", "Microsoft 365 / Outlook + Teams", CAT, "Creates the meeting in Outlook with a Teams link.",
          ["Open entra.microsoft.com and sign in with your work account. Go to App registrations > New registration.",
           "Give it any name. Under Redirect URI choose 'Web'. " + REDIRECT + " Press Register.",
           "Copy the 'Application (client) ID' from the Overview page.",
           "Certificates & secrets > New client secret > Add. Copy the secret's Value now (not the Secret ID). It is shown once.",
           "API permissions > Add a permission > Microsoft Graph > Delegated: Calendars.ReadWrite, OnlineMeetings.ReadWrite, "
           "User.Read and offline_access. Ask your admin to 'Grant admin consent' if asked.",
           "In this app: Integrations > Calendar & meetings > Microsoft 365 > Set up. Paste and Save, then press "
           "'Connect Microsoft' in step 3. Teams links need a work or school account."],
          cost="Included with Microsoft 365.", time="20 minutes",
          paste=[("Application (client) ID", "From the app's Overview page"), ("Client secret value", "The Value, not the ID"),
                 ("Tenant", "Leave as common")],
          test=TEST_WIZARD + " It lists your calendars.", errors=[*OAUTH_ERR, ["'Invalid client secret'", "You probably copied "
                                                                          "the Secret ID. Make a new secret and copy its Value."]],
          safety=KEY_SAFETY + " Client secrets expire (you choose when); make a new one before that date.",
          links=[["Microsoft Entra admin center", "https://entra.microsoft.com"]], attach=["hub:calendar:microsoft"],
          fields={"client_id": hint("Looks like 8-4-4-4-12 letters and numbers with dashes.",
                                    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                                    "The Application (client) ID looks like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f.")},
          keywords="microsoft outlook teams entra azure calendar"),
    guide("zoom", "Zoom meetings", CAT, "Creates a Zoom link for each booking.",
          ["Open marketplace.zoom.us and sign in. Choose Develop > Build App > Server-to-Server OAuth app.",
           "Copy the Account ID, Client ID and Client secret from App Credentials.",
           "Scopes: add the meeting write and user read permissions (search 'meeting:write' and 'user:read').",
           "Fill the required information page and press Activate.",
           "In this app: Integrations > Calendar & meetings > Zoom > Set up. Paste the three values and Save.",
           "Zoom sends no calendar invite by itself: the meeting link is saved on the lead for you to share."],
          cost="Free plan available; meeting length limits apply. " + CHECK_PRICES, time="15 minutes",
          paste=[("Account ID / Client ID / Client secret", "Zoom app > App Credentials")],
          test=TEST_WIZARD, errors=[ERR_KEY_REJECTED, ["'Invalid access token, does not contain scopes'", "Add the meeting "
                                    "write scope in the Zoom app and activate it again."], ERR_NOT_SAVED],
          safety=KEY_SAFETY, links=[["Zoom App Marketplace", "https://marketplace.zoom.us"]], attach=["hub:calendar:zoom"],
          keywords="zoom server to server oauth"),
    guide("calcom", "Cal.com booking page", CAT, "Leads pick a time on your Cal.com page; bookings appear on the lead.",
          ["Sign up at cal.com and create an event type such as 'Demo (30 min)'. Set your available hours.",
           "Copy that event's public link (e.g. https://cal.com/yourname/demo) into 'Your booking link'.",
           "Optional: Settings > Developer > API keys > create a key (starts with cal_live_) and paste it.",
           "In this app: save the card. Step 3 shows a subscriber address and a secret.",
           "Cal.com: Settings > Developer > Webhooks > New. Paste the address and the secret, tick Booking created and "
           "Booking cancelled, and save."],
          cost="Free plan for one person. " + CHECK_PRICES, time="15 minutes",
          paste=[("Your booking link", "Your event's public link"), ("API key", "Optional, starts with cal_live_")],
          test=TEST_WIZARD + " Then book a test slot yourself and see it on the Meetings page.",
          errors=[["Bookings do not show up", "Check the webhook in Cal.com has the address and secret from step 3, and "
                   "both booking events ticked."], ERR_KEY_REJECTED, ERR_MASKED], safety=WEBHOOK_SAFETY,
          attach=["hub:calendar:calcom"],
          fields={"booking_url": BOOKING_URL, "api_key": hint("Starts with cal_live_", r"^cal_(live|test)_\S+$",
                                                               "Cal.com API keys start with cal_live_.")},
          keywords="cal.com calcom booking scheduling"),
    guide("calendly", "Calendly booking page", CAT, "Leads pick a time on your Calendly page; bookings appear on the lead.",
          ["In Calendly create an event type such as 'Demo'.",
           "Integrations > API & Webhooks > Personal access tokens > Generate new token. Give it a name and copy it (shown once).",
           "In this app: Integrations > Calendar & meetings > Calendly > Set up. Paste the token (and optionally your event "
           "link) and Save.", "Press Run test: the app reads your account and registers the booking webhook for you. "
           "Calendly webhooks need a paid plan; the booking link works on any plan."],
          cost="Free plan for the booking link; webhooks need a paid plan. " + CHECK_PRICES, time="10 minutes",
          paste=[("Personal access token", "Calendly > Integrations > API & Webhooks"), ("Booking link", "Optional")],
          test=TEST_WIZARD, errors=[ERR_KEY_REJECTED, ["'Webhooks need a paid plan'", "Upgrade Calendly, or just share the "
                                                       "booking link and record bookings by hand."], ERR_MASKED],
          safety=KEY_SAFETY, attach=["hub:calendar:calendly"], fields={"booking_url": BOOKING_URL},
          keywords="calendly token booking"),
    guide("booking_link", "Any booking link", CAT, "Share any scheduling page (Zoho Bookings, TidyCal, Google appointment pages ...).",
          ["Open your scheduling tool and copy the public link of your booking page.",
           "If the page accepts the visitor's name or email in the link, put {name} and {email} where they go.",
           "In this app: Integrations > Calendar & meetings > Any booking link > Set up. Paste and Save.",
           "'Book a meeting' on a lead then gives you a ready link to send."],
          cost="Depends on your scheduling tool.", time="5 minutes", paste=[("Booking link", "Your public booking page link")],
          test=TEST_WIZARD + " It shows the link filled in for a sample lead.",
          errors=[["The link opens a login page", "You copied the admin link. Copy the public/share link instead."]],
          safety="The booking link is public by design; it holds no secret.", attach=["hub:calendar:booking_link"],
          fields={"booking_url": BOOKING_URL}, keywords="booking link scheduling"),
]
