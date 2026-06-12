# gunicorn_conf.py
# Gunicorn configuration for tell.newtexeco.nl
# Place this file in the project root alongside textile_companies_NL.py

import multiprocessing
import os

# ── Binding ───────────────────────────────────────────────────────────────────
# Gunicorn handles TLS directly; bind to 0.0.0.0:443 so the OS terminates SSL.
# You must run gunicorn as root (or use authbind / CAP_NET_BIND_SERVICE) to
# bind to port 443.
bind = "0.0.0.0:443"

# ── TLS / SSL ─────────────────────────────────────────────────────────────────
# Paths are read from the environment; fall back to standard Let's Encrypt paths.
certfile = os.environ.get("SSL_CERTFILE", "/etc/ssl/tell/fullchain.pem")
keyfile  = os.environ.get("SSL_KEYFILE",  "/etc/ssl/tell/privkey.pem")

# ── Workers ───────────────────────────────────────────────────────────────────
# Dash/Plotly uses long-polling; gevent workers handle many concurrent clients.
worker_class = "gevent"
workers      = 1        # Dash stores are in-process; keep at 1 unless you add Redis
threads      = 4
worker_connections = 100

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog = "/var/log/tell_nte/access.log"
errorlog  = "/var/log/tell_nte/error.log"
loglevel  = "info"

# ── Process ───────────────────────────────────────────────────────────────────
pidfile   = "/var/run/tell_nte.pid"
daemon    = False   # systemd manages the process; keep False
