"""Helpers for talking to a node's WebSocket group through the channel layer."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer


def group_name(node_id: str) -> str:
    return f"node_{node_id}"


def send_to_node(node_id: str, event: dict) -> None:
    """Sync helper (views, tasks, services). Safe to call from DB-thread code."""
    async_to_sync(get_channel_layer().group_send)(group_name(node_id), event)


def send_sms_event(message) -> dict:
    payload = {
        "type": "send_sms",
        "id": str(message.id),
        "to": message.to,
        "body": message.body,
    }
    # The dispatcher's SIM lane wins; otherwise what the client requested.
    # subscriptionId wins on the node; omit what is not set.
    sim = message.sim if message.sim_id else None
    slot = sim.slot if sim else message.sim_slot
    subscription_id = sim.subscription_id if sim else message.subscription_id
    if slot is not None:
        payload["simSlot"] = slot
    if subscription_id is not None:
        payload["subscriptionId"] = subscription_id
    return {"type": "node.send_sms", "payload": payload}


def config_event(**fields) -> dict:
    return {
        "type": "node.config",
        "payload": {
            "type": "config",
            **{k: v for k, v in fields.items() if v is not None},
        },
    }
