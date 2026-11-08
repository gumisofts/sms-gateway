from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import RangeDateTimeFilter
from unfold.decorators import action, display

from . import services
from .models import ApiKey, SmsMessage, Status

STATUS_LABELS = {
    Status.PENDING.value: "warning",
    Status.DISPATCHED.value: "info",
    Status.QUEUED.value: "info",
    Status.SENDING.value: "info",
    Status.SENT.value: "primary",
    Status.DELIVERED.value: "success",
    Status.FAILED.value: "danger",
    Status.EXPIRED.value: "danger",
    Status.CANCELLED.value: "warning",
}


@admin.register(SmsMessage)
class SmsMessageAdmin(ModelAdmin):
    list_display = (
        "display_to",
        "display_status",
        "parts",
        "node",
        "sim",
        "owner",
        "attempts",
        "error_message",
        "created_at",
        "delivered_at",
    )
    list_filter = (
        "status",
        ("created_at", RangeDateTimeFilter),
        "node",
        "parts",
    )
    list_filter_submit = True
    search_fields = ("id", "to", "client_reference", "owner__phone_number")
    date_hierarchy = "created_at"
    list_select_related = ("node", "sim", "owner")
    raw_id_fields = ("owner", "node", "sim", "requested_node")
    list_per_page = 100
    actions = ["cancel_messages", "retry_messages"]
    readonly_fields = (
        "id",
        "parts",
        "tried_nodes",
        "dispatch_attempts",
        "attempts",
        "error_code",
        "error_message",
        "created_at",
        "updated_at",
        "dispatched_at",
        "acked_at",
        "sent_at",
        "delivered_at",
        "failed_at",
    )
    fieldsets = (
        (
            None,
            {"fields": ("id", "owner", "to", "body", "parts", "client_reference")},
        ),
        (
            "Routing",
            {
                "fields": (
                    "status",
                    "node",
                    "sim",
                    "requested_node",
                    "sim_slot",
                    "subscription_id",
                    "callback_url",
                )
            },
        ),
        (
            "Delivery",
            {
                "fields": (
                    "dispatch_attempts",
                    "attempts",
                    "tried_nodes",
                    "error_code",
                    "error_message",
                )
            },
        ),
        (
            "Timeline",
            {
                "classes": ["tab"],
                "fields": (
                    "created_at",
                    "dispatched_at",
                    "acked_at",
                    "sent_at",
                    "delivered_at",
                    "failed_at",
                    "updated_at",
                ),
            },
        ),
    )

    def has_add_permission(self, request):
        return False  # messages come from the API

    @display(description="To", header=True, ordering="to")
    def display_to(self, obj):
        body = obj.body if len(obj.body) <= 40 else obj.body[:40] + "…"
        return [obj.to, body]

    @display(description="Status", ordering="status", label=STATUS_LABELS)
    def display_status(self, obj):
        return obj.status

    @action(description="Cancel selected messages (if not yet on a phone)")
    def cancel_messages(self, request, queryset):
        done = skipped = 0
        for message_id in queryset.values_list("id", flat=True):
            try:
                services.cancel_message(message_id)
                done += 1
            except (services.NotCancellable, SmsMessage.DoesNotExist):
                skipped += 1
        self.message_user(
            request,
            f"Cancelled {done}; {skipped} could not be cancelled.",
            messages.SUCCESS if done else messages.WARNING,
        )

    @action(description="Retry selected failed/expired messages")
    def retry_messages(self, request, queryset):
        ids = list(
            queryset.filter(status__in=[Status.FAILED, Status.EXPIRED]).values_list(
                "id", flat=True
            )
        )
        SmsMessage.objects.filter(id__in=ids).update(
            status=Status.PENDING,
            node=None,
            sim=None,
            tried_nodes=[],
            dispatch_attempts=0,
            attempts=0,
            error_code=None,
            error_message="",
            failed_at=None,
            # A fresh TTL: expiry counts from created_at.
            created_at=timezone.now(),
        )
        services.schedule_dispatch(*ids)
        self.message_user(
            request, f"Re-queued {len(ids)} message(s).", messages.SUCCESS
        )


class ApiKeyCreateForm(forms.ModelForm):
    class Meta:
        model = ApiKey
        fields = ("user", "name")


@admin.register(ApiKey)
class ApiKeyAdmin(ModelAdmin):
    list_display = (
        "name",
        "prefix",
        "user",
        "display_state",
        "created_at",
        "last_used_at",
    )
    list_filter = ("revoked_at",)
    search_fields = ("name", "prefix", "user__phone_number")
    raw_id_fields = ("user",)
    actions = ["revoke_keys"]
    readonly_fields = ("prefix", "created_at", "last_used_at", "revoked_at")

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            kwargs["form"] = ApiKeyCreateForm
        return super().get_form(request, obj, **kwargs)

    def get_fields(self, request, obj=None):
        if obj is None:
            return ("user", "name")
        return ("user", "name", "prefix", "created_at", "last_used_at", "revoked_at")

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ("user", "prefix", "created_at", "last_used_at", "revoked_at")

    def save_model(self, request, obj, form, change):
        if change:
            return super().save_model(request, obj, form, change)
        row, key = ApiKey.create_for(
            form.cleaned_data["user"], form.cleaned_data["name"]
        )
        obj.pk = row.pk
        obj.prefix, obj.key_hash, obj.created_at = (
            row.prefix,
            row.key_hash,
            row.created_at,
        )
        obj._state.adding = False
        self.message_user(
            request,
            f"API key created. Copy it now, it is shown only once: {key}",
            messages.WARNING,
        )

    @display(description="State", label={"Active": "success", "Revoked": "danger"})
    def display_state(self, obj):
        return "Active" if obj.is_active else "Revoked"

    @action(description="Revoke selected keys")
    def revoke_keys(self, request, queryset):
        count = queryset.filter(revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        self.message_user(request, f"Revoked {count} key(s).", messages.SUCCESS)
