#!/usr/bin/env bash
# redeploy.sh — routine update deploy (NOT first-time provisioning).
# Run on the SERVER as root (or with sudo):
#     sudo bash /home/tell/app/deploy/redeploy.sh
#
# It pulls the latest code (which also updates the static pages in site/,
# served by Nginx straight from the checkout), refreshes dependencies, updates
# the Nginx config, and restarts the dashboard + contribution-form services.
# Otherwise, run this command to commit and push first:
#   cd "c:/Users/fsollit/TELL" && git commit -m "Update" && git push origin main 2>&1 | tail -3
set -euo pipefail

APP_USER=tell
APP_DIR=/home/${APP_USER}/app

echo "▶ Pulling latest code…"
git -C "${APP_DIR}" pull --ff-only origin main
chown -R ${APP_USER}:${APP_USER} "${APP_DIR}"

echo "▶ Updating Python dependencies…"
sudo -u ${APP_USER} "${APP_DIR}/venv/bin/pip" install -q -r "${APP_DIR}/requirements.txt"

echo "▶ Updating Nginx config…"
cp "${APP_DIR}/deploy/nginx_tell.conf" /etc/nginx/sites-available/tell
nginx -t && systemctl reload nginx

echo "▶ Restarting application services…"
systemctl restart tell_nte tell-form

echo "▶ Waiting ~30s for the dashboard to load + geocode at startup…"
sleep 30

echo "▶ Status:"
systemctl --no-pager --lines=0 status tell_nte tell-form || true
echo "▶ Listening sockets (expect 8050 + 8051):"
ss -ltnp | grep -E '8050|8051' || echo "  (nothing listening yet — check the logs)"
echo "▶ Memory:"
free -m

echo "✓ Redeploy complete. Hard-refresh the browser (Ctrl+F5)."
