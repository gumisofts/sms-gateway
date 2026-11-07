"""Channels middleware that authenticates a node from its bearer token."""

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware

from .models import NodeToken


class NodeTokenAuthMiddleware(BaseMiddleware):
    """Puts ``scope["node"]`` (a Node or None) from ``Authorization: Bearer``.

    Rejecting is left to the consumer, which closes before ``accept`` so the
    client sees HTTP 403 on the upgrade.
    """

    async def __call__(self, scope, receive, send):
        scope = dict(scope)
        scope["node"] = await self.authenticate(scope)
        return await super().__call__(scope, receive, send)

    @staticmethod
    def bearer_token(scope) -> str:
        for name, value in scope.get("headers", []):
            if name.lower() == b"authorization":
                parts = value.decode("latin-1").split(None, 1)
                if len(parts) == 2 and parts[0].lower() == "bearer":
                    return parts[1].strip()
        return ""

    @database_sync_to_async
    def _lookup(self, token):
        return NodeToken.authenticate(token)

    async def authenticate(self, scope):
        return await self._lookup(self.bearer_token(scope))
