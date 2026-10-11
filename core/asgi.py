import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

asgi_app = get_asgi_application()

from auth.urls import auth_router

app = ProtocolTypeRouter(
    {
        # Django's ASGI application to handle traditional HTTP requests
        "http": asgi_app,
        # WebSocket chat handler
        "websocket": AllowedHostsOriginValidator(
            URLRouter(
                [
                    path("test/", auth_router),
                    path(
                        "dev",
                        AuthMiddlewareStack(
                            URLRouter(
                                [
                                    path("auth/", auth_router),
                                ]
                            )
                        ),
                    ),
                ]
            )
        ),
    }
)
