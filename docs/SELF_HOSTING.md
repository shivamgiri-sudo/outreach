# Running Outreach OS on your own server (or laptop)

Everything Outreach OS knows lives in **one file**: `store.json`. On the server set up by `deploy/setup_server.sh` it is
`/var/lib/outreach/store.json`; on a laptop it is `data/store.json` in the project folder, unless `OUTREACH_DATA_FILE`
says otherwise. Moving the app means: set up the app somewhere new, bring that one file (or a backup of it) along,
and switch sending on again.

> **Never run two copies with the same data and sending switched on.** Both would send the same email sequence and
> people would get every email twice. Stop the old server before you switch on sending on the new one (step 6).

---

## 1. Take your data with you

Pick one of these.

**A. Backup file (recommended).** Sign in, open **Setup → Backup & restore** and click **Download backup**. You get a
file like `outreach-backup-2026-11-03-1530-IST.json.gz` with all leads, messages, replies, tasks, campaigns and settings.

- By default it **leaves out connection keys and secrets** (email provider keys, mailbox passwords, calendar tokens,
  webhook secrets, the server secret). It is safe to keep on your laptop. After restoring, you enter the connections
  again on the Integrations page.
- Tick **Also include connection keys** only if you want the connections to work straight away on the new server.
  That file holds every key in plain text: keep it like a password, never email it or put it in a shared folder, and
  delete it once you have moved.
- Command-line alternative on the server (the app can keep running for an export):

      sudo -u outreach /opt/outreach/app/.venv/bin/python /opt/outreach/app/scripts/backup.py \
          --data-file /var/lib/outreach/store.json export --out /tmp/outreach-backup.json.gz
      # add --include-secrets to keep the keys (plain text!)

**B. Copy the data file itself.** Stop the app first so the file is not changing (`sudo systemctl stop outreach`), then
copy `/var/lib/outreach/store.json`. This copy contains **every secret** and keeps "send for real" exactly as it was,
so treat it like option A with keys. The nightly copies in `/var/backups/outreach/store-*.json.gz` are the same kind
of file (gzip them open with `gunzip`).

Copy the file to your computer, for example:

    scp ubuntu@OLD-SERVER:/tmp/outreach-backup.json.gz .

Also write down the settings from `/etc/outreach/outreach.env` on the old server (it needs `sudo` to read):
`OUTREACH_ADMIN_PASSWORD`, `OUTREACH_BASE_URL`, `OUTREACH_WEBHOOK_SECRET`, `OUTREACH_SECRET_KEY` and anything else in it.

---

## 2. Get a server

Any small Linux machine works (1 CPU and 1 GB of memory is enough for a team). The scripts in `deploy/` are written
for **Ubuntu 24.04** (Oracle Cloud Always Free works). You need:

- the server's IP address and SSH access (`ubuntu@IP`) with your key;
- a web address that points at the server: your own domain (`outreach.yourcompany.in`, an A record to the IP), or a
  free `sslip.io` name made from the IP (`1-2-3-4.sslip.io` for 1.2.3.4);
- ports 80 and 443 open in the cloud provider's firewall (the setup script opens them on the machine itself).

---

## 3a. Install with `deploy/deploy.sh` (Ubuntu, easiest)

On your own computer, in the project folder (needs Node.js 18+ to build the web app, and `ssh`/`scp`):

    SSH_KEY=~/.ssh/your_key deploy/deploy.sh ubuntu@1.2.3.4 outreach.yourcompany.in

This builds the web app, copies the code (never your data or secrets), and the first time runs `setup_server.sh`, which:

- installs Python, Caddy (automatic HTTPS certificates) and cron;
- creates the `outreach` user and the folders `/opt/outreach/app`, `/var/lib/outreach` (data) and `/var/backups/outreach`;
- writes `/etc/outreach/outreach.env` with a **new random admin password** and secrets;
- starts the `outreach` service (one uvicorn worker on 127.0.0.1:8000) and Caddy in front of it;
- adds a nightly copy of the data file (kept 14 days).

Read the new password with `sudo cat /etc/outreach/outreach.env`. To keep the **old** password or secrets, edit that
file (`sudo nano /etc/outreach/outreach.env`), put the old values in, and run `sudo systemctl restart outreach`.
Then go to step 4.

## 3b. Install by hand (any Linux)

1. Install Python 3.10 or newer, `python3-venv`, Node.js 18+ and [Caddy](https://caddyserver.com/docs/install).
2. Put the code in `/opt/outreach/app` (git clone or copy), then:

       cd /opt/outreach/app
       python3 -m venv .venv
       .venv/bin/pip install -r requirements.txt
       (cd web && npm ci && npm run build)          # makes web/dist

3. Create a user and folders: `sudo useradd --system --home /opt/outreach --shell /usr/sbin/nologin outreach`,
   `sudo mkdir -p /var/lib/outreach /var/backups/outreach /etc/outreach`, `sudo chown -R outreach:outreach /opt/outreach /var/lib/outreach /var/backups/outreach`.
4. Create `/etc/outreach/outreach.env` (owner root, `chmod 600`):

       OUTREACH_ADMIN_PASSWORD=choose-a-long-password
       OUTREACH_DATA_FILE=/var/lib/outreach/store.json
       OUTREACH_BASE_URL=https://outreach.yourcompany.in
       OUTREACH_WEBHOOK_SECRET=a-long-random-string
       OUTREACH_AUTORUN=1
       # optional: OUTREACH_COMMIT=<git commit>   (written into backups so you know which version made them)

   Without `OUTREACH_ADMIN_PASSWORD` the app refuses every visitor that is not on the server itself.
5. Copy `deploy/outreach.service` to `/etc/systemd/system/`, then `sudo systemctl daemon-reload && sudo systemctl enable --now outreach`.
   Keep **one worker**: the data file and the background timer must live in one process.
6. Make the Caddy config from the template: replace `__HOST__` in `deploy/Caddyfile.template` with your web address,
   save it as `/etc/caddy/Caddyfile` and run `sudo systemctl reload caddy`. Caddy gets the HTTPS certificate by itself,
   sends `/api`, `/u` and `/demo` to the app and serves the web app from `web/dist`.
7. Check: `curl -s http://127.0.0.1:8000/api/auth/status` answers, and `https://your-address` shows the sign-in page.

---

## 4. Put your data in

**From a backup file, in the browser (easiest).** Sign in on the new server, open **Setup → Backup & restore**, choose the
file and click **Check this file**. You see how many leads, messages, tasks and so on there are now and after the
restore. Nothing changes yet. Click **Replace everything with this backup** to restore.

- The data that was there is kept on the server as `store.json.pre-restore-<time>` next to the data file.
- **Real sending (email, WhatsApp, SMS) is switched off** after every restore.
- A backup made with keys can also bring the old server secret: tick the box only when this is the same install
  moving house. Normally the new server keeps its own (it only signs short-lived calendar sign-in links).

**From a backup file, on the command line.** Stop the app first, because a running app keeps its data in memory and
would write over the restored file:

    scp outreach-backup.json.gz ubuntu@NEW-SERVER:/tmp/
    sudo systemctl stop outreach
    sudo -u outreach /opt/outreach/app/.venv/bin/python /opt/outreach/app/scripts/backup.py \
        --data-file /var/lib/outreach/store.json restore --file /tmp/outreach-backup.json.gz      # dry run: shows the counts
    sudo -u outreach /opt/outreach/app/.venv/bin/python /opt/outreach/app/scripts/backup.py \
        --data-file /var/lib/outreach/store.json restore --file /tmp/outreach-backup.json.gz --confirm
    sudo systemctl start outreach

(`sudo -u outreach` may not be able to read files in your home folder; `/tmp` works.)

**From a copied `store.json`.**

    sudo systemctl stop outreach
    sudo cp store.json /var/lib/outreach/store.json
    sudo chown outreach:outreach /var/lib/outreach/store.json && sudo chmod 600 /var/lib/outreach/store.json
    sudo systemctl start outreach

A copied file keeps "send for real" as it was on the old server. Open the Outbox page right away and check it.

---

## 5. Reconnect and check

1. **Setup → Connections → The basics:** the public web address must be the new one (`https://outreach.yourcompany.in`).
   Unsubscribe and demo links in emails use it.
2. **Integrations:** if the backup had no keys, enter each connection again and press **Test**.
3. Webhook addresses and sign-in return addresses contain the server's address. If the address changed, paste the new
   ones from the Integrations page into each provider (Resend / SendGrid / Postmark events and inbound mail, WhatsApp,
   SMS, Cal.com, Calendly) and update the redirect address of your Google / Microsoft / Zoom calendar app.
4. The product's activity events: give your tech team the new address; the secret is `OUTREACH_WEBHOOK_SECRET` or the
   one under Setup (enter it again if the backup had no secrets).
5. Open **Setup → Go-live checklist** and fix anything it lists.

## 6. Switch sending on (only one server!)

1. Stop the old server for good: `sudo systemctl disable --now outreach` on the **old** machine.
2. On the new server, open the **Outbox** page, tick **Send for real** and save. WhatsApp and SMS have their own
   switches on their Integrations tabs.
3. Send one test from Setup → Connections (sending domain → Test connection) to your own address.

---

## Running it on a laptop (Windows, Mac or Linux)

Good for trying things, looking at your data or keeping a copy. Real sending needs a public `https` address for the
unsubscribe links, so a laptop normally stays in practice mode. Background sending only runs while the laptop is
awake and `OUTREACH_AUTORUN=1` is set.

1. Install **Python 3.10+** (python.org; on Windows tick "Add python.exe to PATH") and **Node.js 18 or newer** (nodejs.org, LTS).
2. Get the code (download the ZIP and unpack it, or `git clone`), and open a terminal in that folder.
3. Install the Python parts:
   - Windows (PowerShell): `py -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt`
   - Mac / Linux: `python3 -m venv .venv` then `.venv/bin/pip install -r requirements.txt`
4. Start the app (leave this window open):
   - Windows: `.venv\Scripts\python -m uvicorn app.main:app --port 8000`
   - Mac / Linux: `.venv/bin/python -m uvicorn app.main:app --port 8000`
5. In a second terminal: `cd web`, `npm ci`, `npm run dev`, then open <http://localhost:5173>.
6. To load your data: **Setup → Backup & restore** in the browser, or stop the app (Ctrl+C) and run
   `python scripts/backup.py restore --file outreach-backup.json.gz --confirm` with the venv's Python, then start it again.

Data is saved in `data/store.json`. To keep it somewhere else, set `OUTREACH_DATA_FILE` before starting:
Windows PowerShell `$env:OUTREACH_DATA_FILE="C:\outreach\store.json"`, Mac / Linux `export OUTREACH_DATA_FILE=~/outreach/store.json`.
Without `OUTREACH_ADMIN_PASSWORD` the app only answers on this computer, which is what you want on a laptop.

---

## Keeping backups

- The server keeps a nightly copy of the data file for 14 days in `/var/backups/outreach` (with secrets). Copy one off
  the server now and then; a disk failure takes those copies with it.
- Download a backup from **Setup → Backup & restore** every week or before big changes (imports, merges, restores).
- The app also keeps its last five saved versions next to the data file (`store.json.1` is the newest), at most one a minute.

## If something goes wrong

- **"This file is not an Outreach OS backup"**: the file is not one made by Download backup or `scripts/backup.py`.
  A raw `store.json` is copied into place instead (step 4, last part).
- **"made by a newer version"**: update the app on this server first, then restore.
- **"Emails are being sent right now"**: a send run was going; wait a minute and try again.
- **Undo a restore**: stop the app, copy `store.json.pre-restore-<time>` back over `store.json` (keep owner `outreach`,
  `chmod 600`), start the app.
- **"Data file … is corrupt"** when starting: the app never overwrites it. Copy `store.json.1` (or a nightly copy) over
  it and start again.
