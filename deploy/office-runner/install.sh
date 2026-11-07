#!/usr/bin/env bash
# MCN LinkedIn office runner: installer for macOS and Linux. The portal serves this file with its address and a one-time
# pairing code filled in; paste the one line the portal shows:
#   curl -fsSL '<portal>/api/linkedin-runner/install.sh?code=XXXXXXXX' | bash
# Everything goes into your own user folder (no sudo):
#   1. Node.js 20 LTS if Node 18+ is missing (the official archive from nodejs.org, checksum verified)
#   2. the runner files from the portal, Playwright (npm ci) and its Chromium browser
#   3. connects this computer to the portal with the one-time code (the long-lived token is saved, readable only by you)
#   4. starts the runner now and at every login (macOS: a LaunchAgent; Linux: desktop autostart, or a systemd user
#      service on a machine without a desktop)
# Options (after `| bash -s --`): --no-autostart (do not register the login start), --no-start (do not start it now).
# Uninstall: bash "<runner folder>/uninstall.sh" (the path is printed at the end), then press Disconnect in the portal.
set -euo pipefail
PORTAL='__PORTAL__'
CODE='__CODE__'
NODE_VERSION='v20.18.1'

# Everything runs inside main(), called on the last line: bash has read the whole script before anything runs, and no
# command (npm, curl) can swallow the rest of a script piped into bash.
main() {
AUTOSTART=1
START=1
for a in "$@"; do
  case "$a" in
    --no-autostart) AUTOSTART=0 ;;
    --no-start) START=0 ;;
  esac
done
say() { printf '[MCN] %s\n' "$*"; }
fail() { printf '[MCN] %s\n' "$*" >&2; exit 1; }

OS=$(uname -s)
case "$OS" in
  Darwin) BASE="$HOME/Library/Application Support/MCN-LinkedIn-Runner" ;;
  Linux) BASE="${XDG_DATA_HOME:-$HOME/.local/share}/mcn-linkedin-runner" ;;
  *) fail "This installer is for macOS and Linux. On Windows use the PowerShell line from the portal." ;;
esac
APP="$BASE/app"
RUNNER="$APP/tools/linkedin_runner.mjs"
mkdir -p "$BASE"
chmod 700 "$BASE"
command -v curl >/dev/null || fail "curl is needed."
command -v tar >/dev/null || fail "tar is needed."

# 1. Node.js
node_ok() { "$1" -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 18 ? 0 : 1)' >/dev/null 2>&1; }
NODE=""
if command -v node >/dev/null && node_ok "$(command -v node)"; then
  NODE=$(command -v node)
elif [ -x "$BASE/node/bin/node" ] && node_ok "$BASE/node/bin/node"; then
  NODE="$BASE/node/bin/node"
else
  case "$(uname -m)" in
    x86_64|amd64) ARCH=x64 ;;
    arm64|aarch64) ARCH=arm64 ;;
    *) fail "This computer's processor ($(uname -m)) is not supported." ;;
  esac
  PLATFORM=$([ "$OS" = Darwin ] && echo darwin || echo linux)
  FILE="node-$NODE_VERSION-$PLATFORM-$ARCH.tar.gz"
  say "Downloading Node.js $NODE_VERSION from nodejs.org..."
  TMP=$(mktemp -d)
  curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/$FILE" -o "$TMP/$FILE"
  curl -fsSL "https://nodejs.org/dist/$NODE_VERSION/SHASUMS256.txt" -o "$TMP/SHASUMS256.txt"
  WANT=$(grep " $FILE\$" "$TMP/SHASUMS256.txt" | cut -d' ' -f1)
  if command -v sha256sum >/dev/null; then GOT=$(sha256sum "$TMP/$FILE" | cut -d' ' -f1); else GOT=$(shasum -a 256 "$TMP/$FILE" | cut -d' ' -f1); fi
  [ -n "$WANT" ] && [ "$WANT" = "$GOT" ] || fail "The Node.js download did not match its checksum; try again."
  rm -rf "$BASE/node"
  mkdir -p "$BASE/node"
  tar -xzf "$TMP/$FILE" -C "$BASE/node" --strip-components 1
  rm -rf "$TMP"
  NODE="$BASE/node/bin/node"
fi
NODE_DIR=$(dirname "$NODE")
export PATH="$NODE_DIR:$PATH"
say "Node.js $("$NODE" -v) ($NODE)"

# Stop a runner that is already installed (this is also how it is updated)
if [ "$OS" = Darwin ]; then
  launchctl bootout "gui/$(id -u)/com.mcn.linkedin-runner" >/dev/null 2>&1 || true
else
  systemctl --user stop mcn-linkedin-runner.service >/dev/null 2>&1 || true
fi
pkill -f "$RUNNER" >/dev/null 2>&1 || true

# 2. The runner files, Playwright and Chromium
say "Downloading the runner from $PORTAL ..."
TMP=$(mktemp -d)
curl -fsSL "$PORTAL/api/linkedin-runner/bundle.tar.gz?code=$CODE" -o "$TMP/bundle.tar.gz" \
  || fail "Could not download the runner. The code may have expired (15 minutes): make a new one in the portal."
rm -rf "$APP/tools"
mkdir -p "$APP"
tar -xzf "$TMP/bundle.tar.gz" -C "$APP"
rm -rf "$TMP"
say "Installing Playwright (this takes a minute)..."
(cd "$APP" && npm ci --omit=dev --no-audit --no-fund --loglevel=error)
say "Installing the Chromium browser for Playwright..."
(cd "$APP" && npx --no-install playwright install chromium)
if [ "$OS" = Linux ]; then
  (cd "$APP" && "$NODE" --input-type=module -e "const { chromium } = await import('playwright'); const b = await chromium.launch(); await b.close();") >/dev/null 2>&1 \
    || say "Chromium could not start: some system libraries are missing. Ask someone with admin rights to run once: cd \"$APP\" && sudo npx playwright install-deps chromium"
fi

# 3. Connect to the portal
"$NODE" "$RUNNER" --portal "$PORTAL" --code "$CODE" --pair-only --home "$BASE" \
  || grep -q '"token"' "$BASE/config.json" 2>/dev/null \
  || fail "Could not connect this computer to the portal (see the message above)."

# Uninstaller
cat > "$BASE/uninstall.sh" <<UNINSTALL
#!/usr/bin/env bash
# Removes the MCN LinkedIn office runner from this computer (the browser sessions too). Then press Disconnect in the portal.
launchctl bootout "gui/\$(id -u)/com.mcn.linkedin-runner" >/dev/null 2>&1 || true
rm -f "\$HOME/Library/LaunchAgents/com.mcn.linkedin-runner.plist"
systemctl --user disable --now mcn-linkedin-runner.service >/dev/null 2>&1 || true
rm -f "\${XDG_CONFIG_HOME:-\$HOME/.config}/systemd/user/mcn-linkedin-runner.service" "\${XDG_CONFIG_HOME:-\$HOME/.config}/autostart/mcn-linkedin-runner.desktop"
pkill -f "$RUNNER" >/dev/null 2>&1 || true
sleep 2
rm -rf "$BASE"
echo "[MCN] The office runner is removed from this computer. Press Disconnect for it in the portal too."
UNINSTALL
chmod 700 "$BASE/uninstall.sh"

# 4. Start now and at every login
if [ "$AUTOSTART" = 1 ]; then
  if [ "$OS" = Darwin ]; then
    PLIST="$HOME/Library/LaunchAgents/com.mcn.linkedin-runner.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.mcn.linkedin-runner</string>
  <key>ProgramArguments</key><array><string>$NODE</string><string>$RUNNER</string><string>--home</string><string>$BASE</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardErrorPath</key><string>$BASE/logs/launchd.log</string>
</dict></plist>
PLIST
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    say "Registered to start at every login (LaunchAgent com.mcn.linkedin-runner) and started."
  elif [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
    AUTO="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
    mkdir -p "$AUTO"
    cat > "$AUTO/mcn-linkedin-runner.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=MCN LinkedIn office runner
Comment=Runs the portal's LinkedIn agents on this computer. Keep it running.
Exec="$NODE" "$RUNNER" --home "$BASE"
X-GNOME-Autostart-enabled=true
NoDisplay=false
DESKTOP
    if [ "$START" = 1 ]; then (nohup "$NODE" "$RUNNER" --home "$BASE" >/dev/null 2>&1 &); fi
    say "Registered to start at every desktop login (~/.config/autostart) and started."
  else
    UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$UNIT"
    cat > "$UNIT/mcn-linkedin-runner.service" <<SERVICE
[Unit]
Description=MCN LinkedIn office runner
After=network-online.target

[Service]
ExecStart="$NODE" "$RUNNER" --home "$BASE" --headless
Restart=on-failure
RestartSec=30

[Install]
WantedBy=default.target
SERVICE
    systemctl --user daemon-reload
    systemctl --user enable --now mcn-linkedin-runner.service
    say "No desktop found: registered as a systemd user service (no visible window). To keep it running while nobody is logged in: loginctl enable-linger $USER"
  fi
elif [ "$START" = 1 ]; then
  (nohup "$NODE" "$RUNNER" --home "$BASE" >/dev/null 2>&1 &)
  say "Started (not registered to start at login)."
fi

say "Done. In the portal, wait for \"Office computer online\", then add the LinkedIn account and log it in there."
say "Keep this computer switched on and connected during working hours, and do not close the LinkedIn browser window."
say "Log: $BASE/logs/runner.log   Uninstall: bash \"$BASE/uninstall.sh\""
}

main "$@" </dev/null
