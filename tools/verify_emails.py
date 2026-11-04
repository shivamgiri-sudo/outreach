#!/usr/bin/env python3
"""Check whether mailboxes probably exist WITHOUT sending any message.

  python tools/verify_emails.py --input people.csv --output checked.csv --helo mail.yourdomain.com --mail-from verify@yourdomain.com

Reads a CSV with an `email` column (other columns are kept) and writes it back with: status, smtp_code, smtp_message,
mx_host, is_catch_all, checked_at. Prints a count per status. See docs/EMAIL_VERIFIER.md for setup (port 25, reverse DNS)
and the limits. `confirmed` and `rejected` are answers; every other status means "not confirmed", never "invalid".
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import smtp_probe  # noqa: E402

OUT_COLUMNS = ["status", "smtp_code", "smtp_message", "mx_host", "is_catch_all", "checked_at"]
CACHE_DAYS = 7


def load_cache(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def fresh(row: dict, now: datetime) -> bool:
    try:
        return now - datetime.fromisoformat(row["checked_at"]) < timedelta(days=CACHE_DAYS)
    except (KeyError, ValueError):
        return False


def run(rows: list, prober, cache: dict, now: datetime, retry_wait: int = 300, sleep=time.sleep) -> list:
    """Probe each row's `email`; returns the rows with the result columns added. Cached results are reused for 7 days."""
    out, pending = [], []
    for row in rows:
        key = (row.get("email") or "").strip().lower()
        hit = cache.get(key)
        if hit and fresh(hit, now):
            result = hit
        else:
            try:
                result = prober.check(key)
            except smtp_probe.ProbeRefused as exc:
                result = {"status": "error", "smtp_code": "", "smtp_message": str(exc), "mx_host": "", "is_catch_all": "",
                          "checked_at": smtp_probe.now_text()}
            cache[key] = result
            if result["status"] == "unknown_temporary":
                pending.append((len(out), key))
        out.append({**row, **{c: result.get(c, "") for c in OUT_COLUMNS}})
    if pending and retry_wait:
        sleep(retry_wait)                                           # greylisting: try once more after a pause
        for index, key in pending:
            try:
                result = prober.check(key)
            except smtp_probe.ProbeRefused:
                break
            cache[key] = result
            out[index].update({c: result.get(c, "") for c in OUT_COLUMNS})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--helo", required=True, help="hostname with matching reverse DNS (PTR) on this server's IP")
    ap.add_argument("--mail-from", required=True, help="sender address at a domain you own")
    ap.add_argument("--delay", type=float, default=1.0, help="minimum seconds between probes (never below 1)")
    ap.add_argument("--limit", type=int, default=smtp_probe.DEFAULT_PER_RUN, help=f"addresses per run (max {smtp_probe.MAX_PER_RUN})")
    ap.add_argument("--cache", default="verify_cache.json")
    ap.add_argument("--log", default="verify_probe.log")
    ap.add_argument("--retry-wait", type=int, default=300, help="seconds before retrying temporary (greylist) answers once; 0 = no retry")
    args = ap.parse_args(argv)
    if args.limit > smtp_probe.MAX_PER_RUN:
        ap.error(f"--limit cannot be above {smtp_probe.MAX_PER_RUN}")
    smtp_probe.MIN_GAP_SECONDS = max(1.0, args.delay)
    if not smtp_probe.port_25_open():
        print("Outbound port 25 looks blocked from here (test: telnet <any mx host> 25). The probe cannot run.", file=sys.stderr)
        return 2
    with open(args.input, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if "email" not in (reader.fieldnames or []):
            ap.error("the input CSV needs an `email` column")
        rows, columns = list(reader), list(reader.fieldnames)
    prober = smtp_probe.Prober(args.helo, args.mail_from, args.log, max_per_run=args.limit)
    cache = load_cache(args.cache)
    results = run(rows, prober, cache, datetime.now(timezone.utc), args.retry_wait)
    with open(args.cache, "w", encoding="utf-8") as handle:
        json.dump(cache, handle)
    with open(args.output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*columns, *[c for c in OUT_COLUMNS if c not in columns]])
        writer.writeheader()
        writer.writerows(results)
    for status, count in Counter(r["status"] for r in results).most_common():
        print(f"{status:20} {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
