"""Staff roles for the admin, as Django groups kept in sync on every migrate.

* Gateway admin: everything about the gateway (phones, SIMs, messages, bulk
  sends, API keys) and managing users; sees all messages.
* Operator: bulk send, cancel/retry their own messages, watch phones and SIMs
  (read-only). Sees only messages and bulk sends they own.
* Viewer: read-only dashboard and lists, all messages.

Superusers bypass all of this. A user's role is their group membership; the
admin's user form sets it and turns ``is_staff`` on/off with it.
"""

from django.contrib.auth.models import Group, Permission

GATEWAY_ADMIN = "Gateway admin"
OPERATOR = "Operator"
VIEWER = "Viewer"
ROLES = (GATEWAY_ADMIN, OPERATOR, VIEWER)

VIEW_ALL = "messaging.view_all_messages"

_GATEWAY_MODELS = [
    "messaging.smsmessage",
    "messaging.smsbatch",
    "messaging.apikey",
    "nodes.node",
    "nodes.nodesim",
    "nodes.nodetoken",
]

ROLE_PERMISSIONS = {
    GATEWAY_ADMIN: [
        *[
            f"{m}.{a}"
            for m in _GATEWAY_MODELS
            for a in ("view", "add", "change", "delete")
        ],
        "accounts.user.view",
        "accounts.user.add",
        "accounts.user.change",
        "auth.group.view",
        VIEW_ALL,
    ],
    OPERATOR: [
        "messaging.smsmessage.view",
        "messaging.smsmessage.change",  # cancel / retry actions
        "messaging.smsbatch.view",
        "messaging.smsbatch.add",  # bulk send
        "messaging.smsbatch.change",  # cancel a batch
        "nodes.node.view",
        "nodes.nodesim.view",
    ],
    VIEWER: [
        "messaging.smsmessage.view",
        "messaging.smsbatch.view",
        "nodes.node.view",
        "nodes.nodesim.view",
        VIEW_ALL,
    ],
}


def _permission(spec: str) -> Permission | None:
    """'app.model.action' (model permission) or 'app.codename' (custom)."""
    parts = spec.split(".")
    if len(parts) == 3:
        app, model, action = parts
        codename = f"{action}_{model}"
    else:
        app, codename = parts
    return Permission.objects.filter(
        content_type__app_label=app, codename=codename
    ).first()


def sync_roles(**kwargs) -> None:
    """Create the role groups and set exactly their permissions (idempotent)."""
    for role, specs in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)
        perms = [p for p in (_permission(s) for s in specs) if p is not None]
        group.permissions.set(perms)


def role_of(user) -> str:
    if user.is_superuser:
        return "Superuser"
    names = set(user.groups.values_list("name", flat=True))
    return next((r for r in ROLES if r in names), "None")


def set_role(user, role: str | None) -> None:
    """Make ``role`` (or no role) the user's only role group; staff follows it."""
    user.groups.remove(*Group.objects.filter(name__in=ROLES))
    if role:
        user.groups.add(Group.objects.get(name=role))
    user.is_staff = bool(role) or user.is_superuser
    user.save(update_fields=["is_staff"])


def can_view_all(user) -> bool:
    return user.is_superuser or user.has_perm(VIEW_ALL)
