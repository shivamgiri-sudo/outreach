"""Guides: the original lead-data connectors (Apollo, Hunter, Firecrawl, Google Places, verifiers ...) and the AI writer."""
from .common import (CHECK_PRICES, ERR_CREDITS, ERR_KEY_REJECTED, ERR_MASKED, ERR_NETWORK, KEY_SAFETY, NO_COST, TEST_SETUP,
                     TEST_SOURCE, guide, hint)

CAT = "Lead data"


def both(cid):
    return [f"hub:lead_data:{cid}", f"integration:{cid}"]


GUIDES = [
    guide("apollo", "Apollo (work emails and phones)", CAT, "Finds the work email and phone of the decision maker at a company.",
          ["Manual way (no key): use Apollo's own site or Chrome extension, export your list as CSV and import it on the "
           "Leads page.", "Automatic way: log in at app.apollo.io and open Settings > Integrations > API (API keys).",
           "Press Create new key. Give it a name such as Outreach. If Apollo offers 'Set as master key', switch it on (the "
           "app's free usage check needs it). Copy the key.",
           "In this app: Integrations > Lead data > Apollo > Set up (or Setup > Connections > Apollo). Choose 'Connect "
           "directly (API)', paste the key and Save.", "Press Test: it checks the key without spending a credit."],
          cost="Free plan with limited credits; paid plans for more. Each email lookup uses credits. " + CHECK_PRICES,
          time="5 minutes", paste=[("Apollo API key", "From Settings > Integrations > API")],
          test=TEST_SETUP + " No credit is used by the test.",
          errors=[ERR_KEY_REJECTED, ["'This endpoint requires a master API key'", "Create a new key in Apollo with 'Set as "
                                     "master key' switched on."], ERR_CREDITS, ERR_MASKED],
          safety=KEY_SAFETY, attach=[*both("apollo"), "setup:apollo"],
          fields={"api_key": hint("A long mix of letters, numbers, dashes and underscores (about 22 characters).", r"^\S{16,}$",
                                  "That looks too short or has spaces. Copy the key again from Apollo > Settings > Integrations > API.")},
          keywords="apollo api key master enrich email phone"),
    guide("hunter", "Hunter.io (public work emails)", CAT, "Finds public work emails for a company's website.",
          ["Sign up for the free plan at hunter.io.", "Open your account menu > API (hunter.io/api-keys) and copy your API key.",
           "In this app: Integrations > Lead data > Hunter.io > Set up. Paste and Save.",
           "Press Run test (it may use one search), then compare it with other sources on the Data sources page."],
          cost="Free plan with a small monthly search allowance. " + CHECK_PRICES, time="5 minutes",
          paste=[("Hunter API key", "From hunter.io > API")], test=TEST_SOURCE,
          errors=[ERR_KEY_REJECTED, ERR_CREDITS, ERR_MASKED], safety=KEY_SAFETY, links=[["Hunter API keys", "https://hunter.io/api-keys"]],
          attach=both("hunter"), fields={"api_key": hint("40 letters and numbers (a-f, 0-9).", r"^[0-9a-fA-F]{40}$",
                                                         "A Hunter key is 40 characters made of 0-9 and a-f.")},
          keywords="hunter email finder domain search"),
    guide("firecrawl", "Firecrawl (reads modern websites)", CAT,
          "Reads company websites that need JavaScript, so more contact pages can be checked.",
          ["Cloud: sign up at firecrawl.dev, open the dashboard and copy your API key (starts with fc-).",
           "Or self-host it with Docker (for your tech person) and paste its address instead; leave the key empty.",
           "In this app: Integrations > Lead data > Firecrawl > Set up. Paste and Save. Press Run test (uses one credit on the "
           "cloud plan)."],
          cost="Cloud has free credits to start. Self-hosted: free. " + CHECK_PRICES, time="5 minutes",
          paste=[("Firecrawl API key", "Starts with fc-"), ("Firecrawl address", "Only when self-hosted")], test=TEST_SOURCE,
          errors=[ERR_KEY_REJECTED, ["'private address blocked' when self-hosted", "Ask your tech person to set "
                                     "OUTREACH_ALLOW_PRIVATE_HOSTS on the server, e.g. localhost:3002."], ERR_CREDITS],
          safety=KEY_SAFETY, attach=both("firecrawl"),
          fields={"api_key": hint("Starts with fc-", r"^fc-[A-Za-z0-9]{8,}$", "Firecrawl keys start with fc-."),
                  "base_url": hint("Only for self-hosting, e.g. http://localhost:3002", r"^https?://\S+$",
                                   "Start the address with https:// or http://")},
          keywords="firecrawl scrape website javascript"),
    guide("google_places", "Google Places (find businesses by type and city)", CAT,
          "Lets a campaign find businesses such as 'dentists in Pune' with name, website and office phone.",
          ["Open console.cloud.google.com and sign in. Create a project (top bar > project picker > New project).",
           "Switch on billing for the project (Billing in the menu). Google needs a card even for the free allowance.",
           "APIs & Services > Library > search 'Places API (New)' > Enable.",
           "APIs & Services > Credentials > Create credentials > API key. Copy it.",
           "Press 'Edit API key' > API restrictions > Restrict key > tick only Places API (New) > Save. This stops misuse.",
           "Optional but wise: Billing > Budgets & alerts > set a monthly budget so you get an email before spending much.",
           "In this app: Integrations > Lead data > Google Places > Set up. Paste and Save. Press Run test."],
          cost="Google gives a monthly free allowance, then charges per search. " + CHECK_PRICES, time="15 minutes",
          paste=[("Places API key", "Starts with AIza")], test=TEST_SOURCE,
          errors=[["'This API project is not authorized to use this API'", "Enable 'Places API (New)' in the same project as "
                   "the key, and make sure the key's restriction includes it."],
                  ["'Billing has not been enabled'", "Switch on billing for the project (step 2)."], ERR_KEY_REJECTED],
          safety=KEY_SAFETY + " Always restrict the key to the Places API and set a budget alert.",
          links=[["Google Cloud console: credentials", "https://console.cloud.google.com/apis/credentials"]],
          attach=both("google_places"),
          fields={"api_key": hint("Starts with AIza and is 39 characters.", r"^AIza[0-9A-Za-z_\-]{35}$",
                                  "Google API keys start with AIza and are 39 characters long.")},
          keywords="google places maps business search api key billing"),
    guide("contact_scraper", "Website contact scraper (free, on your computer)", CAT,
          "A free open-source tool you run yourself to collect public emails and phones from company websites.",
          ["Ask your tech person to install the open-source omkarcloud website-email-contact-scraper (see its GitHub page).",
           "Give it your company websites and use only the homepage or key-pages mode. Do not switch on proxies or bot-wall "
           "bypassing; skip sites that block it.", "Export the results as JSON.",
           "In this app open Data sources and upload the JSON file. Each value keeps the page it came from."],
          cost=NO_COST, time="30 minutes", test="Upload the file on Data sources: it says how many leads were matched.",
          errors=[["'No results with a domain in that file'", "Export as JSON (not CSV), and make sure each row has the website."]],
          safety="Only collect public business contact details, and respect sites that say no.",
          links=[["omkarcloud on GitHub", "https://github.com/omkarcloud"]], attach=both("contact_scraper"),
          keywords="scraper website emails json omkarcloud"),
    guide("sales_navigator", "LinkedIn Sales Navigator (manual)", CAT, "Search and save target accounts, then export by hand.",
          ["Build a lead list in Sales Navigator.", "Export it with the CSV export tool you use and import it on the Leads page.",
           "Send LinkedIn messages yourself; this app writes the text. Nothing logs in to LinkedIn for you."],
          cost="Paid LinkedIn subscription.", time="10 minutes", test="Import the CSV on the Leads page.",
          errors=[["Columns are not matched on import", "Rename columns to Name, Company, Email, Phone, LinkedIn before importing."]],
          safety="Never share your LinkedIn password with any tool.", attach=both("sales_navigator"),
          keywords="linkedin sales navigator export csv"),
    guide("email_verifier", "Email verifier (ZeroBounce or NeverBounce)", "Email verification",
          "Checks emails before sending so bounces do not damage your domain.",
          ["Sign up at zerobounce.net or neverbounce.com.",
           "ZeroBounce: API > API Keys > copy a key. NeverBounce: Apps > create a Custom Integration app > copy its API key.",
           "In this app: Integrations > Lead data > Email verifier > Set up. In 'Provider' type zerobounce or neverbounce, "
           "paste the key and Save.", "Or choose Manual: verify CSVs on their site and import the result."],
          cost="Both list a small free allowance, then pay per check. " + CHECK_PRICES, time="5 minutes",
          paste=[("Provider", "zerobounce or neverbounce"), ("API key", "From the provider")], test=TEST_SOURCE,
          errors=[["'Choose ZeroBounce and add its key'", "Type the provider name exactly: zerobounce or neverbounce."],
                  ERR_KEY_REJECTED, ERR_CREDITS],
          safety=KEY_SAFETY, attach=both("email_verifier"),
          fields={"provider": hint("Type zerobounce or neverbounce.", r"^\s*(zerobounce|neverbounce)\s*$",
                                   "Type exactly zerobounce or neverbounce (one word, small letters).")},
          related=["kickbox", "millionverifier"], keywords="zerobounce neverbounce verify email bounce"),
    guide("llm", "AI writer (Anthropic Claude API key)", "AI",
          "Writes personalised reply drafts and reads replies that the rules cannot place.",
          ["Go to console.anthropic.com and sign up (this is separate from a claude.ai chat subscription).",
           "Billing: add a payment method and buy a small amount of credit to start.",
           "Settings > Limits: set a monthly spend limit you are comfortable with, so costs can never run away.",
           "Settings > API Keys > Create Key. Name it Outreach and copy it (shown only once).",
           "In this app: Integrations > AI > AI writer > Set up (or Setup > Connections > AI writer). Provider: type "
           "anthropic. Paste the key and Save.", "Press Test: it sends a one-word request (costs a fraction of a rupee)."],
          cost="Pay per use; a draft usually costs very little. Set a monthly limit in the console. " + CHECK_PRICES,
          time="10 minutes", paste=[("Provider", "anthropic"), ("API key", "Starts with sk-ant-")], test=TEST_SETUP,
          errors=[["'Connect the AI writer first (provider anthropic ...)'", "Type anthropic in Provider. Other providers such "
                   "as OpenAI are not used for drafts yet."], ["'Your credit balance is too low'", "Add credit under Billing "
                   "in the Anthropic console."], ERR_KEY_REJECTED, ERR_NETWORK],
          safety=KEY_SAFETY + " Keep a monthly spend limit switched on.",
          links=[["Anthropic Console", "https://console.anthropic.com"]], attach=["hub:ai:llm", "integration:llm", "setup:ai_writer"],
          fields={"provider": hint("Type anthropic.", r"^\s*[Aa]nthropic\s*$", "Type anthropic: it is the only provider used right now.",
                                   [(r"^\s*([Oo]pen[Aa][Ii]|[Gg]pt|[Cc]hat[Gg][Pp][Tt])", "Only anthropic is used for drafts "
                                     "right now. An OpenAI key will be saved but not used.")]),
                  "api_key": hint("Starts with sk-ant-", r"^sk-ant-\S{20,}$", "Anthropic keys start with sk-ant-. Copy it again from the console.",
                                  [(r"^sk-(?!ant-)", "This looks like an OpenAI key (sk-...). The AI writer needs an "
                                    "Anthropic key, which starts with sk-ant-.")])},
          keywords="ai writer anthropic claude openai api key spend limit llm"),
]
