import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path, re_path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

asgi_app = get_asgi_application()

from auth.urls import auth_router  # noqa: E402
from nodes.urls import websocket_urlpatterns as node_websocket_urlpatterns  # noqa: E402

app = ProtocolTypeRouter(
    {
        # Django's ASGI application to handle traditional HTTP requests
        "http": asgi_app,
        "websocket": URLRouter(
            [
                # Android nodes: native clients, no Origin header, bearer-token auth.
                *node_websocket_urlpatterns,
                # Browser-facing sockets keep the origin check.
                re_path(
                    r"^",
                    AllowedHostsOriginValidator(
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
                ),
            ]
        ),
    }
)
