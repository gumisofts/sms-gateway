#!/bin/sh
# Container entrypoint for the web app: prepare the database and static files,
# then start the ASGI server (or whatever command was passed).
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

if [ "$#" -gt 0 ]; then
    exec "$@"
fi
exec daphne -b 0.0.0.0 -p "${PORT:-8000}" core.asgi:app
