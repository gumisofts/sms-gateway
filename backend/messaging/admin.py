from django.contrib import admin

from .models import ApiKey, SmsMessage


@admin.register(SmsMessage)
class SmsMessageAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "to",
        "status",
        "node",
        "owner",
        "attempts",
        "dispatch_attempts",
        "created_at",
        "delivered_at",
    )
    list_filter = ("status", "node", "created_at")
    search_fields = ("id", "to", "client_reference", "owner__phone_number")
    date_hierarchy = "created_at"
    raw_id_fields = ("owner", "node", "requested_node")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "prefix",
        "user",
        "created_at",
        "last_used_at",
        "revoked_at",
    )
    list_filter = ("revoked_at",)
    search_fields = ("name", "prefix", "user__phone_number")
    raw_id_fields = ("user",)
    readonly_fields = ("prefix", "key_hash", "created_at", "last_used_at")
