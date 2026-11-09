"""Tamper evidence for the change log (the `audit` collection).

Every row added to `audit` gets `seq` (1, 2, 3 …), `prev_hash` (the hash of the row before it) and `hash`
= sha256(canonical JSON of the row without `hash` + prev_hash). The store calls `seal(old, new)` on every save of
`audit`, under its lock, so every writer (old code that appends rows by hand included) is chained, and two saves at
once cannot fork the chain.

Rows written before this existed have no hash; the chain starts at the first sealed row. When the size cap trims old
rows, the newest trimmed chained row is kept as an anchor row at the top ({"action": "audit.anchor", anchor_seq,
anchor_hash, trimmed}), so trimming is not reported as tampering. `verify(rows)` walks the chain.
"""
import hashlib
import json
from datetime import datetime, timezone

GENESIS = "0" * 64
ANCHOR = "audit.anchor"


def canonical(row: dict) -> str:
    return json.dumps({k: v for k, v in row.items() if k != "hash"}, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str)


def row_hash(row: dict, prev_hash: str) -> str:
    return hashlib.sha256((canonical(row) + (prev_hash or "")).encode("utf-8")).hexdigest()


def is_anchor(row) -> bool:
    return isinstance(row, dict) and row.get("action") == ANCHOR


def chained(row) -> bool:
    return isinstance(row, dict) and not is_anchor(row) and "hash" in row and "seq" in row


def _anchor_of(rows: list):
    return next((r for r in rows if is_anchor(r)), None)


def _tail(rows: list) -> tuple:
    """(seq, hash) of the newest chained row, else of the anchor, else (0, GENESIS)."""
    for r in reversed(rows):
        if chained(r):
            return int(r["seq"]), r["hash"]
    anchor = _anchor_of(rows)
    if anchor and anchor.get("anchor_hash"):
        return int(anchor.get("anchor_seq") or 0), anchor["anchor_hash"]
    return 0, GENESIS


def _new_rows(old: list, new: list) -> list:
    """Indexes (in `new`) of the trailing unsealed rows that were not already in `old`."""
    tail = []
    for i in range(len(new) - 1, -1, -1):
        r = new[i]
        if chained(r) or is_anchor(r) or not isinstance(r, dict):
            break
        tail.append(i)
    if not tail:
        return []
    known = {}
    for r in reversed(old):                          # only old rows after the last sealed one can look like these;
        if not isinstance(r, dict) or chained(r) or is_anchor(r):   # once the chain exists that is none (no work)
            break
        key = canonical(r)
        known[key] = known.get(key, 0) + 1
    fresh = []
    for i in sorted(tail):
        key = canonical(new[i])
        if known.get(key):
            known[key] -= 1
        else:
            fresh.append(i)
    return fresh


def seal(old: list, new) -> list:
    """The `audit` list to save: new rows chained, the anchor kept at the top when old rows were trimmed."""
    if not isinstance(new, list):
        return new
    old = old if isinstance(old, list) else []
    body = [r for r in new if not is_anchor(r)]
    fresh = set(_new_rows(old, body))
    seq, prev = _tail(old)
    out = []
    for i, r in enumerate(body):
        if i in fresh:
            seq += 1
            row = {k: v for k, v in r.items() if k not in ("seq", "prev_hash", "hash")}
            row.update(seq=seq, prev_hash=prev)
            row["hash"] = row_hash(row, prev)
            prev = row["hash"]
            out.append(row)
        else:
            out.append(r)
    # trimming: old rows that are gone from the new list (the writers cap the list at MAX_AUDIT_ROWS)
    old_body = [r for r in old if not is_anchor(r)]
    anchor = dict(_anchor_of(old) or {})
    kept_seqs = {int(r["seq"]) for r in out if chained(r)}
    first_kept = min(kept_seqs) if kept_seqs else None
    dropped = [r for r in old_body if chained(r) and (first_kept is None or int(r["seq"]) < first_kept)]
    dropped_count = max(0, len(old_body) + len(fresh) - len(out))
    from .audit import MAX_AUDIT_ROWS                # late import: audit -> hygiene -> … ; read each time (tests lower it)
    if dropped_count and len(out) >= MAX_AUDIT_ROWS:  # a shorter list lost rows some other way: verify reports them missing
        if dropped:
            last = max(dropped, key=lambda r: int(r["seq"]))
            if int(last["seq"]) > int(anchor.get("anchor_seq") or 0):
                anchor.update(anchor_seq=int(last["seq"]), anchor_hash=last["hash"])
        anchor.update(action=ANCHOR, by="system", by_email="", by_role="", target="change log",
                      at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      trimmed=int(anchor.get("trimmed") or 0) + dropped_count)
        anchor["detail"] = (f"{anchor['trimmed']} oldest row(s) trimmed by the size cap; the chain continues after "
                            f"row {anchor.get('anchor_seq') or 0}")
    return [anchor, *out] if anchor else out


def verify(rows: list) -> dict:
    """Walk the chain. Returns {ok, checked, legacy_rows, first_seq, last_seq, broken: [...], first_broken, missing_seq,
    unsealed_after_start, trimmed, anchor_seq, message}."""
    rows = rows if isinstance(rows, list) else []
    anchor = _anchor_of(rows)
    exp_seq = int((anchor or {}).get("anchor_seq") or 0)
    exp_prev = (anchor or {}).get("anchor_hash") or GENESIS
    started, legacy, unsealed, broken, missing, checked = False, 0, [], [], [], 0
    first_seq = last_seq = None
    for index, r in enumerate(rows):
        if is_anchor(r):
            continue
        if not chained(r):
            if started:
                unsealed.append({"index": index, "at": (r or {}).get("at"), "action": (r or {}).get("action") or "contact.edit"})
            else:
                legacy += 1
            continue
        checked += 1
        seq = int(r["seq"])
        if not started:
            started, first_seq = True, seq
        gap = seq > exp_seq + 1
        if gap:
            missing.extend(range(exp_seq + 1, seq))
        problem = None
        if seq <= exp_seq:
            problem = "out of order or repeated sequence number"
        elif row_hash(r, r.get("prev_hash") or "") != r.get("hash"):
            problem = "row was changed after it was written (hash does not match)"
        elif not gap and r.get("prev_hash") != exp_prev:
            problem = "link to the previous row is broken"
        if problem:
            broken.append({"seq": seq, "index": index, "at": r.get("at"), "action": r.get("action") or "contact.edit", "problem": problem})
        exp_seq, exp_prev, last_seq = max(exp_seq, seq), r.get("hash"), seq
    ok = not broken and not missing and not unsealed
    if not started:
        message = "No sealed rows yet: the chain starts with the next change."
    elif ok:
        message = f"Change log intact: {checked} sealed row(s) checked, rows {first_seq}–{last_seq}."
    else:
        bits = []
        if broken:
            bits.append(f"row {broken[0]['seq']} {broken[0]['problem']}")
        if missing:
            bits.append(f"{len(missing)} row(s) missing (deleted): " + ", ".join(str(m) for m in missing[:10]) + ("…" if len(missing) > 10 else ""))
        if unsealed:
            bits.append(f"{len(unsealed)} row(s) added without a hash after the chain started")
        message = "Change log NOT intact: " + "; ".join(bits) + "."
    note = ("Rows written before tamper evidence was switched on have no hash and cannot be checked "
            f"({legacy} such row(s)).") if legacy else ""
    return {"ok": ok, "checked": checked, "legacy_rows": legacy, "first_seq": first_seq, "last_seq": last_seq,
            "first_broken": broken[0] if broken else None, "broken": broken[:50], "missing_seq": missing[:500],
            "missing_count": len(missing), "unsealed_after_start": unsealed[:50],
            "trimmed": int((anchor or {}).get("trimmed") or 0), "anchor_seq": (anchor or {}).get("anchor_seq"),
            "message": message, "note": note}
