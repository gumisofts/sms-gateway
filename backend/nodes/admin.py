from django.contrib import admin

from .models import Node, NodeSim, NodeToken


class NodeSimInline(admin.TabularInline):
    model = NodeSim
    extra = 0


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = (
        "node_id",
        "device_name",
        "owner",
        "is_active",
        "connected",
        "battery_percent",
        "queue_depth",
        "rate_per_minute",
        "last_seen_at",
    )
    list_filter = ("is_active", "connected", "manufacturer", "app_version")
    search_fields = ("node_id", "device_name", "model", "owner__phone_number")
    raw_id_fields = ("owner",)
    readonly_fields = ("node_id", "connection_id", "created_at", "updated_at")
    inlines = [NodeSimInline]


@admin.register(NodeToken)
class NodeTokenAdmin(admin.ModelAdmin):
    list_display = ("node", "created_at", "last_used_at")
    readonly_fields = ("node", "token_hash", "created_at", "last_used_at")

    def has_add_permission(self, request):
        return False
