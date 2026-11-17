"""Contacts admin. Contacts and groups are private: every staff user, superusers
included, sees and sends to only their own."""

from django import forms
from django.contrib import admin, messages
from django.db.models import Count
from django.shortcuts import redirect, render
from django.urls import reverse
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.widgets import (
    UnfoldAdminCheckboxSelectMultipleWidget,
    UnfoldAdminFileFieldWidget,
    UnfoldAdminTextareaWidget,
    UnfoldBooleanSwitchWidget,
)

from . import bulk, contacts
from .admin_bulk import PICKED_CONTACTS
from .models import Contact, ContactGroup


class OwnedAdmin(ModelAdmin):
    """Rows belong to the logged-in user; the owner is never a form field."""

    def get_queryset(self, request):
        return super().get_queryset(request).filter(owner=request.user)

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.request_user = request.user  # a new class per request
        return form

    def save_model(self, request, obj, form, change):
        if not change:
            obj.owner = request.user
        super().save_model(request, obj, form, change)


class ContactForm(forms.ModelForm):
    request_user = None

    class Meta:
        model = Contact
        fields = ("name", "phone", "groups")

    def clean_phone(self):
        phone = bulk.normalize_number(self.cleaned_data["phone"])
        if phone is None:
            raise forms.ValidationError("Not a valid phone number.")
        taken = Contact.objects.filter(owner=self.request_user, phone=phone)
        if taken.exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("You already have a contact with this number.")
        return phone


class ContactGroupForm(forms.ModelForm):
    request_user = None

    class Meta:
        model = ContactGroup
        fields = ("name",)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        taken = ContactGroup.objects.filter(owner=self.request_user, name=name)
        if taken.exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("You already have a group with this name.")
        return name


class ContactImportForm(forms.Form):
    csv_file = forms.FileField(
        required=False,
        label="CSV file",
        widget=UnfoldAdminFileFieldWidget,
        help_text=(
            "With a header row: a phone column (phone/msisdn/number/to/mobile) and "
            "a name column. Without one: a number and a name per line, in either "
            "order."
        ),
    )
    pasted = forms.CharField(
        required=False,
        label="Or paste lines",
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 8}),
        help_text="One contact per line, e.g. “Abebe, 0912345678”. A name is optional.",
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        required=False,
        label="Add to groups",
        widget=UnfoldAdminCheckboxSelectMultipleWidget,
        help_text="Every imported contact joins these, also ones you already had.",
    )
    update_existing = forms.BooleanField(
        required=False,
        label="Rename contacts I already have",
        widget=UnfoldBooleanSwitchWidget,
        help_text="Off: a number you already have keeps its current name.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["groups"].queryset = ContactGroup.objects.filter(owner=user)
        self.rows = []

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("csv_file")
        if upload:
            if upload.size > contacts.MAX_IMPORT_BYTES:
                self.add_error("csv_file", "CSV file is larger than 10 MB.")
                return cleaned
            self.rows += contacts.rows_from_bytes(upload.read())
        if cleaned.get("pasted", "").strip():
            self.rows += contacts.rows_from_bytes(cleaned["pasted"].encode())
        if not self.rows:
            raise forms.ValidationError("Choose a CSV file or paste some lines.")
        return cleaned


@admin.register(Contact)
class ContactAdmin(OwnedAdmin):
    form = ContactForm
    list_display = ("name", "phone", "display_groups", "created_at")
    list_filter = (("groups", admin.RelatedOnlyFieldListFilter),)
    search_fields = ("name", "phone")
    filter_horizontal = ("groups",)
    list_per_page = 100
    actions = ["send_sms"]
    actions_list = ["import_contacts"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("groups")

    @action(
        description="Import contacts",
        url_path="import",
        icon="upload",
        permissions=["add"],
    )
    def import_contacts(self, request):
        changelist_url = reverse("admin:messaging_contact_changelist")
        posted = request.method == "POST"
        form = ContactImportForm(
            request.POST if posted else None,
            request.FILES if posted else None,
            user=request.user,
        )
        if posted and form.is_valid():
            result = contacts.import_contacts(
                request.user,
                form.rows,
                form.cleaned_data["groups"],
                form.cleaned_data["update_existing"],
            )
            self.message_user(
                request,
                result.summary(),
                (
                    messages.SUCCESS
                    if result.created or result.updated
                    else messages.WARNING
                ),
            )
            return redirect(changelist_url)
        return render(
            request,
            "admin/messaging/contact/import.html",
            {
                **self.admin_site.each_context(request),
                "title": "Import contacts",
                "opts": self.model._meta,
                "form": form,
                "media": self.media + form.media,
                "changelist_url": changelist_url,
            },
        )

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        if db_field.name == "groups":
            kwargs["queryset"] = ContactGroup.objects.filter(owner=request.user)
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    @display(description="Groups")
    def display_groups(self, obj):
        return ", ".join(g.name for g in obj.groups.all()) or "-"

    @action(description="Send SMS to selected contacts")
    def send_sms(self, request, queryset):
        if not request.user.has_perm("messaging.add_smsbatch"):
            self.message_user(request, "You cannot send bulk SMS.", messages.ERROR)
            return None
        # In the session, not the URL: a selection can be thousands of ids.
        request.session[PICKED_CONTACTS] = list(queryset.values_list("pk", flat=True))
        return redirect(reverse("admin:messaging_smsbatch_add") + "?contacts=picked")


@admin.register(ContactGroup)
class ContactGroupAdmin(OwnedAdmin):
    form = ContactGroupForm
    list_display = ("name", "display_contacts", "created_at")
    search_fields = ("name",)
    actions = ["send_sms"]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_contacts=Count("contacts"))

    @display(description="Contacts", ordering="_contacts")
    def display_contacts(self, obj):
        return obj._contacts

    @action(description="Send SMS to selected groups")
    def send_sms(self, request, queryset):
        if not request.user.has_perm("messaging.add_smsbatch"):
            self.message_user(request, "You cannot send bulk SMS.", messages.ERROR)
            return None
        ids = ",".join(str(pk) for pk in queryset.values_list("pk", flat=True))
        return redirect(
            reverse("admin:messaging_smsbatch_add") + f"?contact_groups={ids}"
        )
