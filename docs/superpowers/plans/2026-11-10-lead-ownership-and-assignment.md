# Lead ownership, assignment and list assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One owner per lead, list assignment, assignment rules + round-robin, first-touch clock; a member sends only to leads they own; workspaces fully isolated.

**Architecture:** One access module (`app/lead_access.py`) answers `can_see` / `can_send`; every send path and the Leads/Lists read paths call it. Everything is behind a per-workspace switch `ownership_mode` (default **off**, so nothing changes until an admin turns it on after the migration report). Lists get an assignee. Rules/teams/clock are separate small modules.

**Tech Stack:** FastAPI, JSON/MySQL store (`app/store.py`), React 18/Vite, pytest, Playwright e2e (`web/e2e/qa_*.mjs`), agent sim (`tests/agent_sim`).

Spec: `docs/superpowers/specs/2026-11-10-lead-ownership-and-assignment-design.md`. Facts every builder must know:
- `lead["owner"]` is a **display name or email string** (not an id). A user matches an owner when `owner.lower()` is in `{user.name.lower(), user.email.lower()}` (see `lead_ops.owner_choices`).
- Roles: `owner`, `admin`, `caller`, `viewer`. Workspace of a row: `workspaces.ws_of(row)`; a user's workspaces: `workspaces.allowed_of(user)` (None = all). Workspace strictness must never loosen.
- Existing send paths to guard: `app/lists.py start_email/start_linkedin`, `app/routes_outreach.py enroll_lead`, `app/routes_linkedin.py` start/sent/find, `app/routes_extension.py` queue/match/claim/sent, `app/linkedin_accounts.py auto_start_for`, `app/linkedin_seq.py auto_start`, `app/lead_ops.py run_bulk`, flows/campaigns (`app/flow_actions.py`, `app/campaigns.py`), MCP/API tokens.
- Run tests with `.venv/bin/python -m pytest tests -q -p no:xdist --ignore=tests/qa -k "<subset>"`; full suite ~25 min and is run only by the lead. Sim: `AGENT_SIM_PORTS=88x1,88x7,88x6 .venv/bin/python tests/agent_sim/run_sim.py`. Build: `cd web && npx vite build`.
- Files < 500 lines. Commit trailer: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Never push or deploy.

## File map
- Create `app/lead_access.py` — `ownership_on(store, ws)`, `is_admin(user, ws)`, `owns(user, lead)`, `can_see`, `can_send`, `refuse_send(store, user, lead)`; filters `visible_leads(store, user, leads)`.
- Create `app/list_assign.py` — assign/unassign/split/share a list; reassign with running sequences; `pool(store, ws)`.
- Create `app/ownership_migration.py` — one-time list assignment + mismatch report + `fix_owners`.
- Create `app/assignment.py` — teams, ordered rules, round-robin pointer, `assign_new(store, lead, uploader=None)`.
- Create `app/first_touch.py` — clock, touched detection, alerts, reassign tick (called from `app/autorun.py reminder_tick`).
- Create `app/routes_assignment.py` — teams/rules/settings/migration/list-assign endpoints.
- Modify `app/lists.py`, `app/routes_lists.py`, `app/lead_import.py`, `app/routes_import.py`, `app/inbound_leads.py`, `app/sheet_import.py`, `app/find_leads.py`, `app/main.py` (router), `app/routes_auth.py` allowlists.
- Web: `web/src/pages/Lists.jsx`, `Leads.jsx`, `LeadDrawer.jsx`, `LeadBulkBar.jsx`, new `ListAssign.jsx`, `setup/Assignment.jsx`, `Dashboard.jsx`, `web/src/api.js`.

---
## Phase 1 — access module, list assignee, enforcement, migration

### Task 1: `lead_access.py`
**Files:** Create `app/lead_access.py`; Test `tests/test_lead_access.py`
- [ ] **Step 1: failing test**
```python
from app import lead_access as la

def lead(owner="", ws="main"): return {"id": "l1", "owner": owner, "workspace": ws}
def user(role="caller", name="Priya", email="p@x.in", ws=None): return {"role": role, "name": name, "email": email, "workspaces": ws}

def test_owner_name_or_email_matches(monkeypatch):
    u = user()
    assert la.owns(u, lead("priya")) and la.owns(u, lead("P@X.IN")) and not la.owns(u, lead("Ravi")) and not la.owns(u, lead(""))

def test_admin_sees_all_in_workspace_not_others(store_with_mode_on):
    s = store_with_mode_on
    adm = user("admin", "Adm", "a@x.in", ["main"])
    assert la.can_send(s, adm, lead("Ravi", "main")) and not la.can_send(s, adm, lead("Ravi", "w2"))

def test_mode_off_changes_nothing(store_with_mode_off):
    assert la.can_send(store_with_mode_off, user(), lead("Ravi"))      # old behaviour
```
- [ ] **Step 2:** run, expect FAIL (module missing).
- [ ] **Step 3: implement**
```python
"""Who may see / send to a lead. One owner per lead; admins of the lead's workspace see all; other workspaces never.
Everything is off (old behaviour) until the workspace switch ownership_mode is on."""
from . import workspaces

KEY = "ownership_mode"          # settings[KEY] = {workspace_id: True}

def ownership_on(store, ws: str) -> bool:
    return bool((store.read("settings").get(KEY) or {}).get(ws or workspaces.MAIN))

def _names(user: dict) -> set:
    return {str(user.get(k) or "").strip().lower() for k in ("name", "email")} - {""}

def owns(user: dict, lead: dict) -> bool:
    return bool(lead.get("owner")) and str(lead["owner"]).strip().lower() in _names(user or {})

def in_workspace(user: dict, lead: dict) -> bool:
    allowed = workspaces.allowed_of(user)
    return allowed is None or workspaces.ws_of(lead) in allowed

def is_admin(user: dict, lead_or_ws) -> bool:
    ws = lead_or_ws if isinstance(lead_or_ws, str) else workspaces.ws_of(lead_or_ws)
    allowed = workspaces.allowed_of(user)
    return (user or {}).get("role") in ("owner", "admin") and (allowed is None or ws in allowed)

def can_send(store, user: dict, lead: dict) -> bool:
    if not in_workspace(user, lead):
        return False
    if not ownership_on(store, workspaces.ws_of(lead)) or user is None:
        return True
    return is_admin(user, lead) or owns(user, lead)

def can_see(store, user: dict, lead: dict) -> bool:
    if not in_workspace(user, lead):
        return False
    if not ownership_on(store, workspaces.ws_of(lead)) or user is None or (user or {}).get("role") == "viewer":
        return True
    return is_admin(user, lead) or owns(user, lead)

def refusal(store, user, lead) -> str:
    """'' when allowed, else the message for the 409."""
    return "" if can_send(store, user, lead) else f"This lead belongs to {lead.get('owner') or 'nobody yet'}. Ask an admin to assign it to you."

def visible_leads(store, user, leads) -> list:
    return [l for l in leads if can_see(store, user, l)]
```
- [ ] **Step 4:** run tests, PASS. **Step 5:** commit `feat(ownership): access module`.

### Task 2: list assignee + enforcement
**Files:** Create `app/list_assign.py`; Modify `app/lists.py`, `app/routes_lists.py`, `app/routes_outreach.py`, `app/routes_linkedin.py`, `app/routes_extension.py`, `app/extension_api.py`, `app/linkedin_accounts.py`, `app/linkedin_seq.py`, `app/lead_ops.py`, `app/flow_actions.py`, `app/campaigns.py`; Test `tests/test_list_assign.py`, `tests/test_ownership_enforcement.py`
- [ ] **Step 1:** tests: member (caller) with mode on cannot `POST /api/lists/{id}/start-email`, `/start-linkedin`, `/api/leads/{id}/linkedin/start`, enroll, bulk enroll, extension claim/sent on a lead owned by someone else → 409/refused with the `la.refusal` text; can on own; admin can on all in own workspace and on none of another workspace; mode off → unchanged. List endpoints: caller sees only lists with `assigned_to` == them (or `shared_with`), admin sees all of the workspace.
- [ ] **Step 2:** `lists.create(..., assigned_to=)`; new fields `assigned_to` (user name string, `""` = admin pool), `shared_with: []`, `assigned_at`, `assigned_by`. `list_assign.assign(store, list_id, to, by, take_over=False)` sets `lead.owner` for members via `lead_ops.set_owner(... why="list <name>")`, skipping leads owned by another member unless `take_over` (returns `{moved, skipped:[{lead_id, owner}]}`); `unassign`; `split(store, list_id, members, mode="even"|"count", counts=None)` returns new lists; `share(list_id, users)`; reassignment never restarts or double-sends sequences (do not touch `linkedin_seq` / `sequence`).
- [ ] **Step 3:** call `lead_access.refusal` at every send path listed in the file map; extension paths use the account owner's user (token user); LinkedIn auto-start and daily auto-start pick only leads the token's user can send to. Flows/campaigns: admin only on others' leads, timeline text "sent by system for <owner>".
- [ ] **Step 4:** routes in `app/routes_assignment.py`: `PUT /api/lists/{id}/assign {to, take_over}`, `POST /api/lists/{id}/split`, `PUT /api/lists/{id}/share`, `GET /api/lists` filtered by role, `PUT /api/ownership-mode {on}` (owner/admin of that workspace only), allowlists in `routes_auth.py`.
- [ ] **Step 5:** run `-k "list or lead or linkedin or extension or outreach or flow or campaign or bulk or workspace or ownership"`; commit.

### Task 3: migration
**Files:** Create `app/ownership_migration.py`; Test `tests/test_ownership_migration.py`
- [ ] Each existing list: `assigned_to = created_by` if that name is an active login allowed in the list's workspace, else `""`. Report `{lists_assigned, lists_pool, mismatches:[{lead_id, owner, list_assignee}], unowned}` at `GET /api/ownership/report`; `POST /api/ownership/fix-owners` sets lead owner = list assignee only for listed leads (admin confirm); nothing silent; turning `ownership_mode` on is refused while `unowned` leads exist unless `acknowledge=true`. Tests for each branch. Commit.

---
## Phase 2 — upload ownership + conflicts report (parallel with Phase 3)

### Task 4: uploads own the list
**Files:** Modify `app/lead_import.py`, `app/routes_import.py`, `app/inbound_leads.py`, `app/sheet_import.py`, `app/find_leads.py`, `app/lists.py`; Test `tests/test_upload_ownership.py`
- [ ] Every human import (CSV, Excel, quick paste, Find leads, Apollo) by a **member** creates a list named after the file, `assigned_to` = uploader, new leads `owner` = uploader (`why="uploaded by"`). Admin upload: optional `assign_to` (default pool); `split_between=[names]` + `mode`. Existing leads are never re-owned. Rows whose existing lead is owned by another member: skipped and recorded in `import_record["conflicts"]=[{row, lead_id, owner}]`; owned by uploader or unowned: merged and added to the list. Do-Not-Contact/unsubscribed: kept, flagged not sendable. Per-member daily upload cap + max list count (settings `upload_caps`). Race: two uploads of one person → `store.update_lead` compare-and-set; loser reported as conflict. Connectors with no human call `assignment.assign_new` (Phase 3) and group each batch into a list per owner.
- [ ] Endpoint `GET /api/imports/{id}/conflicts` (admin: all; member: own). Tests: member upload ownership, owned-by-other skipped and reported, re-upload merges, DNC kept not sendable, caps, admin split even/count. Commit.

## Phase 3 — teams, rules, round-robin (parallel with Phase 2)

### Task 5: `assignment.py`
**Files:** Create `app/assignment.py`; Modify `app/routes_assignment.py`; Test `tests/test_assignment_rules.py`
- [ ] Data in `settings["assignment"][ws] = {teams:[{id,name,members:[names],default,daily_cap}], rules:[{id,when:{source,city,branch,campaign,tag},team}], pointers:{team_id:int}, on:bool}`. `assign_new(store, lead, uploader=None)`: if `lead.owner` → keep; human uploader (member) → uploader; else first matching rule → team, else default team, else unassigned (counted). Within a team: rotate `pointer`, skip names that are disabled logins, on leave (`user.get("on_leave")`) or over `daily_cap` assigned today. Writes `owner_why={kind, rule_id, at, by}` and a timeline line. Called from every entry point (import, sheet sync, Meta/inbound capture, Find leads, manual add). `POST /api/assignment/run-unowned` assigns existing unowned leads of the workspace (admin). CRUD endpoints for teams/rules/settings (admin of that workspace; workspace-scoped).
- [ ] Tests: rule order, first match wins, default team, no team → unassigned counted, rotation fairness over 9 leads/3 members, skipped disabled/leave/over-cap, never re-owns, workspace isolation (rules of w1 never apply to w2). Commit.

---
## Phase 4 — first-touch clock, alerts, reassign
### Task 6: `first_touch.py`
**Files:** Create `app/first_touch.py`; Modify `app/autorun.py` (call from `reminder_tick`), `app/assignment.py` (start clock), Test `tests/test_first_touch.py`
- [ ] Clock fields: `first_touch_due_at`, `first_touch_at`, `reassigns`. Start at assignment (working hours Mon–Sat 10:00–19:00 IST, per-workspace override). Touched = owner's email/WhatsApp/LinkedIn sent, call logged, note, meeting (hooks in `lead_model.event` kinds; a stage change alone does not count). `tick(store, now)`: at limit1 (default 30 working minutes) bell to owner; at limit2 (default 120) move to next team member (via `assignment`), bell to old owner + manager; max 2 moves then flag `stuck` and notify the manager. Skip Do-Not-Contact, closed stages, already touched. Switches `assignment.on`, `first_touch.alert`, `first_touch.reassign`. Reassign keeps running sequences (email continues with the new owner; LinkedIn ID unchanged). Tests with a fake clock: alert once, reassign once, max 2, working hours, switches, isolation. Commit.

## Phase 5 — screens and dashboard
### Task 7: UI
**Files:** Modify `web/src/pages/Lists.jsx`, `Leads.jsx`, `LeadDrawer.jsx`, `LeadBulkBar.jsx`, `Dashboard.jsx`, `web/src/api.js`; Create `web/src/pages/ListAssign.jsx`, `web/src/pages/setup/Assignment.jsx`; Test `web/e2e/qa_ownership.mjs`
- [ ] Lists: "My lists" (members) / "All lists" + "Unassigned" queue with age (admins); assignee column and filter; Assign / Split / Share dialogs (`ListAssign.jsx`). Leads: only own leads for members when mode on; "Waiting for first touch" filter with timer; drawer shows "Assigned by rule X, round-robin, 14:02". Setup → Assignment: teams, ordered rules (drag or up/down), default team, minutes, switches, the **ownership mode** switch with the migration report and "fix owners". Imports: conflicts report link. Dashboard: per-member first-touch average, reassigned and unassigned counts. e2e (visible Chrome capable, run via `final-e2e/runqa.sh`): admin creates a team and rule, member uploads a file → own list; second member cannot see it; start email on own list works, on another's is refused; admin sees all; screenshots. Commit.

## Final (lead only)
- [ ] Merge branches, resolve conflicts, `vite build`, full pytest suite, agent sim, every `qa_*.mjs` touched, push, then deploy only when the owner says "deploy"; after deploy: live check with a real login, turn `ownership_mode` on only after the migration report is reviewed with the owner.
