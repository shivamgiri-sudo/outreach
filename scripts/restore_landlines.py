#!/usr/bin/env python3
"""One-off repair: put back office landlines that older code dropped from the lead phone.

Same rules as Data hygiene > Restore landlines (app/landline_restore.py): only leads whose phone is EMPTY and whose own
saved data (company_phone, other numbers, a custom phone field, a value-to-check note) holds a landline with its STD
code. Do-not-contact, unsubscribed and erased numbers are skipped. Nothing is overwritten. An applied run is audited
and can be undone from the Data hygiene page (or with --undo RUN_ID).

  python scripts/restore_landlines.py --data-file data/store.json            # dry run (the default): counts only
  python scripts/restore_landlines.py --data-file data/store.json --apply
  python scripts/restore_landlines.py --data-file data/store.json --undo <run id>
With OUTREACH_DATABASE_URL set, the MySQL store is used (as the app does); keep OUTREACH_SECRET_KEY as the app has it.
Stop the app (or run it when idle) before --apply on the JSON store: the running app keeps its own copy in memory.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audit, landline_restore  # noqa: E402
from app.main import DEFAULT_DATA_FILE, open_store  # noqa: E402

BY = "ops script (restore_landlines.py)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="show what would change (the default)")
    mode.add_argument("--apply", action="store_true", help="fill the empty phones")
    mode.add_argument("--undo", metavar="RUN_ID", help="undo an applied run")
    ap.add_argument("--data-file", default=str(DEFAULT_DATA_FILE), help="JSON store (ignored for data when OUTREACH_DATABASE_URL is set)")
    ap.add_argument("--show", type=int, default=10, help="sample rows to print")
    args = ap.parse_args(argv)
    path = Path(args.data_file)
    if not path.exists() and not os.environ.get("OUTREACH_DATABASE_URL"):
        print(f"No data file at {path}", file=sys.stderr)
        return 2
    store = open_store(path)
    if args.undo:
        try:
            result = landline_restore.undo(store, args.undo, BY)
        except (LookupError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        audit.record(store, BY, "hygiene.restore_landlines_undo", f"{result['reverted']} lead(s)", f"run {args.undo}")
        print(f"Undone run {args.undo}: reverted {result['reverted']}, left {result['kept']} changed since, {result['missing']} gone.")
        return 0
    result = landline_restore.apply(store, BY) if args.apply else landline_restore.plan(store)
    count = result["changed"] if args.apply else result["would_change"]
    print(("Applied" if args.apply else "Dry run") + f": {count} lead(s) {'got' if args.apply else 'would get'} a landline back.")
    for reason, n in sorted(result["skipped"].items()):
        print(f"  skipped {n} ({reason})")
    for c in result["sample"][:args.show]:
        print(f"  {c['lead_id']}  {c['company'] or c['contact_name'] or '-'}: {c['phone']} from {c['from']} \"{c['raw']}\"")
    if args.apply:
        audit.record(store, BY, "hygiene.restore_landlines", f"{count} lead(s)", f"run {result['run_id']} (ops script)")
        print(f"Run id {result['run_id']} (undo: --undo {result['run_id']} or Data hygiene > Restore landlines).")
    else:
        print("Nothing was changed. Add --apply to fill these phones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
