# LinkedIn agent

Sends the LinkedIn connection requests and follow-up messages of the LinkedIn sequence for you, from **your own computer**.

## How it works
- `tools/linkedin_agent.mjs` runs on a computer where you can see Chrome. It opens a Chrome window with its own profile folder
  (`~/.mcn-linkedin-profile`). **You log in to LinkedIn in that window yourself**, including any code LinkedIn sends. Your LinkedIn password
  and cookies stay on that computer; the portal never receives them.
- It asks the portal what it may do (`GET /api/extension/agent`), takes today's due step from the queue, opens the profile, and:
  - step 1: clicks **Connect**, **Add a note**, pastes the note from the sequence, clicks **Send**;
  - later steps: clicks **Message** (only for people who accepted), pastes the message, clicks **Send**;
  - every few hours it looks at the profiles still waiting and records an acceptance when LinkedIn shows them as 1st degree.
  It then tells the portal (`sent`, `accepted`, `connect`/`message` events), which schedules the next step.
- The portal (LinkedIn outreach → LinkedIn agent) holds the switch, the working hours (India time, weekdays), the daily and weekly caps,
  the fixed gap between two actions and the record of what happened. These limits live in the portal, so restarting the agent cannot get round them.

## Safety stops
The agent stops itself, switches the portal switch off and notifies the owner when it sees a login wall, a checkpoint, a CAPTCHA /
security check, the weekly invitation limit, or an "account restricted" message. A person must switch it back on after sorting it out.

## What it does not do
No stealth plugins, no spoofed browser identity, no proxies, no random "human-like" delays: the gap is fixed. This keeps the volume low and
visible; it does **not** make automation allowed. **LinkedIn forbids automation and restricts accounts that use it, even at low volume.**
Use an account you can afford to have restricted.

## Run it
```
cd web && npm ci && npx playwright install chromium && cd ..        # once
node tools/linkedin_agent.mjs --portal https://your-portal --token mcnx_...
```
Get the token in the portal: LinkedIn outreach → Chrome assistant → Create pairing token. Options: `--dry` (look, click nothing), `--once` (one action
then exit), `--allow-no-note` (send a request without a note when LinkedIn will not allow one; off by default because a free account can add only a
few notes a month), `--profile <folder>`.

## Honest limits
- LinkedIn changes its pages without notice. The agent finds buttons by their visible names and labels (Connect, Add a note, Send, Message).
  The tests (`tests/test_linkedin_agent.py`) run the agent against a stand-in page with those names and check every step and stop. They do not prove
  LinkedIn's real pages still match. Run with `--dry` first on a real profile; if it cannot find a button, say which and the selector can be fixed.
- Free accounts get only a handful of personalised notes a month; Premium and Sales Navigator get more.
