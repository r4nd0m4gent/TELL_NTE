# gunicorn_conf.py
# Nginx handles SSL. Gunicorn serves plain HTTP on localhost:8050.
import multiprocessing

bind         = '127.0.0.1:8050'
workers      = multiprocessing.cpu_count() * 2 + 1
worker_class = 'gthread'   # thread-based, no extra deps, stable for Dash
threads      = 4
timeout      = 120
keepalive    = 5

accesslog = '/var/log/tell_nte/access.log'
errorlog  = '/var/log/tell_nte/error.log'
loglevel  = 'info'
daemon    = False
