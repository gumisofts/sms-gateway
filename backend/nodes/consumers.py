import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from messaging import dispatch
from messaging import services as message_services

from . import services
from .channels import config_event, group_name, send_sms_event
from .models import Node

logger = logging.getLogger(__name__)

# Close codes (application range). The node reconnects on any of them.
CLOSE_REPLACED = 4000
CLOSE_DISABLED = 4403


class NodeConsumer(AsyncJsonWebsocketConsumer):
    """Session with one Android node. See docs/PROTOCOL.md section 2.

    ``scope["node"]`` is set by NodeTokenAuthMiddleware. All state is in the DB
    and the channel layer, so several backend processes can serve nodes.
    """

    node: Node | None = None
    group: str = ""

    # ------------------------------------------------------------ lifecycle

    async def connect(self):
        node = self.scope.get("node")
        url_id = self.scope["url_route"]["kwargs"]["node_id"]
        if node is None or node.node_id != url_id or not node.is_active:
            # Closing before accept makes the server answer the upgrade with 403.
            await self.close(code=CLOSE_DISABLED)
            return

        self.node = node
        self.group = group_name(node.node_id)
        await self.accept()

        await database_sync_to_async(services.mark_connected)(
            node.pk, self.channel_name
        )
        # Newest wins: tell older connections of this node to go away. We are
        # not in the group yet, so we do not receive our own notice.
        await self.channel_layer.group_send(
            self.group,
            {"type": "node.replaced", "new_channel": self.channel_name},
        )
        await self.channel_layer.group_add(self.group, self.channel_name)

        await self._send_config()
        await self._resend_unacked()
        await self._dispatch_pending()

    async def disconnect(self, code):
        if self.node is None:
            return
        await self.channel_layer.group_discard(self.group, self.channel_name)
        await database_sync_to_async(services.mark_disconnected)(
            self.node.pk, self.channel_name
        )

    # ----------------------------------------------------- node -> server

    async def receive_json(self, content, **kwargs):
        if not isinstance(content, dict):
            return
        handler = {
            "ack": self._on_ack,
            "status_update": self._on_status_update,
            "heartbeat": self._on_heartbeat,
        }.get(content.get("type"))
        if handler is None:
            return  # unknown types are ignored, per protocol
        try:
            await handler(content)
        except Exception:
            # A bad frame must not kill the session.
            logger.exception("node %s: failed handling %s", self.node.node_id, content)

    async def _on_ack(self, content):
        needs_dispatch = await database_sync_to_async(message_services.handle_ack)(
            self.node,
            content.get("id"),
            bool(content.get("accepted")),
            content.get("reason"),
        )
        if needs_dispatch:
            await self._dispatch_pending()

    async def _on_status_update(self, content):
        await database_sync_to_async(message_services.apply_node_status)(
            self.node, content
        )

    async def _on_heartbeat(self, content):
        await database_sync_to_async(services.record_heartbeat)(self.node.pk, content)
        # Capacity may have freed up since the last attempt.
        await self._dispatch_pending()

    # ----------------------------------------------------- server -> node

    async def _send_config(self):
        node = await Node.objects.aget(pk=self.node.pk)
        await self.send_json(config_event(**services.config_payload(node))["payload"])

    async def _resend_unacked(self):
        jobs = await database_sync_to_async(message_services.unacked_for_node)(
            self.node
        )
        for message in jobs:
            await self.send_json(send_sms_event(message)["payload"])

    async def _dispatch_pending(self):
        await database_sync_to_async(dispatch.dispatch_pending)()

    # ------------------------------------------------- channel layer events

    async def node_send_sms(self, event):
        await self.send_json(event["payload"])

    async def node_config(self, event):
        await self.send_json(event["payload"])

    async def node_replaced(self, event):
        if event["new_channel"] != self.channel_name:
            await self.close(code=CLOSE_REPLACED)

    async def node_disconnect(self, event):
        await self.close(code=CLOSE_DISABLED)
