# gunicorn.conf.py

import multiprocessing

# Bind to all interfaces on port 8000 (adjust as needed)
bind = "unix:/run/app.sock"

# Worker class (sync is default; you can switch to "gevent" or "uvicorn.workers.UvicornWorker" for ASGI apps)
worker_class = "uvicorn.workers.UvicornWorker"

# Recommended formula: (2 x $num_cores) + 1
workers = multiprocessing.cpu_count() * 2 + 1

# Number of threads per worker (good for blocking apps, optional)

# Max pending connections
backlog = 2048

# Restart workers after handling a certain number of requests (helps avoid memory leaks)
max_requests = 1000
max_requests_jitter = 100

# Daemonize (run in background) - usually managed by systemd/supervisord instead
daemon = False

# Logging
accesslog = "access.log"
errorlog = "error.log"
loglevel = "info"

# Timeout for workers (in seconds)
timeout = 30

# Graceful timeout before force kill
graceful_timeout = 30

# Keep-alive for connections
keepalive = 5
