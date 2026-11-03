"""Back up or restore Outreach OS from the command line (for server owners).

    python scripts/backup.py export --out outreach-backup.json.gz            # no connection keys (safe to keep)
    python scripts/backup.py export --out full.json.gz --include-secrets     # keys included: keep it like a password
    python scripts/backup.py restore --file outreach-backup.json.gz          # dry run: shows what would change
    python scripts/backup.py restore --file outreach-backup.json.gz --confirm

The data file is OUTREACH_DATA_FILE (or data/store.json), or --data-file. Stop the app before a restore from here
(sudo systemctl stop outreach): a running app keeps its data in memory and would write over the restored file.
While the app runs, restore from Setup -> Backup & restore instead.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import backup  # noqa: E402  (needs the project folder on the path first)
from app.store import Store  # noqa: E402


def _store(path: Path) -> Store:
    try:
        return Store(path)
    except RuntimeError as exc:
        sys.exit(str(exc))


def export(args) -> int:
    path = Path(args.data_file) if args.data_file else backup.default_data_file()
    if not path.exists():
        sys.exit(f"No data file at {path}. Pass --data-file or set OUTREACH_DATA_FILE.")
    payload = backup.build_backup(_store(path), include_secrets=args.include_secrets)
    out = Path(args.out)
    out.write_bytes(backup.to_bytes(payload))
    out.chmod(0o600)
    rows = ", ".join(f"{k} {v}" for k, v in payload["counts"].items() if v)
    print(f"Saved {out} ({out.stat().st_size} bytes) from {path}.\nContains: {rows or 'no data yet'}.\n{payload['note']}")
    return 0


def restore(args) -> int:
    path = Path(args.data_file) if args.data_file else backup.default_data_file()
    try:
        payload = backup.read_payload(Path(args.file).read_bytes())
    except OSError as exc:
        sys.exit(f"Could not read {args.file}: {exc}")
    except backup.BackupError as exc:
        sys.exit(f"Refused: {exc}")
    store = _store(path)
    if not args.confirm:
        plan = backup.summary(store, payload, args.keep_secrets)
        print(f"Backup made {plan['backup']['created_at_ist'] or plan['backup']['created_at']}"
              f" (secrets included: {'yes' if plan['backup']['includes_secrets'] else 'no'}). Data file: {path}")
        print(f"{'collection':<22}{'now':>8}{'after':>8}")
        for row in plan["collections"]:
            print(f"{row['name']:<22}{row['now']:>8}{row['after']:>8}")
        print("\n".join(["", plan["will_replace"], *plan["notes"], "", "Nothing changed. Add --confirm to restore."]))
        return 0
    try:
        result = backup.restore(store, payload, keep_secrets=args.keep_secrets)
    except backup.BackupError as exc:
        sys.exit(f"Refused: {exc}")
    print(result["message"])
    for note in result["notes"][1:]:
        print(note)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Back up or restore the Outreach OS data file.")
    parser.add_argument("--data-file", help="the data file (default: OUTREACH_DATA_FILE or data/store.json)")
    commands = parser.add_subparsers(dest="command", required=True)
    out = commands.add_parser("export", help="write a backup file (.json.gz)")
    out.add_argument("--out", required=True, help="where to save the backup, e.g. outreach-backup.json.gz")
    out.add_argument("--include-secrets", action="store_true", help="also save connection keys and secrets (plain text!)")
    out.add_argument("--data-file", dest="data_file", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    back = commands.add_parser("restore", help="replace the data with a backup (dry run unless --confirm)")
    back.add_argument("--file", required=True, help="the backup file (.json.gz or .json)")
    back.add_argument("--confirm", action="store_true", help="really replace the data (the old file is kept beside it)")
    back.add_argument("--keep-secrets", action="store_true", help="also take the server secret from a backup made with secrets")
    back.add_argument("--data-file", dest="data_file", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return export(args) if args.command == "export" else restore(args)


if __name__ == "__main__":
    sys.exit(main())
