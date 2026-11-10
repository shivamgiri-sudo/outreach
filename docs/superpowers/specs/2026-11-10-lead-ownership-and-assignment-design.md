# Lead ownership, assignment rules, list assignment and speed-to-lead

Status: design approved by the owner on 2026-11-10 (assignment rules, speed-to-lead, list assignment, three conflict decisions).

## Goal
Every lead has exactly one owner at once. A person sends email, LinkedIn connect/messages and WhatsApp only to leads they own.
Lists are the unit admins hand out. A workspace never sees, or is affected by, another workspace.

## Decisions (owner)
- New-lead owner: **ordered rules, then round-robin inside the matching team**; no match goes to the workspace default team.
- First touch late: **alert the owner, then reassign** to the next person in the team and tell the manager.
- A member sees **only the leads they own** and lists assigned to them; admins see the whole workspace.
- Upload row already owned by someone else: **skip and report**; existing owner keeps it.
- Reassigning a list with running sequences: **continue under the new owner** (email from the new owner; LinkedIn keeps the same LinkedIn ID until the new owner picks theirs; no step is sent twice).

## Model
- `teams` (per workspace): `{id, name, members:[user ids], default:bool, daily_cap}`.
- `assignment_rules` (per workspace, ordered): `{id, when:{source?, city?, branch?, campaign?, tag?}, team}`; `assignment_pointer` per team (round-robin cursor).
- `list.assigned_to` (user id or `""` = admin pool), `list.shared_with` (view-only user ids), `list.assigned_at/by`.
- `lead.owner` is the single owner; `lead.owner_why` = `{kind: rule|upload|list|manual|reassigned, rule_id?, at, by}`.
- `lead.first_touch_due_at`, `lead.first_touch_at`, `lead.reassigns` (max 2).
- One access function `can_send(user, lead)` / `can_see(user, lead)` in `app/lead_access.py`: admin of the lead's workspace: yes; owner: yes; otherwise no. Workspace mismatch is always no.

## Flows
1. **Entry points** (import, sheet sync, Meta/inbound capture, Find leads, manual add, quick paste): a human uploader owns what they upload (list created and assigned to them). Connectors with no human use rules. Existing leads are never re-owned; owned-by-other rows go to the conflicts report (stored on the import record and shown to admins). Do-Not-Contact people are kept but not sendable.
2. **List assignment** (admin): assign whole list, assign selected leads (creates a new list), unassign (admin pool), split a file between members (even or by count), view-only sharing. Assigning sets `lead.owner` for members; leads owned by another member are reported, not taken, unless the admin chooses "take over".
3. **Reassign**: bell entry to old owner, new owner and admin; audit line; running sequences continue as decided; LinkedIn sends stay on the lead's LinkedIn ID (duplicate guard stays per ID); email steps use the new owner's sender.
4. **Member leaves/disabled**: banner "N lists have no active owner" with one-click reassign; sending for that user is refused meanwhile.
5. **Speed-to-lead**: clock starts at assignment, runs in working hours (default Mon-Sat 10:00-19:00 IST). Touched = email/WhatsApp/LinkedIn sent, call logged, note, meeting by the owner (a stage change alone is not). 30 min: bell alert; 2 h: move to next team member, tell old owner and manager; max 2 moves then "stuck" to the manager. Never for Do-Not-Contact, closed stages or already touched leads. Switches: assignment on/off, reassign on/off, per-workspace minutes.
6. **Enforcement** uses `can_send` in: list start-email/start-linkedin, single-lead start/enroll, bulk bar, extension queue/match/claim/sent, LinkedIn daily auto-start (own leads, own LinkedIn ID), flows/campaigns (admin only on others' leads, stamped "by system for X"), API/MCP tokens. Replies go to the lead's owner.
7. **Migration**: each existing list assigned to `created_by` if still active in the workspace, else admin pool; a one-time report of leads whose owner differs from their list's assignee with a "fix owners" button (never silent).
8. **Limits**: per-member daily upload cap and list-count cap; deleting a list never deletes leads; re-upload of the same file merges.

## UI
- Leads: "My leads" (members) / all (admins), "Waiting for first touch" with timer, owner reason in the drawer.
- Lists: "My lists" for members; admins get "All lists", "Unassigned" queue with age, assignee column/filter, assign / split dialogs.
- Setup -> Assignment: teams, ordered rules, default team, first-touch minutes, switches.
- Dashboard: per-member first-touch time, reassigned count, unassigned count.

## Testing
Rule order, rotation, skipped users, working hours, conflicts report, upload ownership, reassign with running sequences, every enforcement point refusing, workspace isolation (two workspaces, same person), migration, browser checks of the assignment and list screens, agent simulation for LinkedIn claim, then full suite and a live check after deploy.

## Build order
1. `lead_access` + list assignee + enforcement points + migration. 2. Upload ownership + conflicts report. 3. Teams, rules, round-robin at every entry point. 4. First-touch clock, alerts, reassignment. 5. Screens and dashboard.
