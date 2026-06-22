# gunicorn_conf.py
# Nginx handles SSL. Gunicorn serves plain HTTP on localhost:8050.
import multiprocessing

bind         = '127.0.0.1:8050'
# Small fixed worker count: this droplet has ~1 GB RAM and each worker holds
# the full dataset + Plotly in memory. cpu_count()*2+1 spawned too many workers
# and triggered OOM kills. With gthread + threads, 2 workers give ample
# concurrency for a dashboard.
workers      = 2
worker_class = 'gthread'   # thread-based, no extra deps, stable for Dash
threads      = 4
timeout      = 120
keepalive    = 5
# Load the app once in the master process, then fork workers. The read-only
# dataset is shared via copy-on-write instead of duplicated per worker, which
# substantially lowers total memory use.
preload_app  = True

accesslog = '/var/log/tell_nte/access.log'
errorlog  = '/var/log/tell_nte/error.log'
loglevel  = 'info'
daemon    = False
