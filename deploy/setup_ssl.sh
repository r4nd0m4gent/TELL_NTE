#!/usr/bin/env bash
# setup_ssl.sh — issue/renew the Let's Encrypt certificate for tell.newtexeco.nl
# and make renewal fully automatic. Safe to re-run at any time.
# Run on the SERVER as root (or with sudo):
#     sudo bash /home/tell/app/deploy/setup_ssl.sh
#
# How renewal works after this script:
#   - certbot uses the "webroot" method: it writes a token to /var/www/certbot,
#     which Nginx serves on port 80 (see nginx_tell.conf). No downtime needed.
#   - certbot's systemd timer (or cron) runs `certbot renew` twice a day and
#     renews when the cert is < 30 days from expiry.
#   - A deploy hook reloads Nginx after each renewal so it serves the new cert.
set -euo pipefail

DOMAIN=tell.newtexeco.nl
WEBROOT=/var/www/certbot
APP_DIR=/home/tell/app
HOOK=/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh

echo "▶ Installing certbot (if missing)…"
if ! command -v certbot >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y certbot
fi
certbot --version

echo "▶ Preparing ACME webroot ${WEBROOT}…"
mkdir -p "${WEBROOT}/.well-known/acme-challenge"
chown -R www-data:www-data "${WEBROOT}"

echo "▶ Installing Nginx config (serves the ACME challenge on port 80)…"
cp "${APP_DIR}/deploy/nginx_tell.conf" /etc/nginx/sites-available/tell
ln -sf /etc/nginx/sites-available/tell /etc/nginx/sites-enabled/tell
nginx -t
systemctl reload nginx

echo "▶ Checking the challenge path is reachable over HTTP…"
echo ok > "${WEBROOT}/.well-known/acme-challenge/selftest"
if curl -fsS -m 10 "http://${DOMAIN}/.well-known/acme-challenge/selftest" | grep -q ok; then
    echo "  reachable ✓"
else
    echo "  ✗ http://${DOMAIN}/.well-known/acme-challenge/selftest is not reachable."
    echo "    Check that port 80 is open (ufw / DigitalOcean cloud firewall)."
    rm -f "${WEBROOT}/.well-known/acme-challenge/selftest"
    exit 1
fi
rm -f "${WEBROOT}/.well-known/acme-challenge/selftest"

echo "▶ Installing deploy hook (reload Nginx after every renewal)…"
mkdir -p "$(dirname "${HOOK}")"
cat > "${HOOK}" <<'EOF'
#!/bin/sh
systemctl reload nginx
EOF
chmod 755 "${HOOK}"

echo "▶ Requesting certificate (webroot method)…"
# --cert-name keeps the existing lineage, so the paths in nginx_tell.conf
# (/etc/letsencrypt/live/${DOMAIN}/...) stay valid. Re-running certonly also
# rewrites the lineage's renewal config to use webroot from now on.
EMAIL_ARGS=(--register-unsafely-without-email)
if ls /etc/letsencrypt/accounts/*/directory/* >/dev/null 2>&1; then
    EMAIL_ARGS=()   # an ACME account already exists; reuse it
fi
certbot certonly --webroot -w "${WEBROOT}" \
    --cert-name "${DOMAIN}" -d "${DOMAIN}" \
    --non-interactive --agree-tos --keep-until-expiring \
    "${EMAIL_ARGS[@]}"

systemctl reload nginx

echo "▶ Enabling automatic renewal…"
if systemctl list-unit-files | grep -q '^certbot.timer'; then
    systemctl enable --now certbot.timer
elif systemctl list-unit-files | grep -q '^snap.certbot.renew.timer'; then
    systemctl enable --now snap.certbot.renew.timer
else
    echo '0 3,15 * * * root certbot renew --quiet' > /etc/cron.d/certbot-renew
    echo "  (no certbot timer found — installed /etc/cron.d/certbot-renew)"
fi
systemctl list-timers --all | grep -i certbot || true

echo "▶ Dry-run renewal (proves the automatic renewal will succeed)…"
certbot renew --dry-run

echo "▶ Certificate now served by Nginx:"
echo | openssl s_client -servername "${DOMAIN}" -connect "${DOMAIN}:443" 2>/dev/null \
    | openssl x509 -noout -issuer -dates

echo "✓ SSL set up. Renewal is automatic; nothing more to do."
