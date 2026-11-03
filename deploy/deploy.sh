#!/usr/bin/env bash
# Deploy the current checkout to the server:  deploy/deploy.sh ubuntu@<ip> <public-host>
# Builds the web app locally, copies code (no data, no secrets), restarts the service.
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
  sudo chown -R outreach:outreach /opt/outreach && sudo systemctl restart outreach && sleep 2 && \
  curl -fsS http://127.0.0.1:8000/api/auth/status"
echo; echo "Deployed. Open https://$HOST"
