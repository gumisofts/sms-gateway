"""Admin bulk send: a two-step form (preview, then confirm) that creates a batch."""

import base64
import binascii

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.widgets import (
    UnfoldAdminFileFieldWidget,
    UnfoldAdminTextareaWidget,
    UnfoldBooleanSwitchWidget,
)

from auth.roles import can_view_all
from nodes.models import Node

from . import bulk, services
from .models import IN_FLIGHT, SmsBatch, SmsMessage, Status

MAX_CSV_BYTES = 10 * 1024 * 1024


class BulkSendForm(forms.ModelForm):
    recipients = forms.CharField(
        required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 6}),
        help_text=(
            "One number per line (or separated by commas). Local numbers like "
            "0912345678 or 912345678 get the country code added."
        ),
    )
    csv_file = forms.FileField(
        required=False,
        label="CSV file",
        widget=UnfoldAdminFileFieldWidget,
        help_text=(
            "Optional. A phone column (phone/msisdn/number/to/mobile or the first "
            "column); other columns can be used as {column} in the text; a "
            "'message' column replaces the text for that row."
        ),
    )
    csv_cache = forms.CharField(required=False, widget=forms.HiddenInput)
    confirm = forms.BooleanField(
        required=False,
        label="Confirm and send",
        widget=UnfoldBooleanSwitchWidget,
        help_text="Leave off and save to see a preview first.",
    )

    class Meta:
        model = SmsBatch
        fields = (
            "name",
            "body",
            "requested_node",
            "sim_slot",
            "valid_hours",
            "callback_url",
        )
        widgets = {"body": UnfoldAdminTextareaWidget(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.plan = None
        if "requested_node" in self.fields:
            self.fields["requested_node"].queryset = Node.objects.filter(
                is_active=True
            ).order_by("device_name")

    def _csv_bytes(self) -> bytes | None:
        upload = self.cleaned_data.get("csv_file")
        if upload:
            if upload.size > MAX_CSV_BYTES:
                raise forms.ValidationError("CSV file is larger than 10 MB.")
            data = upload.read()
            # Keep it for the confirm step: browsers do not resend files.
            self.data = self.data.copy()
            self.data["csv_cache"] = base64.b64encode(data).decode()
            return data
        cached = self.cleaned_data.get("csv_cache")
        if cached:
            try:
                return base64.b64decode(cached)
            except (binascii.Error, ValueError):
                return None
        return None

    def clean(self):
        cleaned = super().clean()
        body = cleaned.get("body") or ""
        if not body.strip():
            return cleaned
        if cleaned.get("valid_hours") == 0:
            self.add_error("valid_hours", "Must be at least 1 hour.")
            return cleaned
        try:
            csv_data = self._csv_bytes()
        except forms.ValidationError as error:
            self.add_error("csv_file", error)
            return cleaned
        plan = bulk.build_plan(body, cleaned.get("recipients", ""), csv_data)
        if not plan.recipients:
            self.add_error(
                "recipients",
                "No valid recipients. " + (bulk.summary(plan) if plan.invalid else ""),
            )
            return cleaned
        limit = settings.BULK_SEND_MAX_RECIPIENTS
        if len(plan.recipients) > limit:
            self.add_error(
                "recipients",
                f"{len(plan.recipients)} recipients; at most {limit} per bulk send.",
            )
            return cleaned
        self.plan = plan
        if not cleaned.get("confirm"):
            self.add_error(
                "confirm",
                "Preview: "
                + bulk.summary(plan)
                + " Switch on 'Confirm and send' and save.",
            )
        return cleaned


@admin.register(SmsBatch)
class SmsBatchAdmin(ModelAdmin):
    form = BulkSendForm
    list_display = (
        "name",
        "created_by",
        "total",
        "display_progress",
        "display_waiting",
        "display_failed",
        "created_at",
        "display_messages",
    )
    list_filter = ("created_at",)
    search_fields = ("name", "body", "created_by__phone_number")
    list_select_related = ("created_by",)
    actions = ["cancel_waiting"]
    actions_detail = ["cancel_waiting_detail"]

    add_fieldsets = (
        (None, {"fields": ("name", "body")}),
        ("Recipients", {"fields": ("recipients", "csv_file", "csv_cache")}),
        (
            "Options",
            {
                "classes": ["tab"],
                "fields": ("requested_node", "sim_slot", "valid_hours", "callback_url"),
            },
        ),
        ("Send", {"fields": ("confirm",)}),
    )
    change_fieldsets = (
        (None, {"fields": ("name", "created_by", "created_at", "body")}),
        (
            "Progress",
            {
                "fields": (
                    "total",
                    "skipped",
                    "display_breakdown",
                    "display_messages",
                )
            },
        ),
        (
            "Options",
            {
                "classes": ["tab"],
                "fields": ("requested_node", "sim_slot", "valid_hours", "callback_url"),
            },
        ),
    )

    def get_fieldsets(self, request, obj=None):
        return self.add_fieldsets if obj is None else self.change_fieldsets

    def get_form(self, request, obj=None, **kwargs):
        if obj is not None:
            kwargs["form"] = forms.ModelForm
            kwargs["fields"] = []
        return super().get_form(request, obj, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return (
            "name",
            "created_by",
            "created_at",
            "body",
            "total",
            "skipped",
            "display_breakdown",
            "display_messages",
            "requested_node",
            "sim_slot",
            "valid_hours",
            "callback_url",
        )

    def get_queryset(self, request):
        qs = (
            super()
            .get_queryset(request)
            .annotate(
                _done=Count(
                    "messages",
                    filter=Q(messages__status__in=[Status.SENT, Status.DELIVERED]),
                ),
                _delivered=Count(
                    "messages", filter=Q(messages__status=Status.DELIVERED)
                ),
                _failed=Count(
                    "messages",
                    filter=Q(messages__status__in=[Status.FAILED, Status.EXPIRED]),
                ),
                _waiting=Count(
                    "messages",
                    filter=Q(messages__status__in=[Status.PENDING, *IN_FLIGHT]),
                ),
            )
        )
        if not can_view_all(request.user):
            qs = qs.filter(created_by=request.user)
        return qs

    def save_model(self, request, obj, form, change):
        if change:
            return  # bulk sends are immutable
        obj.created_by = request.user
        bulk.create_batch(obj, form.plan)
        self.message_user(
            request,
            f"Queued {obj.total} message(s)"
            + (f"; skipped {obj.skipped} invalid/duplicate." if obj.skipped else "."),
            messages.SUCCESS,
        )

    def save_related(self, request, form, formsets, change):
        pass  # no m2m; messages are created in save_model

    # ------------------------------------------------------------ display

    @display(description="Sent")
    def display_progress(self, obj):
        if not obj.total:
            return "-"
        return f"{obj._done} / {obj.total} ({100 * obj._done // obj.total}%)"

    @display(description="Waiting")
    def display_waiting(self, obj):
        return obj._waiting

    @display(description="Failed")
    def display_failed(self, obj):
        return obj._failed

    @display(description="Breakdown")
    def display_breakdown(self, obj):
        rows = (
            SmsMessage.objects.filter(batch=obj)
            .values("status")
            .annotate(c=Count("id"))
            .order_by("status")
        )
        return ", ".join(f"{r['status']}: {r['c']}" for r in rows) or "-"

    @display(description="Messages")
    def display_messages(self, obj):
        url = (
            reverse("admin:messaging_smsmessage_changelist")
            + f"?batch__id__exact={obj.pk}"
        )
        return format_html(
            '<a class="text-primary-600" href="{}">View messages →</a>', url
        )

    # ------------------------------------------------------------ actions

    def _cancel(self, request, batches):
        ids = SmsMessage.objects.filter(
            batch__in=batches, status__in=[Status.PENDING, Status.DISPATCHED]
        ).values_list("id", flat=True)
        done = 0
        for message_id in ids:
            try:
                services.cancel_message(message_id)
                done += 1
            except (services.NotCancellable, SmsMessage.DoesNotExist):
                pass
        self.message_user(
            request, f"Cancelled {done} waiting message(s).", messages.SUCCESS
        )

    @action(description="Cancel waiting messages", permissions=["change"])
    def cancel_waiting(self, request, queryset):
        self._cancel(request, queryset)

    @action(
        description="Cancel waiting messages",
        url_path="cancel-waiting",
        permissions=["change"],
    )
    def cancel_waiting_detail(self, request, object_id):
        from django.shortcuts import redirect

        batch = self.get_queryset(request).filter(pk=object_id).first()
        if batch is not None:
            self._cancel(request, [batch])
        return redirect(reverse("admin:messaging_smsbatch_change", args=[object_id]))
