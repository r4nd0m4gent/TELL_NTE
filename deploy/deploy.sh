#!/usr/bin/env bash
# deploy.sh – run this on a fresh Ubuntu 22.04 / 24.04 Digital Ocean Droplet
# as root (or with sudo). Replace the placeholders marked with <...>.
set -euo pipefail

# ── Config – edit these ───────────────────────────────────────────────────────
APP_USER=tell
APP_DIR=/home/${APP_USER}/app
DATA_DIR=/home/${APP_USER}/data
REPO_URL="<YOUR_GIT_REPO_URL>"        # e.g. https://github.com/yourname/TELL.git
DATA_FILE="<PATH_TO_KvK_textile.xlsx>" # local path on your machine; upload separately

# ── 1. System packages ────────────────────────────────────────────────────────
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx git

# ── 2. Create app user ────────────────────────────────────────────────────────
id -u ${APP_USER} &>/dev/null || useradd -m -s /bin/bash ${APP_USER}
usermod -aG www-data ${APP_USER}

# ── 3. Clone / update repository ─────────────────────────────────────────────
if [ -d "${APP_DIR}/.git" ]; then
    git -C "${APP_DIR}" pull
else
    git clone "${REPO_URL}" "${APP_DIR}"
fi
chown -R ${APP_USER}:${APP_USER} "${APP_DIR}"

# ── 4. Python virtual environment & dependencies ──────────────────────────────
sudo -u ${APP_USER} python3 -m venv "${APP_DIR}/venv"
sudo -u ${APP_USER} "${APP_DIR}/venv/bin/pip" install --upgrade pip
sudo -u ${APP_USER} "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

# ── 5. Data file ──────────────────────────────────────────────────────────────
# Upload the Excel file to the server first, e.g.:
#   scp KvK\ textile.xlsx root@YOUR_IP:/home/tell/data/KvK_textile.xlsx
mkdir -p "${DATA_DIR}"
chown ${APP_USER}:${APP_USER} "${DATA_DIR}"

# ── 6. Swap (1GB droplets OOM-kill workers without it) ────────────────────────
if ! swapon --show | grep -q '/swapfile'; then
    dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ── 7. systemd services ───────────────────────────────────────────────────────
# tell_nte.service runs the dashboard (gunicorn --config deploy/gunicorn_conf.py).
cp "${APP_DIR}/deploy/tell_nte.service"  /etc/systemd/system/
cp "${APP_DIR}/deploy/tell-form.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable  tell_nte tell-form
systemctl restart tell_nte tell-form

# ── 8. Landing page (Nginx serves it from ${APP_DIR}/tell_embed.html) ─────────
cp "${APP_DIR}/deploy/tell_embed.html" "${APP_DIR}/tell_embed.html"
chown ${APP_USER}:${APP_USER} "${APP_DIR}/tell_embed.html"

# ── 9. nginx ──────────────────────────────────────────────────────────────────
cp "${APP_DIR}/deploy/nginx_tell.conf" /etc/nginx/sites-available/tell
ln -sf /etc/nginx/sites-available/tell /etc/nginx/sites-enabled/tell
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl restart nginx

echo ""
echo "✓ Deployment complete."
echo "  Dashboard  →  http://YOUR_DOMAIN_OR_IP/"
echo "  Form       →  http://YOUR_DOMAIN_OR_IP/contribute/"
echo ""
echo "Next steps:"
echo "  1. Edit deploy/nginx_tell.conf  – replace YOUR_DOMAIN_OR_IP"
echo "  2. Upload the Excel data file:"
echo "     scp 'KvK textile.xlsx' ${APP_USER}@YOUR_IP:${DATA_DIR}/KvK_textile.xlsx"
echo "  3. (Optional) Add a free TLS certificate: sudo certbot --nginx"
