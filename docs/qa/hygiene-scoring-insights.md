# QA: data hygiene, scoring, Today, dashboard and Insights

Branch `qa/hygiene-scoring-insights`, run 2026-11-03. Tests: `tests/qa/test_hygiene-scoring-insights.py` (500-lead
simulation, 20 tests) and `tests/qa/test_hygiene-scoring-insights_regressions.py` (one small fixture per bug, 10 tests).
Helpers are in `tests/qa/hsi_helpers.py`.

## The simulated database

`tests/qa/fixtures/hygiene-scoring-insights/build_db.py` builds the store from a fixed seed. The tests call `generate()`
directly, so no large JSON file is committed. Running the script writes `store_500.json` and `truth.json` for a look by
hand; both are git-ignored. All names, domains (`*.example`, `*.invalid`) and numbers (`+91 90000 1xxxx`) are made up.

- **500 leads in the two shapes a real store holds.** "Legacy" rows were saved by quick paste or an older version: ALL
  CAPS, `PVT. LTD.`, `M/S`, honorifics (`MR.`, `SMT.`), Bombay/Poona/Madras, country typed as `India`/`india`/`IND`.
  "Smart" rows were cleaned by the import rules.
- **Real history for every stage.** Each lead has the outbox rows, replies, inbox rows, consent and consent-ledger rows,
  WhatsApp rows and activation its stage implies. Stages run from new to advocate, plus not_now, unsubscribed and
  disqualified.
- **68 planted duplicate groups:**
  - 12 with the same email, across 2 or 3 sources and conflicting stages (engaged + unsubscribed, customer + disqualified, ...);
  - 8 Gmail aliases (dots, `+naukri`, `googlemail.com`);
  - 11 sharing one phone written as `+91 …`, `0…`, bare digits or `+91-…-…`, one of them behind a family number;
  - 8 pairs of a Google Maps company row and a person at the same domain (one on a `careers.` sub-domain);
  - 5 people with two addresses at the same domain (`first.last@` and `flast@`, and `LAST, FIRST`);
  - 12 fuzzy company names in the same city (`Acme Retail Pvt Ltd`/Mumbai and `ACME RETAIL`/Bombay, `Vardhman` and
    `Vardhaman`, `&` and `and`, ...);
  - 8 company-level pairs: two different people at one company domain;
  - 4 "hard" positives that the rules are not built to catch.
- **9 negative sets that must not be grouped:**
  - the same name in three cities;
  - four "Sai Enterprises" in four cities, plus one row with no city;
  - a family phone shared by two people at two firms;
  - a business-centre landline shared by two named people;
  - three company-only rows on one business-centre landline;
  - Balaji Traders vs Balaji Transport, and two similar pairs;
  - two firms called "Nova Tech" with different domains.
- **Sending problems and closed-lead tasks:**
  - 3 leads whose sends failed three times, one email stuck mid-send, one blocked email and a provider error;
  - 3 invalid stored emails and suppressed addresses;
  - an erasure request and a "yes, actually" review on unsubscribed leads;
  - a call task and a review on disqualified leads;
  - an unmatched reply.

## Duplicate detection: precision and recall against the planted truth

The numbers below are measured over lead pairs. A cluster of n leads counts as n·(n-1)/2 pairs.

| | before fixes | after fixes |
|---|---|---|
| Recall on the 68 planted groups (68 pairs) | 65/68 = **95.6 %** | 68/68 = **100 %** |
| Precision, counting company-level pairs as correct | 70/83 = **84.3 %** | 73/76 = **96.1 %** |
| Precision, strict (only the 68 groups count) | 65/83 = 78.3 % | 68/76 = 89.5 % |
| Company-level pairs found (two people at one domain) | 5/8 | 5/8 |
| Hard positives found | 0/4 | 0/4 |
| Negative pairs wrongly grouped | 13 (Sai chain 10, landline 3) | 3 (landline, known limitation) |
| Clusters shown | 60 (130 leads) | 62 (131 leads) |

Once every planted group is merged through `/api/hygiene/merge`, the only clusters left are the 3 landline company
rows and company-level pairs.

## Bugs found and fixed (each in its own commit, with a regression test)

1. **ALL CAPS `LAST, FIRST` names lost the first name** (`app/standardise.py`, medium).
   - Cause: `clean_person_name("NAIDU, FARAH")` returned `Naidu`. The trailing-credentials rule read `, FARAH` as a
     credential, like `, MBA`.
   - Effect: the person key changed, so the same person at the same domain was not grouped (2 planted groups missed).
     Imports also dropped first names.
   - Fix: credentials are stripped only when a full name is left before the comma. `RAVI KUMAR, MBA` still becomes
     `Ravi Kumar`.
2. **A shared family number hid the second person's duplicates** (`app/hygiene_report.py`, medium). Each phone bucket
   was compared only against its first lead. Dev Rao, saved first, blocked Anita Rao's two leads from being grouped.
   Every pair in a bucket is now compared, so the result no longer depends on the order the leads were saved in.
3. **One city-less row chained different firms** (`app/hygiene_report.py`, medium).
   - Cause: `SAI ENTERPRISES` with no city joined the Pune, Lucknow, Surat and Indore firms into one cluster.
   - Risk: "merge" would then fuse four businesses.
   - Fix: a city-less row joins a name only when that name is known in at most one city.
4. **India was detected only as the exact code `IN`** (`app/scoring.py`, medium).
   - Cause: `India`, `india`, `IND`, an Indian city with no country, or a +91 number got no India fit points.
   - Effect on the 500 leads: 152 leads were under-scored. Hot leads went from 85 to 102 after the fix (cold 256 → 241).
   - Fix: the country goes through `clean_country`. With no country, an Indian city/state or a +91 number counts, which
     is the same rule the campaign import uses.
5. **Insights** (`app/routes_insights.py`, medium):
   - Top leads included unsubscribed and disqualified leads. They are now left out; customers stay in the list.
   - `emails_sent` and `emailed_leads` counted `failed` and `provider_error` outbox rows. Practice (`dry_run`) rows still
     count, as an existing test requires.
   - `interested_rate` and `unsubscribe_rate` divided all interested or unsubscribed leads by emailed leads. With leads
     from the sheet, the rate could pass 100 % (2 sheet yeses and 1 email gave 200 %). Now only emailed leads are counted.
   - `tests/test_exports.py` expected the failed row to count, so it now expects 2 instead of 3.
6. **Dashboard reminders listed disqualified hot leads** (`app/main.py`, low).
7. **Erasure requests never showed on Today or the task board** (`app/routes_today.py`, `app/routes_activities.py`, high).
   - Cause: a "delete my data" reply always unsubscribes the lead, and both screens hid automatic tasks of unsubscribed
     leads.
   - Effect: the 24-hour DPDP erasure task was invisible.
   - Fix: erasure tasks are now kept on both screens. Today also keeps `review` tasks, as the board already did.
   - Also fixed: a leftover "Call them" task for a lead who later said no (disqualified) is no longer listed as a call.
8. **Merge left activities, transactional mail, notifications and consent-ledger rows on the merged-away id**
   (`app/routes_import.py`, high for DPDP).
   - Effect: these rows were missing from the kept lead's export, and they survived its erasure. That included call
     notes and the person's consent text.
   - Fix: they are now re-pointed together with outbox, inbox, tasks and WhatsApp.
   - Note: re-pointing changes `lead_id` on consent-ledger rows. Erasure already rewrites those rows, so this is not new.
   - `tests/test_hygiene.py` checked the exact `repointed` dict, so it now lists the new keys.

## Verified as working (no change needed)

- **Merge rules:**
  - unsubscribed beats everything, then disqualified (customer + disqualified gives disqualified);
  - otherwise the most advanced stage wins (call_done + customer gives customer, not_now + new gives not_now);
  - withdrawn consent stays withdrawn;
  - otherwise the kept lead's consent is used, or else the first other lead's;
  - every timeline event is kept (+1 merge event), `merged_ids` is recorded and no rows are left on the old ids;
  - bad requests (keep = merge, repeated ids, unknown id, empty list) change nothing.
- **Standardise:**
  - The preview changes nothing (213 of the 500 leads need changes; the list is capped at 200 and the count is not).
  - Apply matches the preview, never touches email, stage, consent, sequence or engagement, and adds a `standardised`
    event.
  - A second preview shows 0 changes, and `lead_ids` limits the run to those leads.
  - Duplicate recall is unchanged after standardising.
- **Scores:** always between 0 and 100. Each band matches its thresholds (cold below 40, warm 40–69, hot 70 and above)
  and each score equals the rounded sum of its signals. Boundaries were checked with realistic leads: 39 is cold, 42 is
  warm, 68 is warm and 70 is hot. Consent gives intent 10; opens, clicks and replies fill engagement.
- **Dashboard:** the stage counts add up to 500, the funnel follows `STAGES`, the hot count matches the bands, and there
  are at most 10 reminders, all hot and none closed.
- **Insights on the 500:**
  - emails sent 297 (10 failed rows left out), emailed leads 152, reply rate 64.5 %, interested rate 32.9 %, unsubscribe
    rate 7.9 %;
  - the by-source, by-sector and by-band counts each add up to 500, and `bmi_sheet:leads` is shown as `bmi_sheet`.
- **Today** (clock frozen at 2026-11-03 11:30 IST):
  - 5 sending problems come first: 3 failed sends, 1 stuck mid-send, 1 blocked;
  - counts are tasks 26, calls 22 and nudges 9 for the 9 trials;
  - overdue tasks are sorted first, and the list is capped at 20;
  - two days later more tasks are overdue (31 → 43) and the problems are unchanged;
  - logging a call removes that lead from the calls;
  - closed hot leads never get "start the sequence".

## Limitations and recommendations (not changed)

- **Company rows sharing a business-centre landline are grouped** (3 pairs above), because rows with no contact names
  always pass the name check. Recommendation: for two company-only rows, also require a similar company name before a
  phone match counts.
- **Two named people at one company domain are grouped only when the company names match.** When the second row has no
  company name (a Hunter-style row), they are not grouped (3 of 8), even though the import matcher would attach that
  person to the company. Recommendation: add a "same company domain, different person" reason that suggests adding the
  person as another contact rather than a full merge.
- **Hard positives are not caught:** Sri/Shri, two small spellings (91 %, just under 92 %), an extra non-generic word
  (`Packers` vs `Packers and Movers`) and a leading `Dr.`. Fuzzy matching only compares names whose first three letters
  are the same, in the same city. Recommendation: normalise Sri/Shri/Shree and a leading Dr./M/s before keying.
- **The import matcher has the same city-less weakness** (`lead_merge._company_match`). A city-less incoming row matches
  the first same-named firm in any city. That code belongs to the import area, so it is noted here and not changed.
- **Insights "by sector" shows lead counts only.** The PRD says "results by source and by sector". Recommendation: add
  replied, interested and customer counts per sector, as by-source already has.
- **Practice sends count as emails sent on Insights.** This is intended (practice shows a working funnel), but the page
  does not say the numbers are practice. Recommendation: label them in practice mode.

## Formats referenced

- Gmail ignores dots in the local part, and `+tag` addresses reach the same inbox. These are the aliases planted in
  group B. Source: https://support.google.com/mail/answer/7436150
- No external import formats were needed for this area. All rows use the store's own lead schema
  (`docs/PRD.md` §7 data model, `app/lead_model.py`).
