"""Guides: WhatsApp providers, SMS (with India's DLT rules) and calling."""
from .common import (CHECK_PRICES, ERR_KEY_REJECTED, ERR_MASKED, ERR_NOT_SAVED, KEY_SAFETY, TEST_WIZARD,
                     guide, hint)

WA = "WhatsApp"
SMS = "SMS"
CALLS = "Calls"
TEMPLATE_STEP = ("Create each message as a template at the provider and wait for approval (minutes to a day). WhatsApp only "
                 "lets a business start a chat with an approved template. Put the approved names in 'Template names', "
                 "one per line: the app's name = the provider's name.")
WA_ERR = [["'Template not found' or 'not approved'", "Use the exact approved template name (and language code). Check it "
           "shows Approved at the provider."],
          ["The test number never gets the message", "The number must be on WhatsApp, written with country code (91...). "
           "Practice mode is on until you tick 'Send for real' on the WhatsApp tab."], ERR_KEY_REJECTED, ERR_MASKED]
WA_SAFETY = (KEY_SAFETY + " Only message people who agreed to hear from you on WhatsApp; Meta blocks numbers that get "
             "reported as spam.")
PHONE91 = hint("Country code and number, digits only. Example: 919812345678", r"^\+?\d{11,13}$",
               "Write the number with country code and no spaces, like 919812345678.")
WA_ATTACH_LEGACY = ["integration:whatsapp", "setup:whatsapp"]


def _wa(gid, title, summary, steps, paste, fields, *, cost=CHECK_PRICES, extra_attach=(), links=(), keywords=""):
    return guide(gid, title, WA, summary, steps, cost=cost, time="30 minutes, plus template approval", paste=paste,
                 test=TEST_WIZARD + " Type your own WhatsApp number to receive a real test message.", errors=WA_ERR,
                 safety=WA_SAFETY, links=links, attach=[f"hub:whatsapp:{gid}", *extra_attach, *WA_ATTACH_LEGACY], fields=fields,
                 related=["whatsapp_click"], keywords=keywords)


DLT = guide(
    "india_dlt", "India SMS rules: DLT registration explained", SMS,
    "In India every business SMS must be registered on a DLT portal first. Without it, messages are blocked.",
    ["What DLT is: a government (TRAI) system run by the phone companies. You register your business, your sender name "
     "and the exact text of each SMS once.",
     "Register your business as an 'Entity' on any one DLT portal: Jio TrueConnect, Airtel DLT, Vi (VILPOWER) or BSNL. "
     "You need your PAN, GST or other company papers and an authorisation letter. You get an Entity ID (a long number).",
     "Register a Header (sender ID): 6 letters that appear as the sender, for example YRBRND.",
     "Register each message as a Content Template. Write the parts that change as {#var#}, for example: "
     "'Hi {#var#}, your demo is booked for {#var#}. - YourBrand'. You get a Template ID. Approval can take a few days.",
     "Cold outreach counts as Promotional: it may only go out between 10 am and 9 pm, and never to numbers on the "
     "Do Not Disturb list. Messages people asked for (like a booking confirmation) can be Service messages.",
     "Your SMS provider (MSG91, Fast2SMS, Twilio) asks for the Entity ID, Header and Template ID. Add them there exactly."],
    cost="The DLT portals charge a small registration fee. Check the portal you choose.", time="1 hour of forms, then 2 to 7 days of approvals",
    paste=[("DLT Entity ID", "From the DLT portal (long number)"), ("Sender ID (header)", "Your 6-letter header"),
           ("DLT Template ID", "From the DLT portal, one per message text")],
    test="Send a test SMS from the provider's card with your own mobile number.",
    errors=[["SMS fails with 'template mismatch'", "The text sent must match the registered template exactly, except the "
             "{#var#} parts. Even a changed full stop fails."],
            ["Header rejected", "Headers must relate to your brand name and be exactly 6 letters for promotional/service use."],
            ["Messages not delivered to some numbers", "Those numbers are on the Do Not Disturb list, or it is outside 10 am "
             "to 9 pm for promotional SMS."]],
    safety="Keep your DLT portal login private. Anyone with it can register templates in your company's name.",
    links=[["Jio TrueConnect (DLT)", "https://trueconnect.jio.com"], ["Vi VILPOWER (DLT)", "https://www.vilpower.in"]],
    attach=["hub:sms:msg91", "hub:sms:fast2sms", "hub:sms:twilio_sms"], keywords="dlt trai entity header template sms india dnd")

SMS_ERR = [["'DLT template not found' or SMS not delivered", "Open the DLT guide. The IDs and the text must match what is "
            "registered."], ERR_KEY_REJECTED, ERR_MASKED]

GUIDES = [
    _wa("meta_cloud", "Meta WhatsApp Cloud API", "Official WhatsApp API straight from Meta, no middleman. Most steps, lowest cost.",
        ["Create or open your Meta Business account at business.facebook.com and complete Business verification (GST or "
         "Udyam papers help).",
         "Go to developers.facebook.com > My Apps > Create App. Choose a Business type app and add the WhatsApp product.",
         "WhatsApp > API Setup: add your business phone number (one NOT already used in the normal WhatsApp app) and copy "
         "the 'Phone number ID' (a long number, not your phone number).",
         "business.facebook.com > Settings > Users > System users > Add. Then 'Generate new token', pick your app and tick "
         "whatsapp_business_messaging and whatsapp_business_management. Copy the token: this one does not expire after 24 hours.",
         "In your app: App settings > Basic > App secret > Show. Copy it.",
         TEMPLATE_STEP + " (WhatsApp Manager > Message templates.)",
         "In this app: Integrations > WhatsApp > Meta WhatsApp Cloud API > Set up. Paste and Save.",
         "Step 3 shows a Callback URL and a Verify token. In your Meta app: WhatsApp > Configuration > Webhook > Edit, paste "
         "both, press Verify and save, then subscribe to 'messages'."],
        [("Phone number ID", "From WhatsApp > API Setup"), ("Permanent access token", "The System User token"),
         ("App secret", "From App settings > Basic"), ("Template names", "app name = approved template name")],
        {"phone_number_id": hint("Digits only, about 15 long. Not your phone number.", r"^\d{10,20}$",
                                 "The Phone number ID is a long number made only of digits, shown under WhatsApp > API Setup. "
                                 "It is not your mobile number."),
         "access_token": hint("Starts with EAA and is very long.", r"^EAA[A-Za-z0-9]{20,}$",
                              "Meta access tokens start with EAA. Copy the whole token from the System user page."),
         "app_secret": hint("32 letters and numbers (a-f, 0-9).", r"^[0-9a-fA-F]{32}$",
                            "The App secret is 32 characters. Copy it from App settings > Basic > App secret > Show."),
         "graph_version": hint("Like v23.0", r"^v\d+\.\d+$", "Write it like v23.0 (a small v, a number, a dot, a number).")},
        cost="Meta charges per message or conversation; prices for India are on Meta's WhatsApp pricing page.",
        links=[["Meta for Developers: My Apps", "https://developers.facebook.com/apps"],
               ["Meta Business settings", "https://business.facebook.com/settings"]], keywords="meta whatsapp cloud api facebook token phone number id"),
    _wa("gupshup", "Gupshup (WhatsApp)", "Popular Indian WhatsApp provider with a self-serve dashboard.",
        ["Sign up at gupshup.io and create a WhatsApp app (give it a short name, e.g. YourBrandOutreach).",
         "Register your business number and finish Meta business verification from the Gupshup dashboard.",
         TEMPLATE_STEP + " Gupshup uses template IDs: put those IDs on the right-hand side.",
         "Copy your API key (profile menu at the top right) and note the app name.",
         "In this app: Integrations > WhatsApp > Gupshup > Set up. Paste and Save.",
         "For replies: in the Gupshup app settings set the Callback URL to the address shown in step 3."],
        [("API key", "From Gupshup's profile menu"), ("Your WhatsApp number (with 91)", "e.g. 919812345678"),
         ("App name", "Exactly as in Gupshup")], {"source_number": PHONE91}, keywords="gupshup whatsapp"),
    _wa("interakt", "Interakt (WhatsApp)", "Simple Indian WhatsApp tool with a shared team inbox.",
        ["Sign up at interakt.shop and connect your WhatsApp Business number (they guide you through Meta verification).",
         TEMPLATE_STEP + " (Interakt > Templates.)", "Settings > Developer Setting: copy the Secret Key.",
         "In this app: Integrations > WhatsApp > Interakt > Set up. Paste and Save.",
         "On the same Interakt page paste the webhook address shown in step 3 to receive replies."],
        [("Secret key", "Interakt > Settings > Developer Setting"), ("Country code", "+91")], {}, keywords="interakt whatsapp"),
    _wa("wati", "WATI (WhatsApp)", "WhatsApp team inbox with an API.",
        ["Sign up at wati.io and connect your WhatsApp Business number.", TEMPLATE_STEP + " (WATI > Broadcast > Template messages.)",
         "Open 'API Docs' in the WATI menu. Copy the API endpoint (the address at the top) and the access token.",
         "In this app: Integrations > WhatsApp > WATI > Set up. Paste the endpoint and the token WITHOUT the word Bearer. Save.",
         "WATI > Webhooks: add the address shown in step 3 for 'Message received'."],
        [("API endpoint", "e.g. https://live-mt-server.wati.io/123456"), ("Access token", "Without the word Bearer")],
        {"api_endpoint": hint("Starts with https:// and ends with your account number.", r"^https://\S+$",
                              "Paste the full address starting with https:// from WATI > API Docs."),
         "access_token": hint("A long token. Leave out the word 'Bearer'.", "", "",
                              [(r"^\s*[Bb]earer\s", "Remove the word 'Bearer' and the space; paste only the token.")])},
        keywords="wati whatsapp"),
    _wa("aisensy", "AiSensy (WhatsApp)", "Indian WhatsApp marketing tool; sends approved templates through API campaigns.",
        ["Sign up at aisensy.com and connect your WhatsApp Business number.", TEMPLATE_STEP,
         "In AiSensy create an 'API campaign' for each approved template. Put the campaign names in 'Template names'.",
         "Manage > API key: copy it. AiSensy's API sends approved templates only, not free text.",
         "In this app: Integrations > WhatsApp > AiSensy > Set up. Paste and Save."],
        [("API key", "AiSensy > Manage > API key"), ("Template names", "app name = AiSensy campaign name")], {},
        keywords="aisensy whatsapp"),
    DLT,
    guide("msg91", "MSG91 (SMS)", SMS, "Indian SMS provider with DLT built in.",
          ["Finish DLT registration first (see the DLT guide): Entity ID, Header and Template IDs.",
           "Sign up at msg91.com. Add your DLT entity and header in the SMS settings.",
           "SMS > Templates: add the same text with its DLT Template ID and sender ID. Changing parts are named var1, var2... "
           "Copy MSG91's own template ID.", "Click your name (top right) > Authkey. Copy it.",
           "In this app: Integrations > SMS > MSG91 > Set up. Paste and Save. Then tick 'Send for real' when ready."],
          cost=CHECK_PRICES, time="20 minutes (after DLT approval)",
          paste=[("Authkey", "MSG91 > your name > Authkey"), ("MSG91 template ID", "MSG91's ID, not the DLT one"),
                 ("Sender ID (header)", "Your 6-letter header"), ("DLT Entity ID / Template ID", "From the DLT portal")],
          test=TEST_WIZARD + " Type your mobile number to get a real SMS.", errors=SMS_ERR, safety=KEY_SAFETY,
          attach=["hub:sms:msg91"], related=["india_dlt"],
          fields={"sender_id": hint("Exactly 6 letters.", r"^[A-Za-z]{6}$", "A DLT header is exactly 6 letters, like YRBRND."),
                  "dlt_entity_id": hint("Digits only, usually 19 long.", r"^\d{12,20}$", "The DLT Entity ID is a long number."),
                  "dlt_template_id": hint("Digits only, usually 19 long.", r"^\d{12,20}$", "The DLT Template ID is a long number.")},
          keywords="msg91 sms authkey"),
    guide("twilio_sms", "Twilio SMS", SMS, "Global SMS service. India still needs DLT registration.",
          ["Sign up at twilio.com. On the Console home page copy the Account SID (starts with AC) and Auth token (press Show).",
           "For India: follow Twilio's India SMS guidelines to register your sender ID and templates on DLT (see the DLT guide).",
           "Buy a number or create a Messaging Service (its SID starts with MG).",
           "In this app: Integrations > SMS > Twilio SMS > Set up. Paste and Save.",
           "Replies: in Twilio, open the number > Messaging > 'A message comes in' and paste the address from step 3."],
          cost="Trial credit to start; then pay per message. " + CHECK_PRICES, time="20 minutes (after DLT approval)",
          paste=[("Account SID", "Starts with AC"), ("Auth token", "Console home > Auth token"),
                 ("From or Messaging service SID", "Your number, sender ID, or MG...")],
          test=TEST_WIZARD, errors=SMS_ERR, safety=KEY_SAFETY, attach=["hub:sms:twilio_sms"], related=["india_dlt"],
          links=[["Twilio Console", "https://console.twilio.com"]],
          fields={"account_sid": hint("Starts with AC and is 34 characters.", r"^AC[0-9a-fA-F]{32}$",
                                      "A Twilio Account SID starts with AC followed by 32 letters and numbers."),
                  "messaging_service_sid": hint("Starts with MG.", r"^MG[0-9a-fA-F]{32}$", "A Messaging Service SID starts with MG.")},
          keywords="twilio sms sid auth token"),
    guide("fast2sms", "Fast2SMS", SMS, "Low-cost Indian SMS on the DLT route.",
          ["Finish DLT registration first (see the DLT guide).", "Sign up at fast2sms.com. Open DLT SMS and add your "
           "entity, header and template. Fast2SMS gives each template a message ID.", "Open Dev API and copy your API key.",
           "In this app: Integrations > SMS > Fast2SMS > Set up. Paste and Save."],
          cost="Prepaid wallet. " + CHECK_PRICES, time="15 minutes (after DLT approval)",
          paste=[("API key", "Fast2SMS > Dev API"), ("Sender ID (header)", "Your 6-letter header"),
                 ("DLT message ID in Fast2SMS", "Fast2SMS's ID for the template")],
          test=TEST_WIZARD, errors=SMS_ERR, safety=KEY_SAFETY, attach=["hub:sms:fast2sms"], related=["india_dlt"],
          fields={"sender_id": hint("Exactly 6 letters.", r"^[A-Za-z]{6}$", "A DLT header is exactly 6 letters.")},
          keywords="fast2sms sms"),
    guide("click_to_call", "Click-to-call link (your dialler)", CALLS,
          "Lets a representative press Call on a lead and have your existing dialler dial the number.",
          ["Ask whoever runs your dialler (or its support) for a 'click-to-call link' or 'call URL'.",
           "It is a web address with the phone number inside it, e.g. https://dialler.example.com/call?number=9812345678.",
           "Replace the phone number part with {phone} exactly, e.g. https://dialler.example.com/call?number={phone}.",
           "In this app: Integrations > Calls > Your dialler's click-to-call link > Set up (or Setup > Connections > "
           "Calling). Paste and Save.", "No dialler? Choose Manual on Setup: representatives dial by hand and log the result."],
          cost="No extra cost.", time="5 minutes",
          paste=[("Click-to-call URL template", "Your dialler's link with {phone} where the number goes")],
          test="Run test checks that the link contains {phone}. Then press Call on a lead on the Call desk.",
          errors=[["'The link must contain {phone}'", "Type {phone} with curly brackets where the number goes."],
                  ["The dialler opens but does not dial", "Ask the dialler team if the number needs a 0 or +91 in front, and "
                   "put that before {phone}."]],
          safety="Nothing is ever dialled automatically; a person always presses Call.",
          attach=["hub:calls:click_to_call", "integration:telephony"],
          fields={"click_to_call_url": hint("A web address containing {phone}.", r"\{phone\}",
                                            "Put {phone} (with curly brackets) where the phone number goes.")},
          keywords="click to call dialler telephony"),
    guide("exotel", "Exotel (calls)", CALLS, "Indian cloud telephony: rings the representative, then connects the lead.",
          ["Sign up at exotel.com and finish KYC.", "Buy or note your ExoPhone (the number leads will see).",
           "Exotel dashboard > Settings > API Settings: copy the Account SID, API key and API token, and note your region "
           "(Mumbai accounts use api.in.exotel.com).", "In this app: Integrations > Calls > Exotel > Set up. Paste them with "
           "the representative's mobile number. Save."],
          cost="Paid plans. " + CHECK_PRICES, time="20 minutes (after KYC)",
          paste=[("Account SID / API key / API token", "Exotel > Settings > API Settings"), ("ExoPhone", "Your Exotel number"),
                 ("Agent phone", "The representative's mobile, e.g. +919812345678")],
          test=TEST_WIZARD + " Your phone rings first, then the number you typed.",
          errors=[ERR_KEY_REJECTED, ["Call does not connect", "Check the region matches your account and the agent phone "
                  "has country code."], ERR_NOT_SAVED], safety=KEY_SAFETY + " Only call people who agreed, during business hours.",
          attach=["hub:calls:exotel"], fields={"agent_phone": hint("With country code, e.g. +919812345678", r"^\+?\d{10,13}$",
                                                                  "Write the mobile number with country code, digits only.")},
          keywords="exotel calls telephony"),
    guide("knowlarity", "Knowlarity (calls)", CALLS, "Indian cloud telephony with click-to-call.",
          ["Ask your Knowlarity account manager to switch on the click-to-call API.",
           "Knowlarity dashboard > Settings > API: copy the SuperReceptionist API key and the x-api-key.",
           "In this app: Integrations > Calls > Knowlarity > Set up. Paste them with your Knowlarity number and the "
           "representative's mobile. Save."],
          cost="Paid plans. " + CHECK_PRICES, time="15 minutes",
          paste=[("SuperReceptionist API key / x-api-key", "Knowlarity > Settings > API"), ("Knowlarity number", "Your number"),
                 ("Agent phone", "The representative's mobile")],
          test=TEST_WIZARD, errors=[ERR_KEY_REJECTED, ERR_NOT_SAVED], safety=KEY_SAFETY, attach=["hub:calls:knowlarity"],
          keywords="knowlarity calls"),
    guide("twilio_voice", "Twilio Voice (calls)", CALLS, "Global calling service: rings the representative, then dials the lead.",
          ["Sign up at twilio.com and buy a voice number.", "Copy the Account SID and Auth token from the Console home page.",
           "Check Twilio's India calling rules first: some call types to Indian numbers are restricted.",
           "In this app: Integrations > Calls > Twilio Voice > Set up. Paste with the representative's mobile. Save."],
          cost="Trial credit; then pay per minute. " + CHECK_PRICES, time="15 minutes",
          paste=[("Account SID", "Starts with AC"), ("Auth token", "Console home"), ("Twilio number", "The number you bought"),
                 ("Agent phone", "The representative's mobile")],
          test=TEST_WIZARD, errors=[ERR_KEY_REJECTED, ERR_NOT_SAVED], safety=KEY_SAFETY, attach=["hub:calls:twilio_voice"],
          links=[["Twilio Console", "https://console.twilio.com"]],
          fields={"account_sid": hint("Starts with AC and is 34 characters.", r"^AC[0-9a-fA-F]{32}$",
                                      "A Twilio Account SID starts with AC followed by 32 letters and numbers.")},
          keywords="twilio voice calls"),
]
