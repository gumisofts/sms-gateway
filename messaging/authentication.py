from datetime import timedelta

from django.utils import timezone
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework import authentication, exceptions

from .models import ApiKey


class ApiKeyAuthentication(authentication.BaseAuthentication):
    """`X-API-Key: sgk_...`. ``request.auth`` is the ApiKey row."""

    header = "HTTP_X_API_KEY"

    def authenticate(self, request):
        key = request.META.get(self.header)
        if not key:
            return None
        api_key = (
            ApiKey.objects.select_related("user")
            .filter(key_hash=ApiKey.hash_key(key), revoked_at__isnull=True)
            .first()
        )
        if api_key is None or not api_key.user.is_active:
            raise exceptions.AuthenticationFailed("Invalid API key.")
        now = timezone.now()
        # Avoid a write per request.
        if api_key.last_used_at is None or now - api_key.last_used_at > timedelta(
            minutes=1
        ):
            ApiKey.objects.filter(pk=api_key.pk).update(last_used_at=now)
        return api_key.user, api_key


class ApiKeyScheme(OpenApiAuthenticationExtension):
    target_class = "messaging.authentication.ApiKeyAuthentication"
    name = "ApiKeyAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "header", "name": "X-API-Key"}
