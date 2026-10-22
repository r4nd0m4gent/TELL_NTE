#!/usr/bin/env bash
# setup_dev.sh — publish (or remove) the dev preview at https://tell.newtexeco.nl/dev/
# Run on the SERVER as root:
#     sudo bash /home/tell/app-dev/deploy/setup_dev.sh            # set up / refresh
#     sudo bash /home/tell/app-dev/deploy/setup_dev.sh --remove   # take it down
#
# Expects, prepared as user `tell`:
#   /home/tell/app-dev          checkout of the feature branch
#   /home/tell/app-dev/data     copy of the app's data folder
#   /home/tell/app-dev/db/mysql/.env  -> /home/tell/app/db/mysql/.env (database)
#   /home/tell/dev-www/dev      -> /home/tell/app-dev/site
#
# Production is not touched apart from one `include` line in its Nginx site,
# which is backed up first and restored if the new config does not pass
# `nginx -t`. Note: deploy/redeploy.sh copies the production config over the
# site file, which drops that line again; rerun this script after a redeploy.
set -euo pipefail

DEV_DIR=/home/tell/app-dev
SITE=/etc/nginx/sites-available/tell
SNIPPET=/etc/nginx/snippets/tell_dev.conf
UNIT=/etc/systemd/system/tell_dev.service
INCLUDE="    include ${SNIPPET};   # dev preview at /dev/ (deploy/setup_dev.sh)"

if [[ "${1:-}" == "--remove" ]]; then
    echo "▶ Removing the dev preview…"
    systemctl disable --now tell_dev 2>/dev/null || true
    rm -f "${UNIT}"; systemctl daemon-reload
    sed -i "\#include ${SNIPPET};#d" "${SITE}"
    rm -f "${SNIPPET}"
    nginx -t && systemctl reload nginx
    echo "✓ Dev preview removed (the checkout in ${DEV_DIR} is left in place)."
    exit 0
fi

for p in "${DEV_DIR}/site/index.html" "${DEV_DIR}/data" "${DEV_DIR}/db/mysql/.env" /home/tell/dev-www/dev; do
    [[ -e "$p" ]] || { echo "✗ Missing ${p}: prepare the dev checkout first."; exit 1; }
done

echo "▶ Installing the dev dashboard service (127.0.0.1:8052)…"
cp "${DEV_DIR}/deploy/tell_dev.service" "${UNIT}"
systemctl daemon-reload
systemctl enable tell_dev
systemctl restart tell_dev

echo "▶ Adding /dev/ to Nginx…"
mkdir -p "$(dirname "${SNIPPET}")"
cp "${DEV_DIR}/deploy/nginx_tell_dev.conf" "${SNIPPET}"
if ! grep -q "include ${SNIPPET};" "${SITE}"; then
    BACKUP="${SITE}.bak-$(date +%Y%m%d-%H%M%S)"
    cp "${SITE}" "${BACKUP}"
    # Into the HTTPS server block: right after its ssl_ciphers line (the HTTP
    # block only redirects and has none)
    sed -i "/^\s*ssl_ciphers/a\\${INCLUDE}" "${SITE}"
    if ! nginx -t; then
        echo "✗ Nginx config test failed; restoring ${BACKUP}"
        cp "${BACKUP}" "${SITE}"
        exit 1
    fi
fi
nginx -t && systemctl reload nginx

echo "▶ Waiting ~40s for the dev dashboard to load its data…"
sleep 40
systemctl --no-pager --lines=5 status tell_dev || true
echo "▶ Memory:"; free -m
echo "▶ Checks (expect 200):"
for path in /dev/ /dev/network/ /dev/dashboard/; do
    printf "  %-18s %s\n" "${path}" "$(curl -s -o /dev/null -w '%{http_code}' "https://tell.newtexeco.nl${path}")"
done
echo "✓ Dev preview: https://tell.newtexeco.nl/dev/"
