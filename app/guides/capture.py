"""Guides: Lead capture connectors (Meta Lead Ads, Google Ads, website form, sheets, Zapier and Indian lead portals)."""
from .common import ERR_WEBHOOK_NOTHING, KEY_SAFETY, NO_COST, TEST_CAPTURE, guide, hint

CAT = "Lead capture"
TOKEN_SAFETY = ("The capture token is a secret: anyone who has it can add leads. It is shown in full only once. If it leaks, "
                "press 'Make a new token' (the old one stops working) and paste the new one into your tool.")
ERR_TOKEN = ["The tool gets '401' or 'invalid token'", "Send the token as the header X-Capture-Token, or add ?token=YOUR_TOKEN "
             "to the address. If you lost it, make a new token and update your tool."]
ERR_LOST = ["I cannot see the token any more", "It is shown only once for safety. Press 'Make a new token' and copy that one."]
ZAP_STEPS = ["In Zapier add the action 'Webhooks by Zapier' > POST. Paste the address shown on the connector.",
             "Payload type JSON. Add a header named X-Capture-Token with the token shown on the connector."]


def _cap(tid, title, summary, steps, *, cost=NO_COST, time="15 minutes", paste=(), errors=(), fields=None, links=(),
         keywords="", safety=TOKEN_SAFETY):
    return guide(f"capture_{tid}" if tid in ("generic", "google_ads", "meta_ads") else tid, title, CAT, summary,
                 ["On the Integrations page open the 'Lead capture' tab and press Add connector. Choose " f"'{title}'.", *steps,
                  "Press 'Send a test lead' on the connector. It shows on the Leads page, marked as a test."],
                 cost=cost, time=time, paste=paste, test=TEST_CAPTURE, errors=[*errors, ERR_WEBHOOK_NOTHING],
                 safety=safety, links=links, attach=[f"capture:{tid}"], fields=fields, keywords=keywords)


GUIDES = [
    _cap("generic", "Webhook (Zapier, Make, n8n, anything)", "Any tool that can send a web request can send leads here.",
         ["Copy the address and the capture token from the connector.",
          "Zapier: action Webhooks by Zapier > POST, payload JSON, header X-Capture-Token. Make: HTTP > Make a request, "
          "POST, Raw JSON, add the header. n8n: HTTP Request node, POST, JSON body, add the header.",
          "Send fields named name, email, phone, company, city and message; they are recognised on their own."],
         paste=[("In your tool: URL", "The connector's address"), ("In your tool: header X-Capture-Token", "The capture token")],
         errors=[ERR_TOKEN, ERR_LOST], keywords="webhook zapier make n8n capture token"),
    _cap("website_form", "Website or landing-page form", "A small contact form you paste into your website. No secret is visible.",
         ["List the websites allowed to use the form, one per line, starting with https:// (leave empty to allow any site).",
          "Copy the form snippet and paste it into your page where the form should appear (in WordPress: an 'HTML' or "
          "'Custom HTML' block; in Wix: Embed code; in Shopify: a Custom Liquid section).",
          "Or point your own form at the address shown, using the public form key (not the secret token)."],
         paste=[("Websites allowed to use the form", "e.g. https://www.yourbrand.in"), ("Your website", "The form snippet")],
         errors=[["The form says 'not allowed'", "Add your site's exact address (with https:// and www if used) to the allowed "
                  "list."], ["Real people's leads are rejected as spam", "Lower 'Seconds a visitor needs to fill the form' to 2 or 3."]],
         fields={"allowed_origins": hint("One website per line, starting with https://", r"^(https?://[^\s/]+/?\s*\n?)*$",
                                         "Write each site as https://www.yourbrand.in (no page paths), one per line.")},
         safety="The snippet and the public form key are safe to show on your website. Never put the secret capture token "
                "in a web page.", keywords="website form embed snippet wordpress wix"),
    _cap("meta_ads", "Facebook and Instagram Lead Ads", "Leads from Meta Lead Ads arrive within seconds, with the ad they came from.",
         ["In Meta Ads Manager create a Lead Ads form (Instant Form) and use it in your ad.",
          "Go to developers.facebook.com > My Apps. Open (or create) a Business app. Add the 'Webhooks' product, choose Page "
          "and press 'Subscribe to this object'.",
          "Paste the Callback URL and Verify token shown on the connector, press Verify and save. Then subscribe to the "
          "'leadgen' field.", "Connect your Facebook Page to the app (Page settings, or Meta's Leads Access tool).",
          "In the app: App settings > Basic > App secret > Show. Copy it into 'Meta App secret'.",
          "Make a Page access token with the leads_retrieval permission (Business settings > System users > Generate new "
          "token, tick leads_retrieval and pages_manage_metadata). Paste it into 'Page access token' and Save.",
          "Use Meta's Lead Ads Testing Tool to send a test lead too."],
         time="30 minutes", paste=[("At Meta: Callback URL / Verify token", "From the connector"),
                                   ("Meta App secret", "App settings > Basic"), ("Page access token", "With leads_retrieval")],
         errors=[["Meta says 'The URL couldn't be validated'", "Copy the Callback URL and the Verify token again exactly, and "
                  "check your public web address in Setup is a real https address."],
                 ["Leads arrive without name or email", "The Page access token is missing leads_retrieval, or it expired. Make "
                  "a System User token."]],
         fields={"app_secret": hint("32 letters and numbers (a-f, 0-9).", r"^[0-9a-fA-F]{32}$",
                                    "The App secret is 32 characters, from App settings > Basic."),
                 "page_access_token": hint("Starts with EAA and is very long.", r"^EAA[A-Za-z0-9]{20,}$",
                                           "Meta tokens start with EAA. Copy the whole token."),
                 "graph_version": hint("Like v19.0", r"^v\d+\.\d+$", "Write it like v19.0")},
         links=[["Meta for Developers: My Apps", "https://developers.facebook.com/apps"],
                ["Meta Lead Ads Testing Tool", "https://developers.facebook.com/tools/lead-ads-testing"]],
         safety=TOKEN_SAFETY + " " + KEY_SAFETY, keywords="facebook instagram meta lead ads leadgen webhook"),
    _cap("google_ads", "Google Ads lead form", "Leads from a Google Ads lead form arrive as soon as someone submits it.",
         ["In Google Ads open your lead form asset (Assets > Lead forms) and scroll to 'Lead delivery'.",
          "Under Webhook integration paste the webhook URL and the Key shown on the connector.",
          "Press 'Send test data' in Google Ads, then check the Leads page here. Test data is kept apart."],
         paste=[("In Google Ads: Webhook URL", "The connector's address"), ("In Google Ads: Key", "The connector's key")],
         errors=[["Google says the test failed", "Copy the URL and Key again; check your public web address in Setup is a real "
                  "https address."], ERR_LOST], keywords="google ads lead form extension webhook key"),
    _cap("whatsapp_click", "WhatsApp click-to-chat", "People who message your WhatsApp number first become leads.",
         ["Connect WhatsApp first on the Integrations page (WhatsApp tab) with incoming messages switched on.",
          "Switch this connector on. The first message from an unknown number creates a lead and a call-now task.",
          "Use a link like wa.me/919812345678?text=Hi in your ads and website (your number with 91, no + or spaces)."],
         errors=[["Messages do not create leads", "WhatsApp's incoming-message webhook must be set up (see your WhatsApp "
                  "provider's guide)."]], safety="A STOP message never creates a lead.", keywords="whatsapp click to chat wa.me"),
    _cap("sheet_sync", "Google Sheet or CSV link", "Pulls new rows from a published Google Sheet every 15 minutes.",
         ["In Google Sheets choose File > Share > Publish to web. Pick the tab, choose 'Comma-separated values (.csv)' and press "
          "Publish. Copy the link.", "Paste it into 'Published sheet or CSV link' and Save. Columns like Name, Email, Phone and "
          "Company are matched by themselves.", "Tick the consent box only if these people filled a form agreeing to be "
          "contacted. Press Sync now."],
         paste=[("Published sheet or CSV link", "The link from Publish to web")],
         errors=[["'Could not read the sheet'", "Use the 'Publish to web' CSV link (it contains /pub and output=csv), not the "
                  "normal sharing link."]],
         fields={"sheet_url": hint("A Publish-to-web link ending in output=csv", r"^https://\S+$",
                                   "Paste the full link starting with https://",
                                   [(r"/edit", "This is the normal editing link. Use File > Share > Publish to web > CSV instead.")])},
         safety="Anyone with a published link can read that sheet tab. Publish only the tab with leads, nothing private.",
         keywords="google sheet csv publish sync"),
    _cap("linkedin_leadgen", "LinkedIn Lead Gen Forms (via Zapier)", "LinkedIn lead forms reach this app through Zapier.",
         ["In Zapier choose the trigger 'LinkedIn Lead Gen Forms' > New Lead and connect your LinkedIn ad account.", *ZAP_STEPS,
          "Map First Name, Last Name, Email, Phone Number, Company Name, Job Title, Campaign Name and Form Name."],
         cost="Zapier has a free plan; check its limits.", errors=[ERR_TOKEN, ERR_LOST], keywords="linkedin lead gen forms zapier"),
    _cap("indiamart", "IndiaMART", "Each buyer enquiry on IndiaMART arrives here as a lead.",
         ["In IndiaMART Lead Manager open Import / Export Leads > Push API (or ask IndiaMART support to enable it).",
          "Paste the connector's address as the push URL, with ?token= and the capture token at the end."],
         paste=[("In IndiaMART: push URL", "The address + ?token=YOUR_TOKEN")], errors=[ERR_TOKEN, ERR_LOST],
         keywords="indiamart push api enquiry"),
    _cap("justdial", "JustDial", "JustDial leads arrive here as they come in.",
         ["Ask your JustDial account manager to switch on API / webhook lead delivery.",
          "Give them the connector's address with ?token= and the capture token at the end."],
         errors=[ERR_TOKEN, ERR_LOST], keywords="justdial leads webhook"),
    _cap("tradeindia", "TradeIndia", "TradeIndia buyer enquiries arrive here.",
         ["Ask TradeIndia to forward enquiries by webhook / API.", "Give them the connector's address with ?token= and the "
          "capture token at the end."], errors=[ERR_TOKEN, ERR_LOST], keywords="tradeindia enquiries"),
    _cap("tiktok_ads", "TikTok Instant Forms (via Zapier or Make)", "TikTok lead forms reach this app through Zapier or Make.",
         ["In Zapier or Make pick the TikTok Lead Generation trigger and connect your TikTok ads account.", *ZAP_STEPS,
          "Map full name, email, phone number, company and the campaign, ad group, ad and form names."],
         errors=[ERR_TOKEN, ERR_LOST], keywords="tiktok instant forms zapier make"),
]
