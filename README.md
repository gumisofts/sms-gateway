# SMS Gateway

Android phones act as SMS gateway nodes; a backend dispatches outbound SMS jobs across many nodes.

| Path | Contents |
|------|----------|
| [`backend/`](backend/) | Django backend: REST API, node WebSockets, dispatcher, Celery (see [backend/README.md](backend/README.md)) |
| [`android/`](android/) | Kotlin Android gateway app (see [android/README.md](android/README.md)) |
| [`docs/PROTOCOL.md`](docs/PROTOCOL.md) | Node ⇄ backend protocol (REST registration + WebSocket) |
| [`docs/DEVICE_SETUP.md`](docs/DEVICE_SETUP.md) | Phone provisioning guide (adb scripts in `scripts/`, SMS limit, OEM battery settings, ramp-up) |

## Quick start

Backend (from `backend/`):

```sh
poetry install
cp .env.example .env            # set DJANGO_DEBUG=True, DJANGO_ALLOWED_HOSTS=<your LAN IP>
poetry run python manage.py migrate
poetry run daphne -b 0.0.0.0 -p 8000 core.asgi:app
```

Android (from `android/`): put `sdk.dir=<path to Android SDK>` in `local.properties`, then
`./gradlew assembleDebug` (`gradlew.bat` on Windows) and install
`app/build/outputs/apk/debug/app-debug.apk` with `adb install -r`.

## Deployment

The backend is deployed on the Gisila panel (gisila-s1) from `backend/` on `main`; every push to
`main` redeploys it (web app + Celery worker/beat).
