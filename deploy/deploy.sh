#!/usr/bin/env bash
# Deploy the current checkout to the server:  deploy/deploy.sh ubuntu@<ip> <public-host>
# Builds the web app locally, copies code (no data, no secrets), restarts the service (and the cloud LinkedIn agent
# service outreach-linkedin, only when deploy/setup_cloud_linkedin.sh installed it).
# Ownership: the code (/opt/outreach/app, with its .venv) belongs to root and is read-only for the service user
# "outreach", so a compromised app process cannot rewrite the code it runs; only the data and runtime folders
# (/var/lib/outreach, /var/backups/outreach, the systemd ReadWritePaths) are writable by it.
set -euo pipefail
TARGET="${1:?usage: deploy.sh ubuntu@<ip> <public-host>}"; HOST="${2:?public host}"
KEY="${SSH_KEY:-$HOME/.ssh/oracle_outreach}"
cd "$(dirname "$0")/.."
(cd web && npm ci --no-audit --no-fund >/dev/null && npm run build >/dev/null)
tar --exclude=.venv --exclude=web/node_modules --exclude=data --exclude='*.pyc' --exclude=__pycache__ --exclude=.git \
    --exclude=web/e2e-shots --exclude=web/tour-shots -czf /tmp/outreach-release.tgz .
scp -i "$KEY" -o StrictHostKeyChecking=accept-new /tmp/outreach-release.tgz "$TARGET:/tmp/"
ssh -i "$KEY" "$TARGET" "sudo mkdir -p /opt/outreach/app && sudo tar -xzf /tmp/outreach-release.tgz -C /opt/outreach/app && \
  (test -x /opt/outreach/app/.venv/bin/uvicorn || sudo bash /opt/outreach/app/deploy/setup_server.sh '$HOST') && \
  sudo /opt/outreach/app/.venv/bin/pip install -q -r /opt/outreach/app/requirements.txt && \
  sudo chown -R root:root /opt/outreach/app && sudo chmod -R u+rwX,go+rX,go-w /opt/outreach/app && \
  sudo chown root:root /opt/outreach && sudo chmod 755 /opt/outreach && \
  sudo mkdir -p /var/lib/outreach /var/backups/outreach && sudo chown -R outreach:outreach /var/lib/outreach /var/backups/outreach && \
  sudo systemctl restart outreach && \
  if [ -f /etc/systemd/system/outreach-linkedin.service ]; then \
    sudo cp /opt/outreach/app/deploy/outreach-linkedin.service /etc/systemd/system/outreach-linkedin.service && \
    sudo systemctl daemon-reload && sudo systemctl restart outreach-linkedin; fi && \
  for i in \$(seq 1 30); do curl -fsS http://127.0.0.1:8000/api/auth/status && exit 0; sleep 1; done; exit 1"
echo; echo "Deployed. Open https://$HOST"
