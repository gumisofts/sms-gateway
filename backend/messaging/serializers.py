import re

from rest_framework import serializers

from nodes.models import Node

from .bulk import normalize_number
from .contacts import MAX_IMPORT_BYTES
from .models import ApiKey, Contact, ContactGroup, SmsMessage

E164 = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone(value: str) -> str:
    cleaned = re.sub(r"[\s\-().]", "", value or "")
    if not E164.match(cleaned):
        raise serializers.ValidationError(
            "Use international E.164 format, e.g. +15551234567."
        )
    return cleaned


class SmsMessageSerializer(serializers.ModelSerializer):
    node = serializers.SlugRelatedField(slug_field="node_id", read_only=True)
    requested_node = serializers.SlugRelatedField(slug_field="node_id", read_only=True)

    class Meta:
        model = SmsMessage
        fields = [
            "id",
            "to",
            "body",
            "status",
            "node",
            "requested_node",
            "sim_slot",
            "subscription_id",
            "parts",
            "client_reference",
            "callback_url",
            "attempts",
            "dispatch_attempts",
            "error_code",
            "error_message",
            "created_at",
            "dispatched_at",
            "acked_at",
            "sent_at",
            "delivered_at",
            "failed_at",
        ]
        read_only_fields = fields


class SmsMessageCreateSerializer(serializers.Serializer):
    to = serializers.CharField(max_length=32, help_text="E.164, e.g. +15551234567")
    body = serializers.CharField(max_length=1600, trim_whitespace=False)
    node = serializers.SlugRelatedField(
        slug_field="node_id",
        queryset=Node.objects.filter(is_active=True),
        required=False,
        allow_null=True,
        source="requested_node",
        help_text="Only use this node (otherwise the dispatcher picks one).",
    )
    sim_slot = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    subscription_id = serializers.IntegerField(
        required=False, allow_null=True, help_text="Wins over sim_slot."
    )
    client_reference = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=128,
        help_text="Idempotency key, unique per API user: repeating it returns the original message.",
    )
    callback_url = serializers.URLField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Optional webhook that receives status changes.",
    )

    def validate_to(self, value):
        return normalize_phone(value)

    def validate_callback_url(self, value):
        if value and not value.lower().startswith(("http://", "https://")):
            raise serializers.ValidationError("Must be an http(s) URL.")
        return value

    def validate_node(self, node):
        user = self.context["request"].user
        if (
            node is not None
            and node.owner_id not in (None, user.pk)
            and not user.is_staff
        ):
            raise serializers.ValidationError("Unknown node.")
        return node


class ApiKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApiKey
        fields = ["id", "name", "prefix", "created_at", "last_used_at", "revoked_at"]
        read_only_fields = ["id", "prefix", "created_at", "last_used_at", "revoked_at"]


class ApiKeyCreatedSerializer(ApiKeySerializer):
    key = serializers.CharField(
        read_only=True, help_text="The secret. Shown once; only a hash is stored."
    )

    class Meta(ApiKeySerializer.Meta):
        fields = ApiKeySerializer.Meta.fields + ["key"]
        read_only_fields = fields


# ----------------------------------------------------------------- contacts


class OwnedPrimaryKeyField(serializers.PrimaryKeyRelatedField):
    """Only the requesting user's rows; anyone else's id is "does not exist"."""

    def get_queryset(self):
        request = self.context.get("request")
        queryset = super().get_queryset()
        if request is None or not request.user.is_authenticated:
            return queryset.none()
        return queryset.filter(owner=request.user)


class OwnedSerializer(serializers.ModelSerializer):
    def _taken(self, **lookup) -> bool:
        rows = self.Meta.model.objects.filter(
            owner=self.context["request"].user, **lookup
        )
        if self.instance is not None:
            rows = rows.exclude(pk=self.instance.pk)
        return rows.exists()


class ContactGroupSerializer(OwnedSerializer):
    contacts = serializers.IntegerField(
        source="contact_count", read_only=True, help_text="Contacts in the group."
    )

    class Meta:
        model = ContactGroup
        fields = ["id", "name", "contacts", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_name(self, value):
        if self._taken(name=value):
            raise serializers.ValidationError(
                "You already have a group with this name."
            )
        return value


class ContactSerializer(OwnedSerializer):
    groups = OwnedPrimaryKeyField(
        queryset=ContactGroup.objects.all(), many=True, required=False
    )

    class Meta:
        model = Contact
        fields = ["id", "name", "phone", "groups", "created_at"]
        read_only_fields = ["id", "created_at"]

    def validate_phone(self, value):
        phone = normalize_number(value)
        if phone is None:
            raise serializers.ValidationError("Not a valid phone number.")
        if self._taken(phone=phone):
            raise serializers.ValidationError(
                "You already have a contact with this number."
            )
        return phone


class ContactSendSerializer(SmsMessageCreateSerializer):
    """One text to many of the caller's contacts, as one batch."""

    to = None
    subscription_id = None
    client_reference = None

    body = serializers.CharField(
        max_length=1600,
        trim_whitespace=False,
        help_text="{name} becomes each contact's name.",
    )
    contacts = OwnedPrimaryKeyField(
        queryset=Contact.objects.all(), many=True, required=False
    )
    groups = OwnedPrimaryKeyField(
        queryset=ContactGroup.objects.all(), many=True, required=False
    )
    name = serializers.CharField(
        max_length=120, required=False, help_text="Label of the batch in the admin."
    )
    valid_hours = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=720,
        help_text="Messages still waiting after this many hours expire (default 24).",
    )

    def validate(self, data):
        if not data.get("contacts") and not data.get("groups"):
            raise serializers.ValidationError("Give contacts and/or groups.")
        return data


class ContactSendResultSerializer(serializers.Serializer):
    batch = serializers.IntegerField(
        help_text="List its messages with GET /api/messages/?batch=<id>."
    )
    total = serializers.IntegerField(help_text="Messages queued.")
    skipped = serializers.IntegerField(help_text="Invalid or duplicate numbers.")
    parts = serializers.IntegerField(help_text="SMS parts in total.")


class ContactImportRowSerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=40)
    name = serializers.CharField(max_length=120, required=False, allow_blank=True)


class ContactImportSerializer(serializers.Serializer):
    """Rows as JSON (``contacts``) and/or a CSV upload (``file``, multipart)."""

    contacts = ContactImportRowSerializer(many=True, required=False)
    file = serializers.FileField(
        required=False, help_text="CSV, same format as the admin import."
    )
    groups = OwnedPrimaryKeyField(
        queryset=ContactGroup.objects.all(),
        many=True,
        required=False,
        help_text="Your groups that every imported contact joins.",
    )
    update_existing = serializers.BooleanField(
        default=False, help_text="Rename contacts whose number you already have."
    )

    def validate_file(self, upload):
        if upload.size > MAX_IMPORT_BYTES:
            raise serializers.ValidationError("Larger than 10 MB.")
        return upload

    def validate(self, data):
        if not data.get("contacts") and not data.get("file"):
            raise serializers.ValidationError("Give contacts and/or a CSV file.")
        return data


class ContactImportResultSerializer(serializers.Serializer):
    created = serializers.IntegerField()
    updated = serializers.IntegerField(help_text="Renamed (update_existing).")
    existing = serializers.IntegerField(help_text="Already there, left as they were.")
    duplicates = serializers.IntegerField(help_text="Repeated within the import.")
    invalid = serializers.ListField(
        child=serializers.CharField(), help_text="Numbers that were not valid."
    )
