#!/usr/bin/env python3
"""Check the Hiring engine's email guesses from THIS machine (where outbound port 25 is open) and send the results back.

  PROBE_URL=https://your-app PROBE_EMAIL=you@company.com PROBE_PASSWORD=... \\
  python tools/probe_live.py --helo mail.yourdomain.com --mail-from verify@yourdomain.com [--limit 50] [--dry]

It asks the app which guessed addresses wait for a check, probes each with the mailbox probe (no message is sent, DATA is never
issued, 1 probe a second), and posts the answers back. The app then promotes a confirmed mailbox to the lead's email and keeps
everything else as a labelled guess. See docs/EMAIL_VERIFIER.md.
"""
import argparse
import getpass
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import smtp_probe  # noqa: E402


def call(base: str, path: str, token: str = "", body=None):
    req = urllib.request.Request(base.rstrip("/") + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=60) as reply:
        return json.load(reply)


def probe_all(wanted: list, prober, limit: int) -> tuple:
    """([{"lead_id", "checks": [{"email", "status"}]}], count per status). Stops a lead at its first confirmed mailbox."""
    results, counts, done = [], {}, 0
    for item in wanted:
        checks = []
        for guess in item["guesses"]:
            if done >= limit:
                break
            try:
                answer = prober.check(guess["email"])
            except smtp_probe.ProbeRefused:
                done = limit
                break
            done += 1
            counts[answer["status"]] = counts.get(answer["status"], 0) + 1
            checks.append({"email": guess["email"], "status": answer["status"]})
            if answer["status"] == "confirmed":
                break
        if checks:
            results.append({"lead_id": item["lead_id"], "checks": checks})
    return results, counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--helo", required=True)
    ap.add_argument("--mail-from", required=True)
    ap.add_argument("--limit", type=int, default=50, help=f"addresses to probe (max {smtp_probe.MAX_PER_RUN})")
    ap.add_argument("--again", action="store_true", help="also re-probe leads whose guesses were all rejected earlier")
    ap.add_argument("--dry", action="store_true", help="probe and print, but do not send the results back")
    ap.add_argument("--log", default="probe_live.log")
    args = ap.parse_args(argv)
    base = os.environ.get("PROBE_URL") or input("App URL: ")
    email = os.environ.get("PROBE_EMAIL") or input("Login email: ")
    password = os.environ.get("PROBE_PASSWORD") or getpass.getpass("Password: ")
    if not smtp_probe.port_25_open():
        print("Outbound port 25 looks blocked from here.", file=sys.stderr)
        return 2
    limit = min(args.limit, smtp_probe.MAX_PER_RUN)
    token = call(base, "/api/login", body={"email": email, "password": password})["token"]
    wanted = call(base, f"/api/hiring-engine/candidates?max_leads={limit}&include_rejected={str(args.again).lower()}", token)
    print(f"{len(wanted)} leads have guesses waiting.")
    prober = smtp_probe.Prober(args.helo, args.mail_from, args.log, max_per_run=limit)
    results, counts = probe_all(wanted, prober, limit)
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:20} {count}")
    if args.dry:
        print("Dry run: nothing sent back.")
        return 0
    print("Saved:", call(base, "/api/hiring-engine/apply", token, {"results": results}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
