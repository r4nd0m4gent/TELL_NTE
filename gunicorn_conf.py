# gunicorn_conf.py
import os

bind = "0.0.0.0:443"

certfile = os.environ.get("SSL_CERTFILE", "/etc/ssl/tell/fullchain.pem")
keyfile  = os.environ.get("SSL_KEYFILE",  "/etc/ssl/tell/privkey.pem")

worker_class = "gevent"
workers      = 1
threads      = 4
worker_connections = 100

accesslog = "/var/log/tell_nte/access.log"
errorlog  = "/var/log/tell_nte/error.log"
loglevel  = "info"

pidfile = "/var/run/tell_nte.pid"
daemon  = False
