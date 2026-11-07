from django.urls import path
from rest_framework.routers import DefaultRouter

from .auth import NodeTokenAuthMiddleware
from .consumers import NodeConsumer
from .views import NodeRegisterView, NodeViewSet

router = DefaultRouter()
# Mounted under /api/ next to the messaging router, which would clash on the root view.
router.include_root_view = False
router.register("nodes", NodeViewSet, basename="node")

# Registered before the router so `nodes/register` (no trailing slash, as the
# Android app calls it) is never captured as a node id.
urlpatterns = [
    path("nodes/register", NodeRegisterView.as_view(), name="node-register"),
] + router.urls

# Raw WebSocket route: native clients send no Origin header, so this must not
# sit behind AllowedHostsOriginValidator. Auth is the node bearer token.
websocket_urlpatterns = [
    path(
        "ws/nodes/<str:node_id>",
        NodeTokenAuthMiddleware(NodeConsumer.as_asgi()),
    )
]
