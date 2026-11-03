#!/usr/bin/env bash
# Nightly copy of the data file, kept 14 days. Run by /etc/cron.d/outreach-backup as the outreach user.
set -euo pipefail
src=/var/lib/outreach/store.json
dst=/var/backups/outreach
[ -f "$src" ] || exit 0
stamp=$(date -u +%Y%m%d-%H%M)
cp "$src" "$dst/store-$stamp.json"
chmod 600 "$dst/store-$stamp.json"
gzip -f "$dst/store-$stamp.json"
find "$dst" -name 'store-*.json.gz' -mtime +14 -delete
