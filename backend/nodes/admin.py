from django.contrib import admin, messages
from django.db.models import Count, IntegerField, OuterRef, Q, Subquery, Sum
from django.db.models.functions import Coalesce
from unfold.admin import ModelAdmin, TabularInline
from unfold.decorators import action, display

from messaging.dispatch import day_start
from messaging.models import SmsMessage

from .channels import send_to_node
from .models import Node, NodeSim, NodeToken


def sim_state(sim: NodeSim, now=None) -> tuple[str, str]:
    """(label, variant) for a SIM lane."""
    if not sim.is_present:
        return "Absent", "info"
    if not sim.is_enabled:
        return "Disabled", "warning"
    if sim.is_paused(now):
        return "Paused", "danger"
    return "Active", "success"


def _parts_today_subquery():
    return Coalesce(
        Subquery(
            SmsMessage.objects.filter(
                sim=OuterRef("pk"), dispatched_at__gte=day_start()
            )
            .values("sim")
            .annotate(total=Sum("parts"))
            .values("total")[:1],
            output_field=IntegerField(),
        ),
        0,
    )


class NodeSimInline(TabularInline):
    model = NodeSim
    extra = 0
    can_delete = False
    fields = (
        "slot",
        "carrier",
        "label",
        "subscription_id",
        "is_present",
        "is_enabled",
        "rate_per_minute",
        "daily_limit",
        "consecutive_failures",
        "paused_until",
    )
    readonly_fields = (
        "slot",
        "carrier",
        "subscription_id",
        "is_present",
        "consecutive_failures",
    )

    def has_add_permission(self, request, obj=None):
        return False  # SIMs come from the phone


@admin.register(Node)
class NodeAdmin(ModelAdmin):
    list_display = (
        "display_node",
        "display_connection",
        "display_sms_limit",
        "display_battery",
        "sim_count",
        "queue_depth",
        "owner",
        "app_version",
        "last_seen_at",
    )
    list_filter = (
        "connected",
        "is_active",
        "sms_limit_raised",
        "manufacturer",
        "app_version",
    )
    search_fields = ("node_id", "device_name", "model", "owner__phone_number")
    raw_id_fields = ("owner",)
    list_per_page = 50
    inlines = [NodeSimInline]
    actions = ["disable_nodes", "enable_nodes"]
    readonly_fields = (
        "node_id",
        "device_name",
        "manufacturer",
        "model",
        "android_version",
        "sdk_int",
        "app_version",
        "connected",
        "connection_id",
        "connected_at",
        "last_seen_at",
        "battery_percent",
        "charging",
        "signal_level",
        "queue_depth",
        "rate_per_minute",
        "last_heartbeat_at",
        "sms_limit_raised",
        "sms_limit_max_count",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            None,
            {"fields": ("node_id", "device_name", "owner", "is_active")},
        ),
        (
            "Live status",
            {
                "fields": (
                    "connected",
                    "connected_at",
                    "last_seen_at",
                    "last_heartbeat_at",
                    "battery_percent",
                    "charging",
                    "signal_level",
                    "queue_depth",
                    "rate_per_minute",
                    "sms_limit_raised",
                    "sms_limit_max_count",
                )
            },
        ),
        (
            "Config pushed to the phone",
            {"fields": ("desired_rate_per_minute", "desired_heartbeat_interval_sec")},
        ),
        (
            "Device",
            {
                "classes": ["tab"],
                "fields": (
                    "manufacturer",
                    "model",
                    "android_version",
                    "sdk_int",
                    "app_version",
                    "connection_id",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related("owner")
            .annotate(_sims=Count("sims", filter=Q(sims__is_present=True)))
        )

    @display(description="Phone", header=True)
    def display_node(self, obj):
        return [obj.device_name or obj.model or "Unknown phone", obj.node_id]

    @display(
        description="Connection",
        label={"Online": "success", "Offline": "danger", "Disabled": "warning"},
    )
    def display_connection(self, obj):
        if not obj.is_active:
            return "Disabled"
        return "Online" if obj.connected else "Offline"

    @display(
        description="SMS limit",
        label={"Raised": "success", "NOT raised": "danger", "Unknown": "warning"},
    )
    def display_sms_limit(self, obj):
        if obj.sms_limit_raised is True:
            return "Raised"
        if obj.sms_limit_raised is False:
            return "NOT raised"
        return "Unknown"

    @display(description="Battery")
    def display_battery(self, obj):
        if obj.battery_percent is None:
            return "-"
        return f"{obj.battery_percent}%{' ⚡' if obj.charging else ''}"

    @display(description="SIMs", ordering="_sims")
    def sim_count(self, obj):
        return obj._sims

    @action(
        description="Disable selected phones (disconnects them)", permissions=["change"]
    )
    def disable_nodes(self, request, queryset):
        nodes = list(queryset.filter(is_active=True))
        queryset.update(is_active=False)
        for node in nodes:
            try:
                send_to_node(node.node_id, {"type": "node.disconnect"})
            except Exception:
                pass  # offline already; it cannot reconnect while disabled
        self.message_user(request, f"Disabled {len(nodes)} phone(s).", messages.SUCCESS)

    @action(description="Enable selected phones", permissions=["change"])
    def enable_nodes(self, request, queryset):
        count = queryset.update(is_active=True)
        self.message_user(request, f"Enabled {count} phone(s).", messages.SUCCESS)


@admin.register(NodeSim)
class NodeSimAdmin(ModelAdmin):
    list_display = (
        "display_sim",
        "display_state",
        "display_usage",
        "rate_per_minute",
        "daily_limit",
        "is_enabled",
        "consecutive_failures",
        "last_error",
        "last_success_at",
    )
    list_display_links = ("display_sim",)
    list_editable = ("rate_per_minute", "daily_limit", "is_enabled")
    list_filter = ("is_enabled", "is_present", "carrier", "node__connected")
    search_fields = ("node__node_id", "node__device_name", "carrier", "label")
    list_select_related = ("node",)
    list_per_page = 100
    actions = ["resume_sims", "enable_sims", "disable_sims"]
    readonly_fields = (
        "node",
        "slot",
        "subscription_id",
        "carrier",
        "is_present",
        "consecutive_failures",
        "last_success_at",
        "last_failure_at",
        "last_error",
    )
    fieldsets = (
        (None, {"fields": ("node", "slot", "carrier", "subscription_id", "label")}),
        (
            "Sending lane",
            {
                "description": "Counted in SMS parts. Empty uses the server default; 0 means unlimited.",
                "fields": ("is_enabled", "rate_per_minute", "daily_limit"),
            },
        ),
        (
            "Health",
            {
                "fields": (
                    "is_present",
                    "paused_until",
                    "pause_reason",
                    "consecutive_failures",
                    "last_success_at",
                    "last_failure_at",
                    "last_error",
                )
            },
        ),
    )

    def has_add_permission(self, request):
        return False  # SIMs come from the phone

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_today=_parts_today_subquery())

    @display(description="SIM", header=True, ordering="node__device_name")
    def display_sim(self, obj):
        name = obj.label or obj.carrier or "SIM"
        return [f"{name} · slot {obj.slot}", obj.node.device_name or obj.node.node_id]

    @display(
        description="State",
        label={
            "Active": "success",
            "Paused": "danger",
            "Disabled": "warning",
            "Absent": "info",
        },
    )
    def display_state(self, obj):
        return sim_state(obj)[0]

    @display(description="Parts today / cap", ordering="_today")
    def display_usage(self, obj):
        cap = obj.effective_daily_limit
        if not cap:
            return f"{obj._today} / ∞"
        pct = min(round(100 * obj._today / cap), 100)
        return f"{obj._today} / {cap} ({pct}%)"

    @action(
        description="Resume selected SIMs (clear auto-pause)", permissions=["change"]
    )
    def resume_sims(self, request, queryset):
        from messaging import services

        count = 0
        for sim in queryset:
            services.resume_sim(sim)
            count += 1
        self.message_user(request, f"Resumed {count} SIM(s).", messages.SUCCESS)

    @action(description="Enable selected SIMs", permissions=["change"])
    def enable_sims(self, request, queryset):
        count = queryset.update(is_enabled=True)
        self.message_user(request, f"Enabled {count} SIM(s).", messages.SUCCESS)

    @action(description="Disable selected SIMs", permissions=["change"])
    def disable_sims(self, request, queryset):
        count = queryset.update(is_enabled=False)
        self.message_user(request, f"Disabled {count} SIM(s).", messages.SUCCESS)


@admin.register(NodeToken)
class NodeTokenAdmin(ModelAdmin):
    list_display = ("node", "created_at", "last_used_at")
    readonly_fields = ("node", "token_hash", "created_at", "last_used_at")

    def has_add_permission(self, request):
        return False
