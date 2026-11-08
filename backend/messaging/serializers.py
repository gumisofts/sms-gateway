import re

from rest_framework import serializers

from nodes.models import Node

from .models import ApiKey, SmsMessage

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
