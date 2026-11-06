"""Guides: setting up the app itself (web address, login, keys, team, workspaces, backup, LinkedIn agent, Chrome assistant)."""
from .common import NO_COST, guide, hint

CAT = "App & team"
SERVER = ("This is set on the server, not in the app. If someone else hosts the app for you, send them this guide. On a "
          "server installed with deploy/setup_server.sh the settings file is /etc/outreach/outreach.env (needs sudo). "
          "After changing it run: sudo systemctl restart outreach")

GUIDES = [
    guide("public_url", "Your public web address", CAT,
          "The address of this app on the internet. Unsubscribe links, booking links and webhooks use it.",
          ["Look at the address bar of your browser while using this app. If it starts with https:// and is not localhost "
           "or 127.0.0.1, that is your public address (for example https://outreach.yourbrand.in).",
           "Still on a laptop? The app must run on a server reachable from the internet before real sending (see "
           "docs/SELF_HOSTING.md, or ask your tech person).",
           "Own domain: at your DNS host add an A record, e.g. Name outreach, pointing to the server's IP address. A free "
           "alternative is an sslip.io name made from the IP (1-2-3-4.sslip.io for 1.2.3.4).",
           "In this app: Setup > Connections > The basics > 'Your public web address'. Paste it with https:// and without "
           "a slash at the end. Press Save basics."],
          cost="Free with an sslip.io name; your own domain costs a yearly fee.", time="5 minutes (if the server is ready)",
          paste=[("Your public web address", "e.g. https://outreach.yourbrand.in")],
          test="The Go-live checklist shows a tick next to 'Set your public web address'. Open an unsubscribe link from a test "
               "email on your phone (using mobile data, not office Wi-Fi).",
          errors=[["The checklist still says to do", "It must start with https:// and must not be localhost, 127.0.0.1 or a "
                   "private address such as 192.168..."], ["Links in emails do not open on my phone", "The server is not "
                   "reachable from outside. Ask your tech person to open ports 80 and 443."]],
          safety="This address is public by design; it holds no secret.", attach=["setup:public_url", "app:public_url"],
          fields={"public_base_url": hint("Starts with https://, no slash at the end.", r"^https://(?!localhost|127\.|0\.0\.0\.0)[^\s/]+$",
                                          "Use the full https:// address of this app on the internet, without a slash at the "
                                          "end. localhost only works on this computer.")},
          keywords="public url base url domain https address sslip"),
    guide("admin_password", "Turn on login (admin password)", CAT, "Makes everyone sign in before they can see your leads.",
          ["Without a password the app only answers on the computer it runs on. Before sharing it, set an admin password.",
           "Pick a long password (at least 12 characters, a short sentence works well). Write it in a password manager.",
           "Set OUTREACH_ADMIN_PASSWORD=your-password in the server's settings. " + SERVER,
           "Sign in with that password (leave Email empty): you are the owner. Then add each team member's own login on "
           "Setup > Team & workspaces."],
          cost=NO_COST, time="5 minutes for whoever runs the server", paste=[("Server setting OUTREACH_ADMIN_PASSWORD", "Your password")],
          test="Open the app in a private browser window: it should ask you to sign in.",
          errors=[["The login screen does not appear", "The server was not restarted after the change, or the setting has a typo."],
                  ["'Too many wrong passwords'", "Wait one minute and try again."]],
          safety="Never share the admin password. Give each person their own login instead, so you can remove one person "
                 "without changing everyone's password.", attach=["setup:login"], keywords="login password admin sign in"),
    guide("secret_key", "Encrypt saved keys (secret key)", CAT, "Locks the API keys and passwords you save in the app.",
          ["Ask your tech person to make a long random value, e.g. with: python -c \"import secrets; print(secrets.token_urlsafe(32))\"",
           "Set OUTREACH_SECRET_KEY=that-value in the server's settings. " + SERVER,
           "Keep a copy of the value somewhere safe (a password manager). Without it the saved keys cannot be read."],
          cost=NO_COST, time="5 minutes for whoever runs the server", paste=[("Server setting OUTREACH_SECRET_KEY", "The random value")],
          test="The Go-live checklist shows a tick next to 'Encrypt saved keys'.",
          errors=[["Checklist says the key is 'missing or wrong'", "Keys were saved with a different secret key. Put the old "
                   "value back, or enter the connections again."]],
          safety="Losing this value means re-entering every connection. Changing it without care makes saved keys unreadable.",
          attach=["setup:secret_key"], keywords="encrypt secret key outreach_secret_key"),
    guide("product_webhook", "Secret for product activity events", CAT,
          "Lets your own product tell this app when a lead starts using it (for example 'role published').",
          ["In Setup > Connections > The basics press Generate next to 'Secret for product activity events', then Save basics.",
           "Give your tech team the secret and the address shown under the box. Their system sends a POST request with the "
           "header X-Webhook-Secret and the lead's email and event name.", "Skip this if you have no product of your own."],
          cost=NO_COST, time="5 minutes (plus your tech team's time)", paste=[("Secret for product activity events", "Press Generate")],
          test="Ask your tech team to send one test event; the lead's timeline shows it.",
          errors=[["Events are refused with 401", "The header must be X-Webhook-Secret with the exact secret."]],
          safety="Share the secret only with your tech team. Generate a new one if it leaks (then update their system).",
          attach=["setup:webhook"], fields={"webhook_secret": hint("Press Generate for a safe random value.", r"^\S{16,}$",
                                                                  "Use at least 16 characters with no spaces; press Generate.")},
          keywords="webhook secret product activity event"),
    guide("team_logins", "Team logins and roles", CAT, "Give each person their own sign-in with the right access.",
          ["Setup > Team & workspaces > Team logins. Type their email, name and a temporary password (10+ characters).",
           "Choose a role: Owner (everything), Caller (leads, calls, meetings, tasks; no settings or exports) or Viewer (read only).",
           "Press Add login and give them the password in person or by phone, not in a group chat. Ask them to keep it private.",
           "Someone leaving? Press Remove next to their login. Forgot a password? Press Reset password."],
          cost=NO_COST, time="2 minutes per person", paste=[("Email / Name / Password", "The person's details")],
          test="The person signs in with their email and password and sees only what their role allows.",
          errors=[["'Password too short'", "Use at least 10 characters."],
                  ["They cannot open Integrations", "Callers and viewers cannot change settings. Make them an owner only if "
                   "they really need it."]],
          safety="Never share one login between people. Remove logins the same day someone leaves.",
          attach=["app:team_logins"], keywords="team users logins roles caller viewer owner password"),
    guide("workspaces", "Workspaces (one per client)", CAT, "Keeps each client's leads, lists and numbers apart.",
          ["Setup > Team & workspaces > Workspaces. Type the client's name and press Add workspace.",
           "Pick the workspace in the left menu before importing: leads go into the chosen workspace.",
           "To limit a team member to some clients, tick those workspaces next to their login (none ticked = all)."],
          cost=NO_COST, time="1 minute", test="Switch workspace in the menu: the Leads page shows only that client's leads.",
          errors=[["'It must be empty' when removing", "Move or delete its leads first (Leads page > select > Move to workspace)."]],
          safety="A contact can be in only one workspace, so two clients never contact the same person twice from here.",
          attach=["app:workspaces"], keywords="workspace client agency separate"),
    guide("backup", "Backup and restore", CAT, "Download a copy of everything, or move the app to a new server.",
          ["Setup > Backup & restore > Download backup. Keep the file somewhere safe (it holds your leads' contact details).",
           "The normal backup leaves out connection keys. After a restore you enter them again on the Integrations page.",
           "'Download backup with keys' includes every key in plain text: only use it to move servers, never email it, and "
           "delete it after the move.", "To restore: choose the file, press 'Check this file' to see what would change, then "
           "'Replace everything with this backup'. Sending stays off until you switch it on again."],
          cost=NO_COST, time="2 minutes", test="Press 'Check this file' on a downloaded backup: it lists what it contains.",
          errors=[["Restore says the file is not valid", "Use the .json.gz file exactly as downloaded; do not unzip or rename it."],
                  ["Connections stopped working after a restore", "Expected for a backup without keys: re-enter them on "
                   "the Integrations page."]],
          safety="Backups contain personal data. Store them like a password; delete old ones you no longer need.",
          attach=["app:backup"], keywords="backup restore download move server"),
    guide("linkedin_agent", "LinkedIn agent (runs on your computer)", CAT,
          "Sends your LinkedIn connection requests and messages from your own computer, slowly and within limits.",
          ["Read first: LinkedIn does not allow automation and can restrict accounts that use it. Keep numbers low and use an "
           "account you can live without.", "No installation wanted? Use 'Cloud agent (no installation)' at the top of the LinkedIn "
           "outreach page instead: add an account, log it in there, and the server does the work. The steps below are for "
           "running it on your own computer.", "On the LinkedIn outreach page open 'Chrome assistant' and press 'Create pairing "
           "token'. Copy the token (starts with mcnx_); it is shown only once.",
           "On the computer that will do the work, install Node.js (version 18 or newer) from nodejs.org.",
           "Get the project folder onto that computer (ask whoever installed the app). Open a terminal in that folder and run "
           "once: cd web && npm ci && npx playwright install chromium && cd ..",
           "On the LinkedIn outreach page open 'Advanced: run on your own computer' and press 'Copy the command'. Paste it into the "
           "terminal, replace PASTE_THE_TOKEN with your token and press Enter. Add --dry at the end first to try without "
           "sending anything.", "A Chrome window opens. Log in to LinkedIn in that window yourself. Leave the window and the "
           "terminal open while you want it to work.", "On the LinkedIn outreach page press 'Switch on'. The status shows Running."],
          cost=NO_COST, time="20 minutes once", paste=[("In the terminal: --token", "Your pairing token (mcnx_...)")],
          test="The LinkedIn outreach page shows 'Running' and the agent's recent actions under 'What it did lately'.",
          errors=[["'node' is not recognised", "Install Node.js from nodejs.org, then close and reopen the terminal."],
                  ["Status says 'the agent program is not running'", "Start the command again and keep the terminal open; the "
                   "computer must not sleep."], ["'Login required' / 401", "The token was revoked or mistyped. Make a new one."],
                  ["The agent paused by itself", "LinkedIn showed a security check or a limit. Solve it in the Chrome window, wait "
                   "a day, then press Resume."]],
          safety="Your LinkedIn password never reaches this app: you log in inside the Chrome window yourself. Revoke the "
                 "pairing token on the LinkedIn outreach page if the computer is lost.",
          links=[["Node.js download", "https://nodejs.org"]], attach=["app:linkedin_agent"],
          fields={"token": hint("Starts with mcnx_", r"^mcnx_\S+$", "Pairing tokens start with mcnx_.")},
          keywords="linkedin agent pairing token node playwright computer"),
    guide("chrome_extension", "Chrome assistant (LinkedIn helper extension)", CAT,
          "Shows the ready message next to the LinkedIn profile you open. You still press Send yourself.",
          ["Get the folder extensions/linkedin-assistant from the project (ask whoever installed the app for a copy).",
           "In Chrome open chrome://extensions, switch on Developer mode (top right), press 'Load unpacked' and choose that folder.",
           "Pin it: puzzle-piece icon > pin next to MCN LinkedIn Assistant.",
           "In this app open LinkedIn outreach > Chrome assistant, type a name for this computer and press 'Create pairing "
           "token'. Copy it at once (shown only once).", "Click the extension icon > Settings and pairing. Type this app's "
           "address (your public web address) and paste the token. Press 'Save and connect' and allow Chrome's question."],
          cost=NO_COST, time="10 minutes", paste=[("Extension: app address", "Your public web address"),
                                                  ("Extension: pairing token", "Starts with mcnx_")],
          test="The extension popup says Connected and lists today's queue.",
          errors=[["Popup says 'not paired' or 401", "The token was revoked or copied wrongly. Make a new one."],
                  ["Chrome says the extension is from an unknown source", "Expected for 'Load unpacked'; keep Developer mode on."],
                  ["No message for a profile", "That person is not a lead in this app, or has no LinkedIn step due."]],
          safety="The extension never clicks or sends on LinkedIn for you. Revoke a computer's token on the Chrome assistant "
                 "card if the computer is lost or someone leaves.", attach=["app:chrome_extension"],
          keywords="chrome extension linkedin assistant pairing token"),
    guide("load_leads", "Load your leads", CAT, "Bring your list of people and companies into the app.",
          ["Open the Leads page and press Import.", "Choose a CSV or Excel file (from Google Sheets: File > Download > CSV).",
           "Check the column matching (Name, Email, Phone, Company ...) and confirm.", "Duplicates are merged automatically."],
          cost=NO_COST, time="5 minutes", test="The Leads page shows your people.",
          errors=[["Columns are not matched", "Rename the column headings to simple words like Name, Email, Phone, Company."]],
          safety="Only import people you are allowed to contact.", attach=["setup:leads"], keywords="import csv excel leads"),
    guide("go_live", "Switch on real sending (the last step)", CAT, "Moves from practice mode to real emails.",
          ["Finish the required checklist items and send yourself a test email first.",
           "Open Outbox & sending and switch on 'Send for real'. WhatsApp and SMS have their own switch on their Integrations tab.",
           "Watch the first day's replies and bounces closely; switch it off at any time."],
          cost=NO_COST, time="1 minute", test="The Outbox shows emails as sent, not as practice.",
          errors=[["Nothing is being sent", "Sending only runs in business hours, and automatic running must be on for the server."]],
          safety="Never run two copies of the app with the same data and sending on: people would get every email twice.",
          attach=["setup:live_sending"], keywords="send for real live sending practice"),
    guide("bmi_limits", "Free limits in your product", CAT, "Confirms your product itself limits what free users can do.",
          ["This item is about your own product, not this app. Check with your product team that free-trial limits (users, "
           "days, items ...) are set inside the product.", "Then tick the box in Setup > Connections > The basics and Save."],
          cost=NO_COST, time="2 minutes", test="The checklist shows it as done.", errors=[],
          safety="Without limits, free use of your product has no ceiling.", attach=["setup:bmi_limits"], keywords="free limits trial"),
    guide("brand", "Add your business name, logo and what you sell", CAT,
          "Puts your own name, logo and offer on emails, the app header, the demo page and message templates.",
          ["Open Setup and choose the 'Brand & offer' tab (only owners see it).",
           "Type your business name and product name, and the sign-off you want under every email (for example 'Regards, Team Acme').",
           "Upload your logo: a PNG or JPG under 1 MB works best in emails (SVG logos show in the app only).",
           "Pick your main colour. The page warns you if white text on it would be hard to read.",
           "Under 'What you sell', write who it is for, three short benefits and the next step you want (for example 'Book a 15-minute call').",
           "Press Save. The preview on the right shows how an email will look."],
          cost=NO_COST, time="10 minutes",
          test="The checklist shows it as done, and the email preview on the Brand & offer tab shows your logo and name.",
          errors=[["The logo is refused", "Use a PNG or JPG under 1 MB. SVG files with scripts or links are refused for safety."],
                  ["The colour cannot be saved", "White text would be unreadable on it. Choose a darker colour."],
                  ["Emails still show the old name", "Press Save, then open a new preview. Emails already sent keep the old text."]],
          safety="Only use a logo and claims you own and can back up. Never promise results you cannot show.",
          attach=["setup:brand"], keywords="brand logo company name offer white label sign off colours"),
    guide("success_story", "Add a success story", CAT, "A real result from a customer, used in messages after a lead says yes.",
          ["Open Success stories and press Add.", "Write the customer, the result in numbers, and a short quote.",
           "Tick 'permission' only if the customer agreed to be named."],
          cost=NO_COST, time="5 minutes", test="The checklist shows it as done.", errors=[],
          safety="Never invent results or use a customer's name without permission.", attach=["setup:story"],
          keywords="success story case study testimonial"),
]
