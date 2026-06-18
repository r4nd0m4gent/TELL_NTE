# Deployment Guide — tell.newtexeco.nl

## Architecture
- **Nginx** — terminates HTTPS on port 443, serves `tell.html` at `/`, proxies Dash at `/dashboard/`
- **Gunicorn** — runs plain HTTP on `127.0.0.1:8050`, no SSL
- **Dash app** — served at `https://tell.newtexeco.nl/dashboard/`
- **tell.html** — standalone landing page with header/intro/footer, embeds dashboard in iframe

## Data file
Place `companies.xlsx` in the `data/` folder next to the script:
- Local:  `C:\Users\fsollit\Desktop\Data\TELL\data\companies.xlsx`
- Server: `/home/tell/app/data/companies.xlsx`

## Deploy steps

### 1. Push code from VS Code
```
git add .
git commit -m "Production rewrite"
git push origin main
```

### 2. On the server — pull code
```bash
cd /home/tell/app
git pull origin main
```

### 3. Install dependencies
```bash
source venv/bin/activate
pip install -r requirements.txt
deactivate
```

### 4. Fix log folder ownership
```bash
mkdir -p /var/log/tell_nte
chown -R tell:tell /var/log/tell_nte
```

### 5. Update Nginx config
```bash
cp /home/tell/app/deploy/nginx_tell.conf /etc/nginx/sites-available/tell
nginx -t
systemctl reload nginx
```

### 6. Update and restart service
```bash
cp /home/tell/app/deploy/tell_nte.service /etc/systemd/system/tell_nte.service
systemctl daemon-reload
systemctl restart tell_nte
systemctl status tell_nte
```

### 7. Verify
- `https://tell.newtexeco.nl` → shows tell.html with header/intro/footer
- `https://tell.newtexeco.nl/dashboard/` → shows Dash app with working map
- `https://tell.newtexeco.nl/contribute/` → shows contribution form

## After code changes
```bash
cd /home/tell/app && git pull origin main && systemctl restart tell_nte
```

## Logs
```bash
journalctl -u tell_nte -f
tail -f /var/log/tell_nte/error.log
```
