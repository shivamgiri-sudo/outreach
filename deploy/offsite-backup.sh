#!/usr/bin/env bash
# Nightly encrypted backup of MCN Outreach: the MySQL database (OUTREACH_DATABASE_URL) and the data folder
# (/var/lib/outreach), kept locally for KEEP days and copied offsite with HTTP PUT.
#
# Offsite target: an Oracle Object Storage pre-authenticated request of type "write objects only" (URL ends in /o/).
# The server can add files but cannot list, read or delete them, so a hacked server cannot wipe the copies.
#
# Install (as root, once):
#   install -m 750 -o root -g root deploy/offsite-backup.sh /usr/local/sbin/outreach-offsite-backup
#   cp deploy/outreach-offsite-backup.service deploy/outreach-offsite-backup.timer /etc/systemd/system/
#   /etc/outreach/offsite-backup.env (chmod 600, never commit):
#     BACKUP_PASSPHRASE=<openssl rand -hex 32>    # keep a copy OFF the server: without it backups cannot be read
#     BACKUP_OFFSITE_URL=https://objectstorage.<region>.oraclecloud.com/p/<token>/n/<ns>/b/<bucket>/o/
#     BACKUP_OFFSITE_PREFIX=outreach/              # optional
#     BACKUP_OFFSITE_EXPIRES=2027-11-13            # link expiry, shown in the status file
#   systemctl daemon-reload && systemctl enable --now outreach-offsite-backup.timer
#   Test now: systemctl start outreach-offsite-backup.service && cat /var/lib/outreach-backup/status.json
#
# Restore:
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in outreach-db-*.sql.gz.enc | gunzip | mysql ...
#   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in outreach-files-*.tar.gz.enc | tar xz -C /
set -euo pipefail
umask 077

read_env() { # file: KEY=VALUE lines, without executing the file
  [ -r "$1" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" =~ ^[[:space:]]*([A-Z0-9_]+)=(.*)$ ]] || continue
    local k="${BASH_REMATCH[1]}" v="${BASH_REMATCH[2]}"
    v="${v%\"}"; v="${v#\"}"; v="${v%\'}"; v="${v#\'}"
    [ -z "${!k+x}" ] && export "$k=$v"
  done < "$1"
}
read_env "${BACKUP_ENV_FILE:-/etc/outreach/offsite-backup.env}"
read_env "${APP_ENV_FILE:-/etc/outreach/outreach.env}"

DIR="${BACKUP_DIR:-/var/lib/outreach-backup}"
DATA_DIR="${OUTREACH_DATA_DIR:-/var/lib/outreach}"
KEEP="${BACKUP_KEEP_DAYS:-14}"
STATUS="$DIR/status.json"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
WORK="$(mktemp -d)"
mkdir -p "$DIR"

json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g' | tr -d '\n\r'; }
status() { # ok message offsite_ok uploaded
  printf '{"ok":%s,"at":"%s","message":"%s","offsite":{"ok":%s,"uploaded":%s,"expires":"%s"}}\n' "$1" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "$(json_escape "$2")" "$3" "$4" "$(json_escape "${BACKUP_OFFSITE_EXPIRES:-}")" > "$STATUS.tmp"
  chmod 644 "$STATUS.tmp"; mv -f "$STATUS.tmp" "$STATUS"
}
fail() { echo "[outreach-backup] FAILED: $*" >&2; status false "$*" false 0; exit 1; }
trap 'rm -rf "$WORK"; rm -f "$DIR"/*.partial' EXIT
trap 'fail "unexpected error on line $LINENO"' ERR

[ "${#BACKUP_PASSPHRASE}" -ge 16 ] || fail "BACKUP_PASSPHRASE is missing or shorter than 16 characters"
enc() { openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:BACKUP_PASSPHRASE -out "$1"; }
dec() { openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:BACKUP_PASSPHRASE -in "$1"; }
FILES=()

# 1. Database (when Outreach runs on MySQL).
if [ -n "${OUTREACH_DATABASE_URL:-}" ]; then
  command -v mysqldump >/dev/null || fail "mysqldump is not installed"
  # Parse mysql[+driver]://user:pass@host:port/db (password may be URL-encoded) into a private options file.
  python3 - "$OUTREACH_DATABASE_URL" "$WORK/my.cnf" "$WORK/db" <<'PY' || fail "OUTREACH_DATABASE_URL is not a mysql:// URL"
import sys, urllib.parse
u = urllib.parse.urlsplit(sys.argv[1])
assert u.scheme.split("+")[0] == "mysql" and u.hostname and u.path.strip("/")
pw = urllib.parse.unquote(u.password or "").replace("\\", "\\\\").replace('"', '\\"')
open(sys.argv[2], "w").write(f'[client]\nhost={u.hostname}\nport={u.port or 3306}\nuser={urllib.parse.unquote(u.username or "")}\npassword="{pw}"\n')
open(sys.argv[3], "w").write(u.path.strip("/").split("?")[0])
PY
  DB="$(cat "$WORK/db")"
  OUT="$DIR/outreach-db-$STAMP.sql.gz.enc"
  GTID=""; mysqldump --help 2>/dev/null | grep -q -- '--set-gtid-purged' && GTID="--set-gtid-purged=OFF"
  if ! mysqldump --defaults-extra-file="$WORK/my.cnf" --single-transaction --quick --no-tablespaces $GTID "$DB" 2>"$WORK/dump.err" | gzip -6 | enc "$OUT.partial"; then
    fail "mysqldump failed: $(grep -v 'Using a password' "$WORK/dump.err" | head -c 200)"
  fi
  dec "$OUT.partial" | gunzip | tail -c 300 | grep -q "Dump completed" || fail "database dump is incomplete"
  TABLES="$(dec "$OUT.partial" | gunzip | grep -c '^CREATE TABLE' || true)"
  [ "$TABLES" -ge 1 ] || fail "database dump has no tables"
  mv -f "$OUT.partial" "$OUT"; sha256sum "$OUT" > "$OUT.sha256"
  FILES+=("$OUT" "$OUT.sha256")
  echo "[outreach-backup] database $DB: $TABLES tables, $(stat -c %s "$OUT") bytes"
fi

# 2. Data folder (JSON store, LinkedIn runtime files...).
if [ -d "$DATA_DIR" ]; then
  OUT="$DIR/outreach-files-$STAMP.tar.gz.enc"
  tar -C / -czf - "${DATA_DIR#/}" 2>"$WORK/tar.err" | enc "$OUT.partial" || fail "archiving $DATA_DIR failed: $(head -c 200 "$WORK/tar.err")"
  dec "$OUT.partial" | tar -tzf - >/dev/null || fail "data archive does not read back"
  mv -f "$OUT.partial" "$OUT"; sha256sum "$OUT" > "$OUT.sha256"
  FILES+=("$OUT" "$OUT.sha256")
  echo "[outreach-backup] files $DATA_DIR: $(stat -c %s "$OUT") bytes"
fi
[ "${#FILES[@]}" -gt 0 ] || fail "nothing to back up (no database URL and no data folder)"
find "$DIR" -maxdepth 1 -name 'outreach-*.enc*' -mtime +"$KEEP" -delete

# 3. Offsite copy. A failure keeps the local backup and is written to the status file.
trap - ERR
OFF_OK=true; SENT=0; MSG=""
if [ -n "${BACKUP_OFFSITE_URL:-}" ]; then
  case "$BACKUP_OFFSITE_URL" in https://*|http://127.0.0.1*|http://localhost*) ;; *) OFF_OK=false; MSG="BACKUP_OFFSITE_URL must be https";; esac
  PREFIX="${BACKUP_OFFSITE_PREFIX:-outreach/}"
  if $OFF_OK; then
    for f in "${FILES[@]}"; do
      if curl -sS --fail --retry 4 --retry-connrefused --retry-delay 15 --connect-timeout 20 --max-time 3600 -o /dev/null \
           -H "Content-Type: application/octet-stream" -T "$f" "${BACKUP_OFFSITE_URL}${PREFIX}$(basename "$f")" 2>"$WORK/curl.err"; then
        SENT=$((SENT + 1))
      else OFF_OK=false; MSG="offsite upload failed ($(basename "$f")): $(head -c 200 "$WORK/curl.err")"; break; fi
    done
  fi
  if $OFF_OK; then echo "[outreach-backup] offsite ok: $SENT file(s)"; else echo "[outreach-backup] offsite FAILED: $MSG" >&2; fi
else
  OFF_OK=false; MSG="no offsite copy: BACKUP_OFFSITE_URL is not set"
fi
status true "$MSG" "$OFF_OK" "$SENT"
$OFF_OK || exit 2   # makes the systemd unit show as failed so the problem is visible
