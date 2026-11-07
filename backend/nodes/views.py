import hmac

from django.conf import settings
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .channels import config_event, send_to_node
from .models import Node
from .serializers import (
    NodeConfigSerializer,
    NodeRegisterRequestSerializer,
    NodeRegisterResponseSerializer,
    NodeSerializer,
)

IN_FLIGHT = ("dispatched", "queued", "sending")


def registration_allowed(key: str | None) -> bool:
    expected = settings.NODE_REGISTRATION_KEY
    if not expected:
        return settings.DEBUG
    return hmac.compare_digest((key or "").encode(), expected.encode())


class NodeRegisterView(APIView):
    """POST /api/nodes/register: called by the Android app (no user auth)."""

    authentication_classes: list = []
    permission_classes = [AllowAny]

    @extend_schema(
        tags=["nodes"],
        summary="Register a gateway node",
        description=(
            "Protocol section 1. Requires the shared `registrationKey` "
            "(`DJANGO_NODE_REGISTRATION_KEY`); when that is unset registration is "
            "only open in DEBUG. Re-registering with `previousNodeId` reuses the "
            "node and rotates its token (the old token stops working)."
        ),
        request=NodeRegisterRequestSerializer,
        responses={200: NodeRegisterResponseSerializer},
    )
    def post(self, request):
        serializer = NodeRegisterRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not registration_allowed(data.get("registrationKey")):
            return Response(
                {"detail": "Invalid registration key."},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            node, token = services.register_node(data)
        except services.NodeDisabled:
            return Response(
                {"detail": "Node is disabled."}, status=status.HTTP_403_FORBIDDEN
            )
        return Response({"nodeId": node.node_id, "token": token})


class NodeViewSet(viewsets.ReadOnlyModelViewSet):
    """Staff-facing node management."""

    serializer_class = NodeSerializer
    permission_classes = [IsAdminUser]
    lookup_field = "node_id"
    lookup_value_regex = "[^/]+"
    filter_backends = [DjangoFilterBackend]
    filterset_fields = {
        "is_active": ["exact"],
        "connected": ["exact"],
        "owner": ["exact"],
    }

    def get_queryset(self):
        return (
            Node.objects.all()
            .prefetch_related("sims")
            .annotate(
                in_flight=Count(
                    "messages", filter=Q(messages__status__in=IN_FLIGHT), distinct=True
                )
            )
        )

    def _node(self) -> Node:
        return get_object_or_404(Node, node_id=self.kwargs["node_id"])

    def _respond(self):
        node = self.get_queryset().get(node_id=self.kwargs["node_id"])
        return Response(self.get_serializer(node).data)

    @extend_schema(request=None, responses=NodeSerializer)
    @action(detail=True, methods=["post"])
    def disable(self, request, node_id=None):
        """Disable the node: kicks its WebSocket and refuses reconnects and jobs."""
        node = self._node()
        Node.objects.filter(pk=node.pk).update(is_active=False)
        send_to_node(node.node_id, {"type": "node.disconnect"})
        Node.objects.filter(pk=node.pk).update(connected=False, connection_id="")
        return self._respond()

    @extend_schema(request=None, responses=NodeSerializer)
    @action(detail=True, methods=["post"])
    def enable(self, request, node_id=None):
        """Re-enable a disabled node (it still needs a valid token to connect)."""
        node = self._node()
        Node.objects.filter(pk=node.pk).update(is_active=True)
        return self._respond()

    @extend_schema(request=NodeConfigSerializer, responses=NodeSerializer)
    @action(detail=True, methods=["post"])
    def config(self, request, node_id=None):
        """Store and push `config` (rate per minute / heartbeat interval) to the node."""
        node = self._node()
        serializer = NodeConfigSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if "rate_per_minute" in data:
            node.desired_rate_per_minute = data["rate_per_minute"]
        if "heartbeat_interval_sec" in data:
            node.desired_heartbeat_interval_sec = data["heartbeat_interval_sec"]
        node.save(
            update_fields=[
                "desired_rate_per_minute",
                "desired_heartbeat_interval_sec",
                "updated_at",
            ]
        )
        send_to_node(
            node.node_id,
            config_event(
                ratePerMinute=data.get("rate_per_minute"),
                heartbeatIntervalSec=data.get("heartbeat_interval_sec"),
            ),
        )
        return self._respond()
