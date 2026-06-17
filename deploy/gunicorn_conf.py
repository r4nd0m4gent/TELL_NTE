# gunicorn_conf.py
# No SSL here — Nginx handles HTTPS and proxies to this port
import os

bind         = "127.0.0.1:8050"
worker_class = "gevent"
workers      = 1
threads      = 4
worker_connections = 100

accesslog = "/var/log/tell_nte/access.log"
errorlog  = "/var/log/tell_nte/error.log"
loglevel  = "info"

pidfile = "/var/run/tell_nte.pid"
daemon  = False
