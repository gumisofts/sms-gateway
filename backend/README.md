# SMS Gateway backend

Django backend for an SMS gateway whose "nodes" are Android phones (the app lives in
`../android`). Clients call a REST API to send SMS; the backend picks an online node and
pushes the job to it over the node's WebSocket. The node/backend contract is
[`../docs/PROTOCOL.md`](../docs/PROTOCOL.md); this backend implements its server side.

Stack: Django 5.2, DRF, Channels (Daphne), Celery + Redis, Postgres (SQLite for dev),
drf-spectacular (Swagger UI at `/`).

## Architecture

```
  client (JWT or X-API-Key)
        |  POST /api/messages/
        v
   REST (DRF) --> DB (SmsMessage: pending)
        |             ^   ^
        | on_commit   |   | status / ack / heartbeat
        v             |   |
   dispatcher --------+   |        pick online, least-loaded node
   (messaging/dispatch.py)|
        |  group_send("node_<id>")
        v                |
   channel layer (Redis) |
        |                |
        v                |
   NodeConsumer  <== WebSocket /ws/nodes/<nodeId> ==>  Android node
   (nodes/consumers.py)  ---- ack / status_update / heartbeat ---->

   Celery beat: dispatch pending, re-dispatch unacked, expire pending, mark stale nodes offline
```

* **State lives in the DB and the channel layer**, never in process memory, so you can run
  several web processes. The in-memory channel layer is per process: with more than one
  process (web + celery worker, or multiple web workers) set `DJANGO_REDIS_URL`.
* **Apps**: `auth` (users, JWT), `nodes` (registration, WebSocket, node admin API),
  `messaging` (messages, API keys, dispatcher, Celery tasks, webhooks).
* **Message lifecycle**: `pending -> dispatched -> queued -> sending -> sent -> delivered`,
  or `failed` / `expired` / `cancelled`. Terminal states never change
  (`SmsMessage.apply_node_status` is the single place that decides).
* **Dispatch (per-SIM lanes)**: eligible nodes are active, connected, honour the requested
  node / SIM (`subscription_id` wins over `sim_slot`) and, if the node has an `owner`, only
  serve that owner's messages (nodes without an owner are a shared pool). Every present,
  enabled, unpaused SIM of those nodes is a lane with its own rate (parts/minute) and daily
  cap (parts/day, reset at local midnight); the least loaded lane with room wins (node
  `queueDepth` + unacked jobs, then the SIM's recent and daily usage), so dual-SIM phones use
  both SIMs. Usage is counted in SMS parts (`messaging/segments.py`, then the count the phone
  reports). Nodes that report no SIMs are one lane paced by their own `ratePerMinute`.
  No lane with room means the message stays `pending` until one frees up (next minute, next
  day, a phone connects), the beat task retries, or it expires.
* **SIM health**: a message's final failure counts against its SIM; `DJANGO_SIM_PAUSE_AFTER_FAILURES`
  failures in a row pause the SIM for `DJANGO_SIM_PAUSE_MINUTES` (likely carrier block), any
  sent/delivered message resets the streak. SIMs missing from a heartbeat are kept as
  "absent" so their settings survive.
* **Admin**: django-unfold theme with a dashboard at `/admin/` (today's totals, phones online,
  capacity left, paused SIMs, phones without the Android SMS limit raised, 24 h chart, per-SIM
  usage). SIM rate/cap/enabled are editable in the SIM list; actions resume/enable/disable
  SIMs, disable phones, cancel/retry messages, revoke API keys. API keys can be created in
  the admin (shown once).
* **Acks**: `accepted: false` sends the job to a different node (`invalid_job` fails it).
  Jobs not acked within `DJANGO_MESSAGE_ACK_TIMEOUT_SEC` are re-sent to the same node if it is
  still connected (ids are idempotent on the node) or moved to another node if it is offline.

## Local development

```bash
poetry install
cp .env.example .env            # edit: DJANGO_DEBUG=True, DJANGO_NODE_REGISTRATION_KEY=...
poetry run python manage.py migrate
poetry run python manage.py createsuperuser

# web + node WebSockets (ASGI)
poetry run daphne -b 0.0.0.0 -p 8000 core.asgi:app

# background jobs (separate terminals)
poetry run celery -A core worker -l info
poetry run celery -A core beat -l info
```

Without `DJANGO_POSTGRES_URL` SQLite is used; without `DJANGO_REDIS_URL` the in-memory
channel layer and a `memory://` Celery broker are used, which only work inside a single
process (fine for the web server alone: the dispatcher runs in the request, Celery only adds
the periodic safety nets, so run Redis if you want worker and beat).

Docker: `cp config/app.env.example config/app.env`, `cp config/pg.env.example config/pg.env`,
then `docker compose up --build` (app on :8880, plus pg, redis, celery worker and beat). The
entrypoint runs `migrate` and `collectstatic` before starting Daphne.

Tests: `poetry run python manage.py test` (SQLite and the in-memory channel layer; Celery is
eager). `select_for_update` is a no-op on SQLite, so the row-locking paths are only really
exercised on Postgres (`DJANGO_POSTGRES_URL=... manage.py test`, as in CI).

Formatting: `black`, `isort --profile black`, `flake8` (pre-commit is configured).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | random per start | Django secret (set it in production) |
| `DJANGO_DEBUG` | off | `True` enables debug and open node registration when no key is set |
| `DJANGO_ALLOWED_HOSTS` | `localhost` | comma separated |
| `DJANGO_CORS_ALLOWED_ORIGINS` | localhost:3000 | comma separated |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | unset | comma separated, e.g. `https://sms.example.com` (admin over https) |
| `DJANGO_BEHIND_PROXY` | off | `True` trusts `X-Forwarded-Proto` / `X-Forwarded-Host` from the reverse proxy |
| `DJANGO_POSTGRES_URL` | unset (SQLite) | `postgres://user:pass@host:5432/db` |
| `DJANGO_REDIS_URL` | unset (in-memory) | channel layer; also the default Celery broker |
| `DJANGO_CELERY_BROKER_URL` | `CELERY_BROKER_URL`, then Redis URL, then `memory://` | Celery broker |
| `DJANGO_CELERY_RESULT_BACKEND` | `CELERY_BACKEND_URL` or none | Celery result backend |
| `DJANGO_NODE_REGISTRATION_KEY` | unset | secret the app sends as `registrationKey`; unset = registration only in DEBUG |
| `DJANGO_NODE_HEARTBEAT_INTERVAL_SEC` | 30 | `heartbeatIntervalSec` sent in `config` on connect |
| `DJANGO_NODE_OFFLINE_AFTER_SEC` | 120 | no heartbeat for this long (or 3x the node's interval): marked offline |
| `DJANGO_MESSAGE_ACK_TIMEOUT_SEC` | 60 | dispatched but unacked: re-send / re-dispatch |
| `DJANGO_MESSAGE_PENDING_TTL_SEC` | 3600 | pending this long: `expired` |
| `DJANGO_MESSAGE_MAX_DISPATCH_ATTEMPTS` | 5 | then the message is `failed` |
| `DJANGO_MESSAGE_BULK_MAX` | 500 | max messages in `POST /api/messages/bulk/` |
| `DJANGO_TIME_ZONE` | `Africa/Addis_Ababa` | also the midnight boundary of per-SIM daily caps |
| `DJANGO_SIM_DEFAULT_RATE_PER_MINUTE` | 10 | SMS parts per minute per SIM (a SIM can override it in the admin; 0 = unlimited) |
| `DJANGO_SIM_DEFAULT_DAILY_LIMIT` | 1000 | SMS parts per day per SIM (override per SIM; 0 = unlimited) |
| `DJANGO_SIM_PAUSE_AFTER_FAILURES` | 5 | failed messages in a row that auto-pause a SIM (0 = never) |
| `DJANGO_SIM_PAUSE_MINUTES` | 60 | how long an auto-pause lasts (resume early from the admin) |
| `DJANGO_WEBHOOK_SECRET` | unset | if set, webhooks carry `X-Signature: sha256=<hmac>` |
| `DJANGO_STATIC_ROOT/_URL`, `DJANGO_MEDIA_ROOT/_URL` | see settings | static and media |
| `DJANGO_SIMPLE_JWT_*`, `DJANGO_EMAIL_*` | see settings | template settings |

## REST API

Interactive docs: Swagger UI at `/` (schema at `/api/schema/`).

Clients authenticate with a JWT (`Authorization: Bearer`) or an API key
(`X-API-Key: sgk_...`; create one with `POST /api/api-keys/` while logged in; the secret is
shown once and only its hash is stored).

```bash
curl -X POST http://localhost:8000/api/messages/ \
  -H "X-API-Key: sgk_..." -H "Content-Type: application/json" \
  -d '{"to": "+15551234567", "body": "Hello", "client_reference": "order-42"}'
```

`client_reference` is an idempotency key (unique per user). Optional: `node`, `sim_slot`,
`subscription_id`, `callback_url` (the backend POSTs the message's state there on every
status change, retrying with backoff; see `messaging/tasks.py`).

## Nodes and the WebSocket

1. Set `DJANGO_NODE_REGISTRATION_KEY` on the server.
2. In the Android app set the backend URL to e.g. `https://sms.example.com` (the app derives
   `wss://.../ws/nodes/<nodeId>`) and the same registration key. The app calls
   `POST /api/nodes/register`, stores `nodeId` + `token` and connects.
3. The node shows up in `GET /api/nodes/` (staff only) and in the Django admin.

WebSocket: `GET /ws/nodes/<nodeId>` with `Authorization: Bearer <token>`. Invalid
credentials, a token for another node, or a disabled node get HTTP 403 on the upgrade (the
consumer closes before accepting). Frames and semantics are exactly as in
[`../docs/PROTOCOL.md`](../docs/PROTOCOL.md):

* server to node: `send_sms`, `config` (sent on connect and when an admin pushes config)
* node to server: `ack`, `status_update`, `heartbeat`; unknown types are ignored
* on connect the backend marks the node online, re-sends jobs assigned to it that it never
  acked, then dispatches pending messages
* a second connection for the same node replaces the first (the old socket is closed with
  code 4000); disabling a node closes its socket with 4403
* `status_update` is idempotent per `(id, status, attempts)`; updates for a job the node does
  not own, or for a terminal message, are ignored
