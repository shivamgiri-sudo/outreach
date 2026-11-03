# MCN LinkedIn Assistant (Chrome extension)

A small helper for the person who sends the LinkedIn messages of MCN Outreach. When you open someone's LinkedIn profile
it shows the message the app has already written for that person, lets you copy it or drop it into the open box, and
lets you tell the app, with one click, what you did.

## The rule this extension is built on

**LinkedIn forbids automation.** This extension is an assistant, not a robot: a human clicks every action on LinkedIn.

It deliberately does NOT:

- click any LinkedIn button, press Send or Connect, or submit any form
- scroll, page through, or scrape search results, lists or feeds, and it never crawls or opens other profiles
- simulate typing or mouse movement, use human-like or randomised delays, rotate anything, or hide itself
- send anything to LinkedIn or to the server by itself (every "sent / accepted / replied" is a click of yours in the panel)

It only does this:

1. Reads the one profile page you have open (name, headline, company, whether LinkedIn shows them as 1st degree, the
   profile address) to find the matching lead in your own app.
2. Shows the ready-made message in a side panel with a **Copy** button.
3. Puts the text into the open note or message box, once, only when you press **Insert message**. It will not
   overwrite text you already typed unless you say so. You read it, edit it and click LinkedIn's own Send button.
4. Quietly notices (read-only) that the message box closed, or that the person now shows as a 1st-degree connection,
   and then ASKS you to confirm ("I sent it", "They accepted"). It never reports on its own.

Why: LinkedIn restricts accounts that automate. Keeping a person in every step keeps your account safe and the
outreach honest. `tests/test_extension_rules.py` (and `scripts/check_extension_rules.py`) read this folder's source
and fail if a click, synthetic mouse or keyboard event, timer, randomness or scroll call ever appears.

## Install (no build step)

1. Open `chrome://extensions` in Chrome (version 114 or newer).
2. Switch **Developer mode** on (top right).
3. Click **Load unpacked** and choose this folder: `extensions/linkedin-assistant`.
4. Pin the extension (puzzle icon, then the pin) so its icon is always visible.

## Pair it with your app (once per computer)

1. In the web app open **LinkedIn outreach**, find the **Chrome assistant** card and click **Create pairing token**.
   Copy it at once: it is shown only one time. (Lost it? Revoke it there and make a new one.)
2. Click the extension icon, open **Settings and pairing**, type your app's address (for example
   `https://outreach.example.com`; `http://localhost:8000` works for a local copy) and paste the token.
3. Click **Save and connect**. Chrome asks permission to talk to that one address. Allow it.
4. The popup should say "Connected" and list today's queue.

## Daily workflow

1. Click the extension icon to see today's queue. Click **Open profile** on the first person (it opens a normal
   LinkedIn tab). Then **Open assistant panel**.
2. On the profile the panel shows who it is, which step this is, and the message.
3. On LinkedIn click **Connect** then **Add a note** (step 1), or **Message** (later steps). Then press **Insert
   message** in the panel. If no box is open the panel tells you to open one first.
4. Read the text, change anything you like, and click LinkedIn's **Send**.
5. Press **I sent it** in the panel. The app schedules the next step.
6. Later, when a request is accepted, open their profile and press **They accepted**. When someone writes back, press
   **They replied**, paste what they said (optional) and save: the sequence stops and the app handles the reply.

The panel also tells you plainly when there is nothing to do: not paired, profile is not a lead, the person is
unsubscribed or disqualified (never offered a message), still waiting to accept, already sent today, or not due yet.

## Privacy

- The extension talks only to the LinkedIn page you are on and to your own server (the address you typed).
- The pairing token is stored in `chrome.storage.local` in this browser profile. It is never logged and never sent
  anywhere except your server. On the server only a hash of it is kept.
- A pairing token can use only the few `/api/extension/...` routes (look up a lead, today's queue, record sent /
  accepted / replied). It cannot read your lead list, settings or users, and it cannot make more tokens. It expires
  after 90 days and can be revoked at any time in the web app.
- Only the profile address without its query string, the name and the company shown on the page are sent to your
  server for matching. Nothing else on the page is read.

## Permissions (and why)

| Permission | Why |
| --- | --- |
| `storage` | remember the server address and token |
| `activeTab`, `sidePanel` | show the panel next to the tab you are on |
| host `https://www.linkedin.com/*` | read the open profile / fill the box you asked for |
| optional host, your server only | requested when you press Save and connect; not granted at install |

There is no `<all_urls>`, no `webRequest`, no `scripting`. One content script is declared for
`https://www.linkedin.com/*` (LinkedIn is a single-page app, so a script limited to `/in/*` would not load when you
click from the feed to a profile). It does nothing until the panel asks, and only reads when you are on a `/in/` or
`/messaging/` page.

## Files

`manifest.json`, `background.js` (server calls), `content.js` (read-only page reader and the one-time insert),
`popup.html/js` (settings, queue), `sidepanel.html/js` (the panel), `ui.css`, `icons/`. Regenerate icons with
`python3 scripts/make_extension_icons.py` (needs Pillow, not an app dependency).

## Maintenance: LinkedIn changes its pages

LinkedIn changes its HTML often. If name, company or the box are not found, update the selectors at the top of
`content.js` (`readProfile`: `main h1`, `.text-body-medium`, `button[aria-label^='Current company']`,
`.distance-badge .dist-value`; `BOX_SELECTORS`: the note `textarea#custom-message` and the message box
`div.msg-form__contenteditable`). Matching also works from the profile address alone, so a broken name selector only
affects the name + company fallback.

## Publishing to the Chrome Web Store (later)

Zip the contents of this folder (not the folder itself) and upload it in the developer dashboard. Expect to justify
the optional host permission (your own server) and to state the single purpose: "show and insert a prepared outreach
message; the user sends it". Bump `version` in `manifest.json` for each upload. Do not add automation features: they
would breach LinkedIn's terms and the store policy.
