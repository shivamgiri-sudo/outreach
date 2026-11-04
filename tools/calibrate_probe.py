#!/usr/bin/env python3
"""Measure the mailbox probe against addresses whose answer you KNOW, from this machine. No message is sent.

  python tools/calibrate_probe.py --helo mail.yourdomain.com --mail-from careers@yourdomain.com \\
      --truth tools/samples/calibration.csv

The truth file has `email,expected` where expected is `valid` (the mailbox exists) or `invalid` (it does not). Use your own
domains and mailboxes. The report counts, per mail provider, how many were right, how many came back "not confirmed", and how
many were WRONG (a real mailbox called rejected, or a made-up one called confirmed). Wrong answers must be zero.
"""
import argparse
import csv
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import smtp_probe  # noqa: E402

VERDICT = {"confirmed": "valid", "rejected": "invalid"}


def score(rows: list, prober) -> dict:
    """rows: [{"email", "expected"}] -> per provider {"right", "not_confirmed", "wrong", "detail": [...]}."""
    table = defaultdict(lambda: {"right": 0, "not_confirmed": 0, "wrong": 0, "detail": []})
    for row in rows:
        result = prober.check(row["email"])
        provider = result.get("provider") or "no mail server"
        got = VERDICT.get(result["status"])
        bucket = table[provider]
        if got is None:
            bucket["not_confirmed"] += 1
        elif got == row["expected"]:
            bucket["right"] += 1
        else:
            bucket["wrong"] += 1
        bucket["detail"].append((row["email"], row["expected"], result["status"], result.get("smtp_code"), str(result.get("smtp_message"))[:70]))
    return dict(table)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--helo", required=True)
    ap.add_argument("--mail-from", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--log", default="calibrate_probe.log")
    args = ap.parse_args(argv)
    if not smtp_probe.port_25_open():
        print("Outbound port 25 looks blocked from here.", file=sys.stderr)
        return 2
    with open(args.truth, newline="", encoding="utf-8-sig") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("email")]
    extra = os.environ.get("CALIBRATE_EXTRA_VALID", "")
    rows += [{"email": e.strip(), "expected": "valid"} for e in extra.split(",") if e.strip()]
    prober = smtp_probe.Prober(args.helo, args.mail_from, args.log, max_per_run=smtp_probe.MAX_PER_RUN)
    table = score(rows, prober)
    total = Counter()
    for provider, b in sorted(table.items()):
        print(f"{provider:14} right {b['right']:3}  not confirmed {b['not_confirmed']:3}  WRONG {b['wrong']:3}")
        for email, expected, status, code, msg in b["detail"]:
            print(f"    {email:42} expected {expected:7} got {status:17} {code} {msg}")
        total.update({k: v for k, v in b.items() if k != "detail"})
    print(f"TOTAL right {total['right']}, not confirmed {total['not_confirmed']}, WRONG {total['wrong']}")
    return 1 if total["wrong"] else 0


if __name__ == "__main__":
    sys.exit(main())
