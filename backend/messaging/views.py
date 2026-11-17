from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from . import bulk, contacts, services
from .filters import SmsMessageFilter
from .models import ApiKey, Contact, ContactGroup, SmsBatch, SmsMessage
from .serializers import (ApiKeyCreatedSerializer, ApiKeySerializer,
                          ContactGroupSerializer,
                          ContactImportResultSerializer,
                          ContactImportSerializer, ContactSendResultSerializer,
                          ContactSendSerializer, ContactSerializer,
                          SmsMessageCreateSerializer, SmsMessageSerializer)


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


class OwnedViewSet(viewsets.ModelViewSet):
    """Rows of the caller only (JWT or their API key). Staff get no wider view."""

    permission_classes = [IsAuthenticated]
    pagination_class = MessagePagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return self.queryset.none()
        return self.queryset.filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


@extend_schema(tags=["contacts"])
class ContactGroupViewSet(OwnedViewSet):
    """Your contact groups."""

    queryset = ContactGroup.objects.annotate(contact_count=Count("contacts")).order_by(
        "name"
    )
    serializer_class = ContactGroupSerializer

    def perform_create(self, serializer):
        super().perform_create(serializer)
        serializer.instance.contact_count = 0

    def perform_update(self, serializer):
        count = serializer.instance.contact_count
        super().perform_update(serializer)
        serializer.instance.contact_count = count


@extend_schema(tags=["contacts"])
class ContactViewSet(OwnedViewSet):
    """Your contacts. Private: only you (JWT or your API key) can list or send to them."""

    queryset = Contact.objects.prefetch_related("groups")
    serializer_class = ContactSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["phone", "groups"]

    def get_serializer_class(self):
        if self.action == "send":
            return ContactSendSerializer
        if self.action == "import_contacts":
            return ContactImportSerializer
        return ContactSerializer

    @extend_schema(
        request={
            "application/json": ContactImportSerializer,
            "multipart/form-data": ContactImportSerializer,
        },
        responses={200: ContactImportResultSerializer},
    )
    @action(
        detail=False,
        methods=["post"],
        url_path="import",
        pagination_class=None,
        filter_backends=[],
    )
    def import_contacts(self, request):
        """Add many contacts at once from JSON rows and/or a CSV file.

        Numbers you already have are kept (renamed with `update_existing`)."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        rows = list(data.get("contacts") or [])
        if data.get("file"):
            rows += contacts.rows_from_bytes(data["file"].read())
        result = contacts.import_contacts(
            request.user, rows, data.get("groups", ()), data["update_existing"]
        )
        return Response(
            ContactImportResultSerializer(
                {
                    "created": result.created,
                    "updated": result.updated,
                    "existing": result.existing,
                    "duplicates": result.duplicates,
                    "invalid": result.invalid[:100],
                }
            ).data
        )

    @extend_schema(
        request=ContactSendSerializer, responses={201: ContactSendResultSerializer}
    )
    @action(detail=False, methods=["post"], pagination_class=None, filter_backends=[])
    def send(self, request):
        """Queue one text to your contacts and/or everyone in your groups.

        Ids that are not your own contacts or groups are rejected."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        rows = bulk.contact_rows(
            request.user, data.get("contacts", ()), data.get("groups", ())
        )
        plan = bulk.build_plan(data["body"], rows=rows)
        if not plan.recipients:
            return Response(
                {"detail": "No contacts to send to."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        limit = settings.BULK_SEND_MAX_RECIPIENTS
        if len(plan.recipients) > limit:
            return Response(
                {"detail": f"At most {limit} recipients per send."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        batch = bulk.create_batch(
            SmsBatch(
                name=data.get("name") or "Contacts (API)",
                created_by=request.user,
                body=data["body"],
                requested_node=data.get("requested_node"),
                sim_slot=data.get("sim_slot"),
                callback_url=data.get("callback_url") or "",
                valid_hours=data.get("valid_hours", 24),
            ),
            plan,
        )
        return Response(
            ContactSendResultSerializer(
                {
                    "batch": batch.pk,
                    "total": batch.total,
                    "skipped": batch.skipped,
                    "parts": plan.total_parts,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )
