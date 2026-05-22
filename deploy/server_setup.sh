#!/usr/bin/env bash
# server_setup.sh – run as root on the Digital Ocean droplet after files are uploaded.
set -euo pipefail

APP_DIR=/home/tell/app
DATA_DIR=/home/tell/data

echo "── 1. Installing system packages ────────────────────────────────────────"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx

echo "── 2. Creating app user ─────────────────────────────────────────────────"
id -u tell &>/dev/null || useradd -m -s /bin/bash tell
usermod -aG www-data tell

echo "── 3. Fixing permissions ─────────────────────────────────────────────────"
chown -R tell:tell "${APP_DIR}" "${DATA_DIR}"

echo "── 4. Python virtual environment & dependencies ──────────────────────────"
sudo -u tell python3 -m venv "${APP_DIR}/venv"
sudo -u tell "${APP_DIR}/venv/bin/pip" install --upgrade pip --quiet
sudo -u tell "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt" --quiet

echo "── 5. Installing systemd services ───────────────────────────────────────"
cp "${APP_DIR}/deploy/tell-dashboard.service" /etc/systemd/system/
cp "${APP_DIR}/deploy/tell-form.service"      /etc/systemd/system/
systemctl daemon-reload
systemctl enable  tell-dashboard tell-form
systemctl restart tell-dashboard tell-form

echo "── 6. Configuring nginx ─────────────────────────────────────────────────"
cp "${APP_DIR}/deploy/nginx_tell.conf" /etc/nginx/sites-available/tell
ln -sf /etc/nginx/sites-available/tell /etc/nginx/sites-enabled/tell
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

echo ""
echo "✓ Deployment complete!"
echo "  Dashboard  →  http://165.232.86.238/"
echo "  Form       →  http://165.232.86.238/contribute/"
