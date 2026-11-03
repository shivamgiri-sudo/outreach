#!/usr/bin/env bash
# One-time server setup on Ubuntu 24.04 (Oracle Cloud Always Free). Run as root:  bash setup_server.sh <public-host>
# The app code is expected in /opt/outreach/app (copied by deploy.sh), with web/dist already built.
set -euo pipefail
HOST="${1:?usage: setup_server.sh <public-host, e.g. 1-2-3-4.sslip.io>}"

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv python3-pip debian-keyring debian-archive-keyring apt-transport-https curl gnupg cron

# Caddy (automatic HTTPS) from its official repository
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -y && apt-get install -y caddy
fi

# Swap (helps small machines)
if ! swapon --show | grep -q swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# Service user and folders
id outreach >/dev/null 2>&1 || useradd --system --home /opt/outreach --shell /usr/sbin/nologin outreach
mkdir -p /opt/outreach/app /var/lib/outreach /var/backups/outreach /etc/outreach
chown -R outreach:outreach /var/lib/outreach /var/backups/outreach
chmod 700 /var/lib/outreach /var/backups/outreach /etc/outreach

# Python environment
cd /opt/outreach/app
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chown -R outreach:outreach /opt/outreach

# Environment file (created once; edit /etc/outreach/outreach.env to change)
if [ ! -f /etc/outreach/outreach.env ]; then
  cat > /etc/outreach/outreach.env <<ENV
OUTREACH_ADMIN_PASSWORD=$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')
OUTREACH_DATA_FILE=/var/lib/outreach/store.json
OUTREACH_BASE_URL=https://${HOST}
OUTREACH_WEBHOOK_SECRET=$(python3 -c 'import secrets;print(secrets.token_hex(24))')
OUTREACH_SECRET_KEY=$(python3 -c 'import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')
OUTREACH_AUTORUN=1
ENV
  chmod 600 /etc/outreach/outreach.env
fi

# systemd + Caddy
cp deploy/outreach.service /etc/systemd/system/outreach.service
sed "s/__HOST__/${HOST}/" deploy/Caddyfile.template > /etc/caddy/Caddyfile
systemctl daemon-reload
systemctl enable --now outreach
systemctl reload caddy || systemctl restart caddy

# Backups
install -m 755 deploy/backup.sh /usr/local/bin/outreach-backup
echo "15 21 * * * outreach /usr/local/bin/outreach-backup" > /etc/cron.d/outreach-backup   # 02:45 IST

# Firewall: Oracle Ubuntu images block 80/443 in iptables by default
for port in 80 443; do
  iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null || iptables -I INPUT 6 -m state --state NEW -p tcp --dport $port -j ACCEPT
done
netfilter-persistent save 2>/dev/null || (apt-get install -y iptables-persistent && netfilter-persistent save)

echo "Done. Admin password is in /etc/outreach/outreach.env (OUTREACH_ADMIN_PASSWORD)."
