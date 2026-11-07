import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from django.core.management.utils import get_random_secret_key
from dotenv import load_dotenv

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent.parent

TESTING = "test" in sys.argv[1:2]

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", get_random_secret_key())

DEBUG = os.getenv("DJANGO_DEBUG", False) == "True"

ALLOWED_HOSTS = [
    "localhost",
    *os.getenv("DJANGO_ALLOWED_HOSTS", "").split(","),
]


# Behind a TLS-terminating reverse proxy (nginx on the Gisila panel): trust its
# X-Forwarded-Proto so request.is_secure() and the admin's CSRF origin check work.
if os.getenv("DJANGO_BEHIND_PROXY", "False") == "True":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True

# e.g. "https://sms.example.com" (needed for the admin / browsable API over https).
CSRF_TRUSTED_ORIGINS = [
    origin
    for origin in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin
]


# Application definition

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:4000",
    "http://127.0.0.1:4200",
    *os.getenv("DJANGO_CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(","),
]

AUTH_USER_MODEL = "accounts.User"
DEFAULT_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_filters",
    "channels",
    "drf_spectacular",
    "drf_spectacular_sidecar",
    "corsheaders",
]

LOCAL_APPS = [
    "auth",
    "nodes",
    "messaging",
]

INSTALLED_APPS = DEFAULT_APPS + THIRD_PARTY_APPS + LOCAL_APPS


X_FRAME_OPTIONS = "SAMEORIGIN"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "core.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "core.wsgi.app"
ASGI_APPLICATION = "core.asgi.app"

# Postgres when DJANGO_POSTGRES_URL is set, SQLite otherwise.
postgres_url = os.getenv("DJANGO_POSTGRES_URL")

if postgres_url:
    _pg = urlparse(postgres_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _pg.path[1:],
            "USER": unquote(_pg.username or ""),
            "PASSWORD": unquote(_pg.password or ""),
            "HOST": _pg.hostname,
            "PORT": _pg.port,
            "OPTIONS": dict[str, Any](parse_qsl(_pg.query)),
            "DISABLE_SERVER_SIDE_CURSORS": True,
        },
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        },
    }

# Channel layer. Node WebSockets and the REST/Celery processes only see each
# other through the layer, so any multi-process deployment needs Redis.
# The in-memory layer is process-local (single process dev and tests only).
REDIS_URL = os.getenv("DJANGO_REDIS_URL")

if REDIS_URL and not TESTING:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_URL]},
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"},
    }


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

AUTHENTICATION_BACKENDS = [
    "auth.backends.PhoneBackend",
    "auth.backends.EmailBackend",
    "django.contrib.auth.backends.ModelBackend",
]

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}

STATIC_URL = os.getenv("DJANGO_STATIC_URL", "/static/")
STATIC_ROOT = os.getenv("DJANGO_STATIC_ROOT", os.path.join(BASE_DIR, "staticfiles"))

MEDIA_URL = os.getenv("DJANGO_MEDIA_URL", "/medias/")
MEDIA_ROOT = os.getenv("DJANGO_MEDIA_ROOT", os.path.join(BASE_DIR, "medias"))


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "messaging.authentication.ApiKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

EMAIL_USE_SSL = True
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("DJANGO_EMAIL_HOST")
EMAIL_PORT = os.getenv("DJANGO_EMAIL_PORT")
EMAIL_HOST_USER = os.getenv("DJANGO_EMAIL_HOST_USER")
EMAIL_HOST_PASSWORD = os.getenv("DJANGO_EMAIL_HOST_PASSWORD")

ADMIN = ("Murad", "nuradhussen082@gmail.com")

# Celery (see core/celery.py). Broker falls back to the Redis URL.
CELERY_BROKER_URL = (
    os.getenv("DJANGO_CELERY_BROKER_URL")
    or os.getenv("CELERY_BROKER_URL")
    or REDIS_URL
    or "memory://"
)
CELERY_RESULT_BACKEND = (
    os.getenv("DJANGO_CELERY_RESULT_BACKEND") or os.getenv("CELERY_BACKEND_URL") or None
)
CELERY_TASK_ALWAYS_EAGER = TESTING
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "dispatch-pending-messages": {
        "task": "messaging.tasks.dispatch_pending_messages",
        "schedule": 10.0,
    },
    "redispatch-unacked-messages": {
        "task": "messaging.tasks.redispatch_unacked_messages",
        "schedule": 30.0,
    },
    "expire-pending-messages": {
        "task": "messaging.tasks.expire_pending_messages",
        "schedule": 60.0,
    },
    "mark-stale-nodes-offline": {
        "task": "nodes.tasks.mark_stale_nodes_offline",
        "schedule": 30.0,
    },
}

# SMS gateway tuning.
# Shared secret nodes must present when registering. If unset, registration is
# only allowed when DJANGO_DEBUG=True.
NODE_REGISTRATION_KEY = os.getenv("DJANGO_NODE_REGISTRATION_KEY", "")
# Heartbeat interval pushed to nodes in the `config` message on connect.
NODE_HEARTBEAT_INTERVAL_SEC = int(os.getenv("DJANGO_NODE_HEARTBEAT_INTERVAL_SEC", 30))
# A connected node with no heartbeat for this long (or 3x its configured
# interval, whichever is larger) is marked offline by the beat task.
NODE_OFFLINE_AFTER_SEC = int(os.getenv("DJANGO_NODE_OFFLINE_AFTER_SEC", 120))
# Dispatched but never acked after this long: resend / re-dispatch.
MESSAGE_ACK_TIMEOUT_SEC = int(os.getenv("DJANGO_MESSAGE_ACK_TIMEOUT_SEC", 60))
# Pending (no node available) for longer than this: expired.
MESSAGE_PENDING_TTL_SEC = int(os.getenv("DJANGO_MESSAGE_PENDING_TTL_SEC", 3600))
# Give up (failed) after this many dispatch attempts.
MESSAGE_MAX_DISPATCH_ATTEMPTS = int(
    os.getenv("DJANGO_MESSAGE_MAX_DISPATCH_ATTEMPTS", 5)
)
MESSAGE_BULK_MAX = int(os.getenv("DJANGO_MESSAGE_BULK_MAX", 500))
# If set, status webhooks carry an X-Signature: sha256=<hmac of body>.
WEBHOOK_SECRET = os.getenv("DJANGO_WEBHOOK_SECRET", "")


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(
        days=os.getenv("DJANGO_SIMPLE_JWT_ACCESS_TOKEN_LIFETIME", 90)
    ),
    "REFRESH_TOKEN_LIFETIME": timedelta(
        days=os.getenv("DJANGO_SIMPLE_JWT_REFRESH_TOKEN_LIFETIME", 90)
    ),
    "UPDATE_LAST_LOGIN": True,
    "ALGORITHM": "HS256",
    "SIGNING_KEY": os.getenv("DJANGO_SIMPLE_JWT_SIGNING_KEY", SECRET_KEY),
    "VERIFYING_KEY": os.getenv("DJANGO_SIMPLE_JWT_VERIFYING_KEY", SECRET_KEY),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "AUTH_HEADER_NAME": "HTTP_AUTHORIZATION",
}


SPECTACULAR_SETTINGS = {
    "TITLE": "SMS Gateway API",
    "DESCRIPTION": (
        "Send SMS through a fleet of Android gateway nodes. Clients call the REST "
        "API (JWT bearer or X-API-Key); the backend dispatches jobs to nodes over "
        "WebSocket (/ws/nodes/{nodeId}, see docs/PROTOCOL.md)."
    ),
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": True,
    # OTHER SETTINGS
    "SWAGGER_UI_DIST": "SIDECAR",  # shorthand to use the sidecar instead
    "SWAGGER_UI_FAVICON_HREF": "SIDECAR",
    "REDOC_DIST": "SIDECAR",
}
