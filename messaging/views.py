from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from . import services
from .filters import SmsMessageFilter
from .models import ApiKey, SmsMessage
from .serializers import (
    ApiKeyCreatedSerializer,
    ApiKeySerializer,
    SmsMessageCreateSerializer,
    SmsMessageSerializer,
)


class MessagePagination(PageNumberPagination):
    page_size = 50
    page_size_query_param = "page_size"
    max_page_size = 500


class NotApiKey(BasePermission):
    """API keys cannot mint or revoke API keys."""

    def has_permission(self, request, view):
        return not isinstance(request.auth, ApiKey)


@extend_schema(tags=["messages"])
class MessageViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Send and track SMS. Authenticate with a JWT or `X-API-Key`."""

    permission_classes = [IsAuthenticated]
    pagination_class = MessagePagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = SmsMessageFilter

    def get_queryset(self):
        qs = SmsMessage.objects.select_related("node", "requested_node")
        if getattr(self, "swagger_fake_view", False):
            return qs.none()
        if self.request.user.is_staff:
            return qs
        return qs.filter(owner=self.request.user)

    def get_serializer_class(self):
        if self.action in ("create", "bulk"):
            return SmsMessageCreateSerializer
        return SmsMessageSerializer

    @extend_schema(
        request=SmsMessageCreateSerializer,
        responses={
            201: SmsMessageSerializer,
            200: OpenApiResponse(
                SmsMessageSerializer, description="client_reference already used"
            ),
        },
    )
    def create(self, request, *args, **kwargs):
        """Queue one SMS. Returns 201, or 200 with the original if `client_reference` repeats."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        message, created = services.create_message(
            request.user, serializer.validated_data
        )
        return Response(
            SmsMessageSerializer(message).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @extend_schema(
        request=SmsMessageCreateSerializer(many=True),
        responses={201: SmsMessageSerializer(many=True)},
    )
    @action(detail=False, methods=["post"])
    def bulk(self, request):
        """Queue up to `DJANGO_MESSAGE_BULK_MAX` SMS in one request (all or nothing)."""
        items = request.data
        if not isinstance(items, list) or not items:
            return Response(
                {"detail": "Send a non-empty JSON list of messages."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(items) > settings.MESSAGE_BULK_MAX:
            return Response(
                {
                    "detail": f"At most {settings.MESSAGE_BULK_MAX} messages per request."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = self.get_serializer(data=items, many=True)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            messages = [
                services.create_message(request.user, item)[0]
                for item in serializer.validated_data
            ]
        return Response(
            SmsMessageSerializer(messages, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        request=None,
        responses={
            200: SmsMessageSerializer,
            409: OpenApiResponse(
                description="Already acknowledged by a node or finished"
            ),
        },
    )
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        """Cancel while still pending or dispatched-but-not-acked."""
        message = self.get_object()
        try:
            message = services.cancel_message(message.pk)
        except services.NotCancellable as exc:
            return Response(
                {"detail": f"Cannot cancel a message that is {exc}."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(SmsMessageSerializer(message).data)


@extend_schema(tags=["api-keys"])
class ApiKeyViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Manage your API keys (JWT/session auth only). DELETE revokes a key."""

    permission_classes = [IsAuthenticated, NotApiKey]
    serializer_class = ApiKeySerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return ApiKey.objects.none()
        return ApiKey.objects.filter(user=self.request.user)

    @extend_schema(request=ApiKeySerializer, responses={201: ApiKeyCreatedSerializer})
    def create(self, request, *args, **kwargs):
        """Create a key. The secret is returned once and cannot be retrieved later."""
        serializer = ApiKeySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row, key = ApiKey.create_for(request.user, serializer.validated_data["name"])
        row.key = key
        return Response(
            ApiKeyCreatedSerializer(row).data, status=status.HTTP_201_CREATED
        )

    def perform_destroy(self, instance):
        if instance.revoked_at is None:
            instance.revoked_at = timezone.now()
            instance.save(update_fields=["revoked_at"])
