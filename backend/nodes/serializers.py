from rest_framework import serializers

from .models import Node, NodeSim


class NodeRegisterRequestSerializer(serializers.Serializer):
    """Request body of POST /api/nodes/register (docs/PROTOCOL.md section 1)."""

    class SimSerializer(serializers.Serializer):
        slot = serializers.IntegerField()
        subscriptionId = serializers.IntegerField(required=False, allow_null=True)
        carrier = serializers.CharField(
            required=False, allow_blank=True, allow_null=True, max_length=255
        )

    deviceName = serializers.CharField(max_length=255, allow_blank=True)
    manufacturer = serializers.CharField(max_length=255, allow_blank=True)
    model = serializers.CharField(max_length=255, allow_blank=True)
    androidVersion = serializers.CharField(max_length=32, allow_blank=True)
    sdkInt = serializers.IntegerField(min_value=0)
    appVersion = serializers.CharField(max_length=32, allow_blank=True)
    sims = SimSerializer(many=True)
    previousNodeId = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, max_length=64
    )
    registrationKey = serializers.CharField(
        required=False, allow_blank=True, allow_null=True, write_only=True
    )


class NodeRegisterResponseSerializer(serializers.Serializer):
    nodeId = serializers.CharField()
    token = serializers.CharField()


class NodeSimSerializer(serializers.ModelSerializer):
    class Meta:
        model = NodeSim
        fields = ["slot", "subscription_id", "carrier"]


class NodeSerializer(serializers.ModelSerializer):
    sims = NodeSimSerializer(many=True, read_only=True)
    online = serializers.BooleanField(source="connected", read_only=True)
    in_flight = serializers.IntegerField(
        read_only=True,
        help_text="Messages assigned to the node that are not yet sent/terminal.",
    )
    stats = serializers.SerializerMethodField()

    class Meta:
        model = Node
        fields = [
            "node_id",
            "owner",
            "device_name",
            "manufacturer",
            "model",
            "android_version",
            "sdk_int",
            "app_version",
            "is_active",
            "online",
            "connected_at",
            "last_seen_at",
            "in_flight",
            "stats",
            "desired_rate_per_minute",
            "desired_heartbeat_interval_sec",
            "sims",
            "created_at",
        ]
        read_only_fields = fields

    def get_stats(self, node) -> dict:
        return {
            "battery_percent": node.battery_percent,
            "charging": node.charging,
            "signal_level": node.signal_level,
            "queue_depth": node.queue_depth,
            "rate_per_minute": node.rate_per_minute,
            "last_heartbeat_at": node.last_heartbeat_at,
        }


class NodeConfigSerializer(serializers.Serializer):
    rate_per_minute = serializers.IntegerField(
        required=False, min_value=0, max_value=100000, help_text="0 = unlimited"
    )
    heartbeat_interval_sec = serializers.IntegerField(
        required=False, min_value=5, max_value=600
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide at least one field.")
        return attrs
