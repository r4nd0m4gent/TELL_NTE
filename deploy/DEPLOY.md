# Deployment Guide — tell.newtexeco.nl (HTTPS, no reverse proxy)

## What changed in the code

| File | Change |
|---|---|
| `textile_companies_NL.py` | `app.run()` now reads `SSL_CERTFILE` / `SSL_KEYFILE` from env and binds `0.0.0.0:443` when certs are present |
| `contribution_form.py` | No functional change; `requests_pathname_prefix='/contribute/'` already correct |
| `wsgi.py` | **New** — single Gunicorn entry point, mounts both apps via `DispatcherMiddleware` |
| `deploy/gunicorn_conf.py` | **New** — Gunicorn config (TLS, workers, logs) |
| `deploy/tell_nte.service` | **New** — systemd unit file |
| `requirements.txt` | Added `pyopenssl>=24.0.0` (needed for Gunicorn TLS) |

---

## Step 1 — SSH into your Droplet

```bash
ssh root@<your-droplet-ip>
```

---

## Step 2 — Get an SSL certificate (Let's Encrypt)

If you don't have a cert yet, install Certbot and issue one. Your DNS A-record for
`tell.newtexeco.nl` must already point to the Droplet IP.

```bash
apt update && apt install -y certbot
# Stop anything currently running on :80 or :443 briefly
certbot certonly --standalone -d tell.newtexeco.nl
```

Certbot writes the cert to:
- `/etc/letsencrypt/live/tell.newtexeco.nl/fullchain.pem`
- `/etc/letsencrypt/live/tell.newtexeco.nl/privkey.pem`

Create the directory the service expects and symlink (or copy) the files:

```bash
mkdir -p /etc/ssl/tell
ln -sf /etc/letsencrypt/live/tell.newtexeco.nl/fullchain.pem /etc/ssl/tell/fullchain.pem
ln -sf /etc/letsencrypt/live/tell.newtexeco.nl/privkey.pem   /etc/ssl/tell/privkey.pem
```

> **Already have a cert?** Just copy your `fullchain.pem` and `privkey.pem` to `/etc/ssl/tell/`.

---

## Step 3 — Pull / update the code

```bash
cd /opt/tell_nte          # or wherever the repo lives on the server
git pull origin main
```

If this is a fresh deployment:

```bash
git clone https://github.com/r4nd0m4gent/TELL_NTE.git /opt/tell_nte
cd /opt/tell_nte
```

---

## Step 4 — Set up the Python environment

```bash
cd /opt/tell_nte
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install gevent          # required by gunicorn worker_class = "gevent"
pip install -r requirements.txt
deactivate
```

---

## Step 5 — Create / verify the `.env` file

```bash
cat /opt/tell_nte/.env
```

It should contain at minimum your database credentials, e.g.:

```
DATABASE_URL=postgresql://user:password@localhost:5432/tell_nte
SSL_CERTFILE=/etc/ssl/tell/fullchain.pem
SSL_KEYFILE=/etc/ssl/tell/privkey.pem
```

---

## Step 6 — Create the log directory

```bash
mkdir -p /var/log/tell_nte
```

---

## Step 7 — Install and start the systemd service

```bash
cp /opt/tell_nte/deploy/tell_nte.service /etc/systemd/system/tell_nte.service
systemctl daemon-reload
systemctl enable tell_nte
systemctl start tell_nte
systemctl status tell_nte
```

The app is now live at **https://tell.newtexeco.nl** (main dashboard) and
**https://tell.newtexeco.nl/contribute/** (contribution form).

---

## Step 8 — Auto-renew the certificate

Let's Encrypt certs expire after 90 days. Add a cron job to renew and restart Gunicorn:

```bash
crontab -e
```

Add this line:

```
0 3 * * * certbot renew --quiet && systemctl restart tell_nte
```

---

## Troubleshooting

| Problem | Command |
|---|---|
| View live logs | `journalctl -u tell_nte -f` |
| View Gunicorn error log | `tail -f /var/log/tell_nte/error.log` |
| Restart after code change | `git pull && systemctl restart tell_nte` |
| Port 443 already in use | `lsof -i :443` — kill the conflicting process |
| Certificate not found | Check symlinks in `/etc/ssl/tell/` |
