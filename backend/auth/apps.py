from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "auth"
    label = "accounts"

    def ready(self):
        from .roles import sync_roles

        # post_migrate fires once per app; permissions of later apps exist by
        # the last call, and sync_roles is idempotent.
        post_migrate.connect(sync_roles, dispatch_uid="accounts.sync_roles")
