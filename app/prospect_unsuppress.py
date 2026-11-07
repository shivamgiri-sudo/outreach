"""Undo a prospect-database opt-out (owner only, audited by the route).

The prospect suppression list (pdb_suppressions) is local to the prospect database. The global lists always win:
  - store "suppressed"          unsubscribes, bounces and the CRM do-not-contact list (emails)
  - store "suppressed_hashes"   people erased on request (privacy.py hashes), plus pdb_erased
  - CRM leads marked do_not_contact or in stage "unsubscribed"
An email on any of these is refused with the reason. Lifting a domain opt-out re-checks every person at that domain:
anyone still on a global list (or with their own email opt-out) stays suppressed and is counted in `kept`.

  list_suppressions(store) -> [{value, kind, reason, at, blocked}]
  unsuppress(store, value) -> {unsuppressed, kind, people, kept}   raises Refused / ValueError / LookupError
"""
from . import prospect_fields as F
from .hygiene import normalise_email
from .privacy import contact_hash, hashes_for
from .prospect_db import _doc, _suppressed_value
from .prospect_repo import repo_for

MAX_LIST = 500


class Refused(Exception):
    """The value is on a global do-not-contact / unsubscribe / erased list, so it stays suppressed."""


def _email_hashes(email: str) -> set:
    out = hashes_for(email)
    norm = F.normalise_email(email)
    if norm:
        out.add(contact_hash(norm))
    return out


class Blocklist:
    """The global lists read once, so checking many emails does not re-read the store per email."""

    def __init__(self, store, repo=None):
        self.repo = repo or repo_for(store)
        self.suppressed = set(store.read("suppressed"))
        self.erased = set(store.read("suppressed_hashes"))
        self.leads = {}
        for lead in store.read("leads"):
            e = normalise_email(lead.get("email"))
            if e and (lead.get("do_not_contact") or lead.get("stage") == "unsubscribed"):
                self.leads[e] = ("the CRM lead with this email is marked do-not-contact" if lead.get("do_not_contact")
                                 else "the CRM lead with this email has unsubscribed")

    def reason(self, email: str) -> str:
        """Why this email must stay suppressed everywhere, or "" when no global list holds it."""
        email = F.normalise_email(email) or normalise_email(email) or ""
        if not email:
            return ""
        if email in self.suppressed:
            return "it is on the do-not-contact / unsubscribe list"
        hashes = _email_hashes(email)
        if hashes & self.erased or (hashes and self.repo.q1(
                f"SELECT 1 AS x FROM pdb_erased WHERE h IN ({', '.join('?' for _ in hashes)})", list(hashes))):
            return "the person was erased on request (privacy list)"
        return self.leads.get(email, "")


def global_block_reason(store, email: str, repo=None) -> str:
    return Blocklist(store, repo).reason(email)


def list_suppressions(store, limit: int = MAX_LIST) -> list:
    repo = repo_for(store)
    rows = repo.q("SELECT value, kind, reason, at FROM pdb_suppressions ORDER BY at DESC LIMIT ?", (int(limit),))
    block = Blocklist(store, repo)
    for r in rows:
        r["blocked"] = block.reason(r["value"]) if r["kind"] == "email" else ""
    return rows


def _person_emails(row: dict) -> list:
    doc = _doc(row)
    out = [e["v"] for e in doc.get("fields", {}).get("email", []) if e.get("v")]
    return list(dict.fromkeys([*([row["email"]] if row.get("email") else []), *out]))


def _recheck(block, repo, rows: list) -> tuple:
    """Re-decide `suppressed` for these people after an entry was removed. Returns (lifted, kept)."""
    lifted = kept = 0
    for row in rows:
        emails = _person_emails(row)
        local = _suppressed_value(repo, *emails, row.get("company_domain"), *(F.clean_domain(e) for e in emails))
        if local or any(block.reason(e) for e in emails):
            kept += 1
            continue
        repo.x("UPDATE pdb_people SET suppressed = 0 WHERE id = ?", (row["id"],))
        lifted += 1
    return lifted, kept


def unsuppress(store, value: str) -> dict:
    raw = str(value or "").strip().lower()
    email = F.normalise_email(raw)
    domain = None if email else F.clean_domain(raw)
    if not (email or domain):
        raise ValueError("Give an email address or a company domain.")
    target = email or domain
    repo = repo_for(store)
    if not repo.q1("SELECT value FROM pdb_suppressions WHERE value = ?", (target,)):
        raise LookupError(f"{target} is not on the prospect opt-out list.")
    block = Blocklist(store, repo)
    if email:
        reason = block.reason(email)
        if reason:
            raise Refused(f"{email} stays suppressed: {reason}.")
    with repo.tx():
        repo.x("DELETE FROM pdb_suppressions WHERE value = ?", (target,))
        if email:
            rows = repo.q("SELECT * FROM pdb_people WHERE suppressed = 1 AND id IN (SELECT ref FROM pdb_keys WHERE k = ?)", ("e:" + email,))
        else:
            rows = repo.q("SELECT * FROM pdb_people WHERE suppressed = 1 AND (company_domain = ? OR email LIKE ?)", (domain, "%@" + domain))
            repo.x("UPDATE pdb_companies SET suppressed = 0 WHERE domain = ?", (domain,))
        lifted, kept = _recheck(block, repo, rows)
    return {"unsuppressed": target, "kind": "email" if email else "domain", "people": lifted, "kept": kept}
