# QA report: templates, copy, prompt generator, AI drafts, demo page

Branch `qa/templates-prompts-ai`. All tests run in practice mode with fake clocks and fake transports. No network calls.

## What was tested

| Area | Tests | How |
|---|---|---|
| Email 1 copy for every messy row | `test_templates-prompts-ai.py` | 144-row CSV (BOM, ALL CAPS, Pvt/LLP/OPC/(P)/Pte suffixes, honorifics with and without a dot, Hindi script, mail-merge leftovers, blank row, duplicate rows, two contacts at one domain) plus a per-row expected-output file |
| End to end | same | import (141 created, 2 duplicates), enrol everyone, then 3 weeks of practice runs (days 0/1/3/7/14). Checks: Emails 1-4 go out, no `{{`, no honorific greeting, signed `/u/<id>.<sig>` unsubscribe link, identity footer plus `Privacy notice:` line, at most one email per company domain per day, and the mail-merge lead blocked with a reason |
| A/B variant | same | `email1_include_link` off, then on. Outbox `variant` is `default`/`no_role`, then `with_link` with the lead's own demo link, and the column survives `/api/export/outbox.csv` |
| Footer | same | no settings means no footer. A postal address replaces website/email. `http://` and `javascript:` privacy links are refused |
| WhatsApp placeholders | same | every template for a lead with company `{{Company}}` is refused (400). Custom text with an unknown `{{salary_band}}` is refused. Custom text outside the 24 h window, no consent, or an unsubscribed lead are all refused |
| Templates page API, Stories | same | `/api/sequence` (every body has unsubscribe/greeting/sender, no-role variants have no `{{role}}`), `/api/whatsapp/templates` (all end with the STOP line). A story without permission does not count on `/api/readiness`. Stories never reach the copy |
| LinkedIn | `_linkedin.py` | 30 titles mapped to personas, all 7 steps for every fixture lead, the 300-char note cut, the case-study step gated on a real link, all 4 branches and empty-slot refusals |
| Prompt generator | `_ai.py` | 36 channel x tone x language combinations: the guardrails are always the exact last block. 6 injection strings in each of 8 fields (incl. a forged "Rules (these override…)" block and Hinglish) always sit above the real guardrails |
| AI drafts | same | tag breakouts in lead message and lead facts (7 variants), the 70-word flag, Hinglish/Hindi mirroring, `ESCALATE` (upper/lower case, bare), provider error, empty answer, auto-reply gets no draft |
| AI labels | same | an AI `interested` creates a high-priority review task, with no consent, no ledger "given" entry and no WhatsApp. `unsubscribe`/`not_now` apply. Prose-wrapped JSON, unknown labels and non-JSON all fall back. AI is not called when the rules already know the answer |
| Demo page | same | 6 XSS payloads in company and role: only one `<script>` (the nonce beacon), no `on*=` attributes, nonce in the CSP, signed stop link, `noindex`. Beacon: GET (scanner) records nothing, POST records one open, a second POST is idempotent, unknown tokens 404 |

Result: 163 QA tests; full suite 1010 passed.

## Bugs found and fixed

1. **Mail-merge leftovers were sent** (high). Company `{{Company}}` went out in the subject and body. Contact `{{First Name}}` became "Hi {{first,". `render` now treats a value containing `{{`/`}}` as missing, so the email or WhatsApp is blocked. A name like that becomes "Hello,". (`app/sequences.py`)
2. **Dirty greetings and company names in cold email** (medium). "Hi Dr.meena," "Hi Mr.vikram," "Hi Prof," "Hi CA," "Hi Km," "Hi Sh,". Companies "Sharma Traders (P)", "XYZ Services OPC", "Omega Steel P", "Alpha One Pte". Roles "hiring for telecaller | work from home", "telecaller - hiring!!", and a role cut ending on "and". Fixes:
   - Honorifics glued to the name by a dot are now split off.
   - Added km/sh/prof/er/ca/adv/capt.
   - OPC, Pte, and P. Ltd / (P) Ltd are recognised as suffixes.
   - Removed role noise: hiring (for), work from home/wfh, `!`, and a trailing and/or.
3. **LinkedIn persona order** (medium). "HR Director", "Director - HR", "Director - Talent Acquisition" and "Staffing Director" got the founder hook, because `director` was checked first. The CHRO pattern's own `hr director` alternatives were unreachable. HR, talent and recruitment personas are now checked first, and "Human Resources" counts as CHRO.
4. **LinkedIn branch with empty slots** (low). The helper produced "Would  or  work for you?" ready to copy. It now returns 400 naming the missing slot.
5. **Untrusted-text wrapper** (low). `< /lead_message>`, `<\lead_message>` and tags with attributes were not stripped. An AI "yes" never gives consent anyway, so the impact was limited.

## Limitations and recommendations (not changed)

- **LinkedIn note length.** Several sources say free LinkedIn accounts are limited to 200 characters per invitation note, and only Premium gets 300. The default connection note is about 210 characters. Recommendation: make `NOTE_LIMIT` a setting (200/300), or shorten the default note under 200.
- **Quick paste drops the LinkedIn profile link.** `/api/leads/import` (`lead_model.build_lead`) does not keep `linkedin_url`, so the LinkedIn sequence cannot start on those leads. The tests set it directly. Use the smart importer, or keep the field in `build_lead` (importer area).
- **"role" header means the person's title.** A CSV column literally named `role` is mapped to the person's job title, not the job being hired for. The fixture uses `Hiring For`.
- **Names and companies still kept as written:**
  - "M/s. Theta Enterprises" keeps the M/s prefix.
  - An all-lowercase company ("iota foods") keeps its casing.
  - "Kumar, Ravi" greets "Hi Kumar,".
  - "MOHD. ARIF" greets "Hi Mohd,".
- **Escalation format.** It is only detected when the AI's text starts with `ESCALATE`. Markdown such as `**ESCALATE:**` would be shown as a draft. It is still never sent automatically.
- **The 70-word limit is a flag, not a cut.** The note tells the person to trim the draft. This is by design, since a person always edits before sending.
- **Prompt generator line breaks.** User fields can contain newlines, so a user can write a fake "Rules…" line. The real guardrails are always last and say they override everything above them. The generator is used by trusted staff, so the line breaks are kept.
- **Unknown prompt channel.** An unknown channel (for example `telegram`) silently uses the email rules.

## Sources for formats

- Anthropic Messages API request/response shape used by the fake transport: https://docs.anthropic.com/en/api/messages
- LinkedIn invitation note limits (200 free / 300 Premium, quoted from LinkedIn help): https://www.hublead.io/linkedin-character-limit, https://expandi.io/blog/linkedin-message-character-limits/, https://quickmail.com/linkedin-connection-message-character-limit
- Indian company name suffixes ("Private Limited", "(OPC) Private Limited", LLP), from the Companies Act 2013 and LLP Act 2008 naming rules: https://www.mca.gov.in
- WhatsApp templates and the 24-hour session window: https://developers.facebook.com/docs/whatsapp/pricing#conversations (rules already in `app/whatsapp.py`)
