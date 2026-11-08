"""Node lifecycle: registration, connection state, heartbeats, config.

Everything here is synchronous ORM code; the WebSocket consumer wraps calls
with ``database_sync_to_async``. State lives in the DB so any backend process
sees it.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import Node, NodeSim, NodeToken


class NodeDisabled(Exception):
    pass


def sync_sims(node: Node, sims: list[dict]) -> None:
    """Make the node's SIM rows match the reported list (keyed by slot).

    Rows are never deleted here: a SIM missing from the report is marked
    ``is_present=False`` so its admin settings and pause state survive. A
    different subscription in a slot means a swapped SIM: its health resets.
    """
    seen = []
    for sim in sims or []:
        slot = sim.get("slot")
        if slot is None:
            continue
        seen.append(slot)
        subscription_id = sim.get("subscriptionId")
        row = NodeSim.objects.filter(node=node, slot=slot).first()
        if row is None:
            NodeSim.objects.create(
                node=node,
                slot=slot,
                subscription_id=subscription_id,
                carrier=sim.get("carrier") or "",
            )
            continue
        if row.subscription_id != subscription_id:
            row.consecutive_failures = 0
            row.paused_until = None
            row.pause_reason = ""
            row.last_error = ""
        row.subscription_id = subscription_id
        row.carrier = sim.get("carrier") or ""
        row.is_present = True
        row.save()
    node.sims.exclude(slot__in=seen).update(is_present=False)


@transaction.atomic
def register_node(data: dict, owner=None) -> tuple[Node, str]:
    """Create (or reuse, for ``previousNodeId``) a node and rotate its token."""
    node = None
    previous = data.get("previousNodeId")
    if previous:
        node = Node.objects.select_for_update().filter(node_id=previous).first()
        if node is not None and not node.is_active:
            raise NodeDisabled(node.node_id)
    if node is None:
        node = Node()

    node.device_name = data.get("deviceName", "")
    node.manufacturer = data.get("manufacturer", "")
    node.model = data.get("model", "")
    node.android_version = data.get("androidVersion", "")
    node.sdk_int = data.get("sdkInt")
    node.app_version = data.get("appVersion", "")
    if owner is not None and node.owner_id is None:
        node.owner = owner
    node.save()
    sync_sims(node, data.get("sims", []))
    token = NodeToken.issue(node)
    return node, token


def _ms_to_dt(ms) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def mark_connected(node_pk: int, connection_id: str) -> None:
    """Newest connection wins: record it as the owner of the node's session."""
    now = timezone.now()
    Node.objects.filter(pk=node_pk).update(
        connected=True,
        connection_id=connection_id,
        connected_at=now,
        last_seen_at=now,
    )


def mark_disconnected(node_pk: int, connection_id: str) -> bool:
    """Only the connection that currently owns the node may mark it offline."""
    return bool(
        Node.objects.filter(pk=node_pk, connection_id=connection_id).update(
            connected=False, connection_id=""
        )
    )


def record_heartbeat(node_pk: int, data: dict) -> None:
    node = Node.objects.get(pk=node_pk)
    now = timezone.now()
    node.last_seen_at = now
    node.last_heartbeat_at = now
    node.battery_percent = _clamp(data.get("batteryPercent"), 0, 100)
    node.charging = data.get("charging")
    node.signal_level = _clamp(data.get("signalLevel"), 0, 4)
    node.queue_depth = max(int(data.get("queueDepth") or 0), 0)
    node.rate_per_minute = max(int(data.get("ratePerMinute") or 0), 0)
    node.save(
        update_fields=[
            "last_seen_at",
            "last_heartbeat_at",
            "battery_percent",
            "charging",
            "signal_level",
            "queue_depth",
            "rate_per_minute",
            "updated_at",
        ]
    )
    if "sims" in data:
        sync_sims(node, data["sims"])
    limit = data.get("smsLimit")
    if isinstance(limit, dict):
        raised = limit.get("raised")
        max_count = limit.get("maxCount")
        node.sms_limit_raised = raised if isinstance(raised, bool) else None
        node.sms_limit_max_count = (
            max(int(max_count), 0) if isinstance(max_count, int) else None
        )
        node.save(update_fields=["sms_limit_raised", "sms_limit_max_count"])


def _clamp(value, low, high):
    if value is None:
        return None
    try:
        return min(max(int(value), low), high)
    except (TypeError, ValueError):
        return None


def config_payload(node: Node) -> dict:
    return {
        "ratePerMinute": node.desired_rate_per_minute,
        "heartbeatIntervalSec": node.desired_heartbeat_interval_sec
        or settings.NODE_HEARTBEAT_INTERVAL_SEC,
    }


def mark_stale_offline() -> int:
    """Flip nodes that stopped heartbeating to offline. Returns how many."""
    now = timezone.now()
    base = timedelta(seconds=settings.NODE_OFFLINE_AFTER_SEC)
    candidates = Node.objects.filter(connected=True).filter(
        Q(last_seen_at__isnull=True) | Q(last_seen_at__lt=now - base)
    )
    count = 0
    for node in candidates:
        interval = node.desired_heartbeat_interval_sec or 0
        limit = max(base, timedelta(seconds=3 * interval))
        if node.last_seen_at is None or node.last_seen_at < now - limit:
            count += Node.objects.filter(
                pk=node.pk, last_seen_at=node.last_seen_at, connected=True
            ).update(connected=False, connection_id="")
    return count
