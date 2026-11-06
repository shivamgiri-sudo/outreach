#!/usr/bin/env bash
# One-time (and re-runnable) setup of the cloud LinkedIn agent on the server. Run as root AFTER deploy.sh has copied the
# code to /opt/outreach/app:   sudo bash /opt/outreach/app/deploy/setup_cloud_linkedin.sh
# Installs Node.js 20 (NodeSource), Playwright (only that package, in /opt/outreach/linkedin-runtime), Chromium and its
# OS libraries, a 2 GB swap file if there is none, and the outreach-linkedin systemd service. Safe to run again (for
# example after the Playwright version in deploy/linkedin-runtime/package.json changes).
set -euo pipefail
APP=/opt/outreach/app
RUNTIME=/opt/outreach/linkedin-runtime
BROWSERS=/opt/outreach/ms-playwright
CLOUD=/var/lib/outreach/linkedin-cloud
ENV_FILE=/etc/outreach/outreach.env
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)."; exit 1; }
[ -f "$APP/tools/linkedin_cloud_supervisor.mjs" ] || { echo "Deploy the app first ($APP/tools/linkedin_cloud_supervisor.mjs is missing)."; exit 1; }
id outreach >/dev/null 2>&1 || { echo "The outreach user is missing: run deploy/setup_server.sh first."; exit 1; }

# 1. Node.js 20 from NodeSource (skipped when node 18+ is already there)
NODE_MAJOR=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)
if [ "$NODE_MAJOR" -lt 18 ]; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl gnupg
  curl -fsSL https://deb.nodesource.com/setup_20.x -o /tmp/nodesource_setup.sh
  bash /tmp/nodesource_setup.sh
  DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs
fi
echo "node $(node -v), npm $(npm -v)"

# 2. Swap: one Chromium on a ~1 GB machine needs room to breathe
if [ -z "$(swapon --show --noheadings)" ]; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  echo "2 GB swap file added"
fi

# 3. Playwright only (web/node_modules is not deployed): a minimal package with a lock file, installed with npm ci
mkdir -p "$RUNTIME"
cp "$APP/deploy/linkedin-runtime/package.json" "$APP/deploy/linkedin-runtime/package-lock.json" "$RUNTIME/"
(cd "$RUNTIME" && npm ci --omit=dev --no-audit --no-fund)

# 4. Chromium + the OS libraries it needs (apt, so as root), into a shared folder the service user can read
mkdir -p "$BROWSERS"
(cd "$RUNTIME" && PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" npx --no-install playwright install --with-deps chromium)
chown -R outreach:outreach "$RUNTIME" "$BROWSERS"

# 5. Folders (0700, service user) and settings
mkdir -p "$CLOUD/home" "$CLOUD/profiles"
chown -R outreach:outreach "$CLOUD"
chmod 700 "$CLOUD" "$CLOUD/home" "$CLOUD/profiles"
grep -q '^OUTREACH_CLOUD_LI_DIR=' "$ENV_FILE" || echo "OUTREACH_CLOUD_LI_DIR=$CLOUD" >> "$ENV_FILE"
grep -q '^OUTREACH_CLOUD_LI_MAX=' "$ENV_FILE" || echo "OUTREACH_CLOUD_LI_MAX=1" >> "$ENV_FILE"

# 6. Check that Chromium starts as the service user (catches missing libraries before the service does)
sudo -u outreach env HOME="$CLOUD/home" PLAYWRIGHT_BROWSERS_PATH="$BROWSERS" node --input-type=module -e "
  const { chromium } = await import('$RUNTIME/node_modules/playwright/index.mjs');
  const b = await chromium.launch({ headless: true, args: ['--disable-dev-shm-usage'] });
  const p = await b.newPage(); await p.setContent('<h1>ok</h1>');
  console.log('Chromium works:', await p.textContent('h1'), b.version()); await b.close();"

# 7. Service; the app is restarted so it reads the new settings
cp "$APP/deploy/outreach-linkedin.service" /etc/systemd/system/outreach-linkedin.service
systemctl daemon-reload
systemctl restart outreach
systemctl enable outreach-linkedin
systemctl restart outreach-linkedin
sleep 3
systemctl --no-pager --lines=5 status outreach-linkedin || true
echo
echo "Done. Add a cloud account on the LinkedIn outreach page (Cloud agent card). Logs: journalctl -u outreach-linkedin -f"
