from django import forms
from django.contrib import admin, messages
from django.contrib.admin import SimpleListFilter
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminSelectWidget

from . import roles
from .models import User

admin.site.unregister(Group)

ROLE_CHOICES = [("", "No admin access")] + [(r, r) for r in roles.ROLES]
ROLE_HELP = (
    "Gateway admin: everything incl. users. Operator: bulk send and their own "
    "messages, read-only phones/SIMs. Viewer: read-only, all messages."
)


def _role_field():
    return forms.ChoiceField(
        choices=ROLE_CHOICES,
        required=False,
        help_text=ROLE_HELP,
        widget=UnfoldAdminSelectWidget,
    )


class PhoneUserCreationForm(UserCreationForm):
    role = _role_field()

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("phone_number", "first_name")
        field_classes = {}


class PhoneUserChangeForm(UserChangeForm):
    role = _role_field()

    class Meta(UserChangeForm.Meta):
        model = User
        fields = "__all__"
        field_classes = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            current = roles.role_of(self.instance)
            self.fields["role"].initial = current if current in roles.ROLES else ""


class RoleFilter(SimpleListFilter):
    title = "role"
    parameter_name = "role"

    def lookups(self, request, model_admin):
        return [(r, r) for r in roles.ROLES] + [("none", "No role")]

    def queryset(self, request, queryset):
        if self.value() == "none":
            return queryset.exclude(groups__name__in=roles.ROLES)
        if self.value():
            return queryset.filter(groups__name=self.value())
        return queryset


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = PhoneUserChangeForm
    add_form = PhoneUserCreationForm
    change_password_form = AdminPasswordChangeForm

    list_display = (
        "phone_number",
        "first_name",
        "last_name",
        "display_role",
        "is_active",
        "last_login",
    )
    list_filter = (RoleFilter, "is_superuser", "is_active")
    search_fields = ("phone_number", "first_name", "last_name", "email")
    ordering = ("phone_number",)
    readonly_fields = ("last_login", "date_joined")
    actions = ["make_gateway_admin", "make_operator", "make_viewer", "remove_role"]

    fieldsets = (
        (None, {"fields": ("phone_number", "password")}),
        (_("Personal info"), {"fields": ("first_name", "last_name", "email")}),
        (_("Access"), {"fields": ("role", "is_active")}),
        (
            _("Advanced permissions"),
            {
                "classes": ["tab"],
                "fields": ("is_superuser", "groups", "user_permissions"),
            },
        ),
        (
            _("Important dates"),
            {"classes": ["tab"], "fields": ("last_login", "date_joined")},
        ),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone_number",
                    "first_name",
                    "role",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related("groups")
        if not request.user.is_superuser:
            # A gateway admin must not be able to take over a superuser
            # (e.g. by changing its password).
            qs = qs.filter(is_superuser=False)
        return qs

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_superuser and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            # Only superusers hand out superuser or raw permissions.
            fields += ["is_superuser", "groups", "user_permissions"]
        return fields

    def save_model(self, request, obj, form, change):
        # The inherited unique `username` column follows the phone number.
        if not obj.username:
            obj.username = obj.phone_number
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if "role" in form.cleaned_data:
            roles.set_role(form.instance, form.cleaned_data["role"] or None)

    @display(
        description="Role",
        label={
            "Superuser": "danger",
            roles.GATEWAY_ADMIN: "primary",
            roles.OPERATOR: "success",
            roles.VIEWER: "info",
            "None": "default",
        },
    )
    def display_role(self, obj):
        if obj.is_superuser:
            return "Superuser"
        names = {g.name for g in obj.groups.all()}
        return next((r for r in roles.ROLES if r in names), "None")

    def _assign(self, request, queryset, role):
        count = 0
        for user in queryset:
            roles.set_role(user, role)
            count += 1
        self.message_user(
            request,
            f"{count} user(s) now {role or 'without a role'}.",
            messages.SUCCESS,
        )

    @action(description="Make Gateway admin", permissions=["change"])
    def make_gateway_admin(self, request, queryset):
        self._assign(request, queryset, roles.GATEWAY_ADMIN)

    @action(description="Make Operator", permissions=["change"])
    def make_operator(self, request, queryset):
        self._assign(request, queryset, roles.OPERATOR)

    @action(description="Make Viewer", permissions=["change"])
    def make_viewer(self, request, queryset):
        self._assign(request, queryset, roles.VIEWER)

    @action(description="Remove role (no admin access)", permissions=["change"])
    def remove_role(self, request, queryset):
        self._assign(request, queryset, None)


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    pass
