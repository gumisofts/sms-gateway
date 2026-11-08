"""Picks a node for pending messages and pushes the job over the channel layer.

State is in the DB, so any number of backend processes can run this: the message
row lock (``select_for_update(skip_locked=True)``, ignored on SQLite) stops two
processes assigning the same message. Node load is read without locking, so
under concurrency the balancing is approximate.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from nodes.channels import send_sms_event, send_to_node
from nodes.models import Node, NodeSim

from . import services
from .models import SmsMessage, Status

logger = logging.getLogger(__name__)

# dispatch_pending gives up after this many messages in a row found no lane.
DISPATCH_MAX_MISSES = 25


def eligible_nodes(message: SmsMessage, allow_tried: bool = False):
    qs = Node.objects.filter(is_active=True, connected=True).filter(
        Q(owner__isnull=True) | Q(owner_id=message.owner_id)
    )
    if message.requested_node_id:
        qs = qs.filter(pk=message.requested_node_id)
    elif message.tried_nodes and not allow_tried:
        qs = qs.exclude(node_id__in=message.tried_nodes)
    # subscriptionId wins over simSlot, as on the node.
    if message.subscription_id is not None:
        qs = qs.filter(sims__subscription_id=message.subscription_id)
    elif message.sim_slot is not None:
        qs = qs.filter(sims__slot=message.sim_slot)
    return qs.distinct()


def _sim_matches(message: SmsMessage, sim: NodeSim) -> bool:
    if message.subscription_id is not None:
        return sim.subscription_id == message.subscription_id
    if message.sim_slot is not None:
        return sim.slot == message.sim_slot
    return True


def day_start(now=None):
    """Start of the current day in TIME_ZONE (the daily cap boundary)."""
    local = timezone.localtime(now or timezone.now())
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _parts_by(field: str, ids, since) -> dict:
    """Sum of parts dispatched since ``since``, grouped by ``field`` (sim/node)."""
    if not ids:
        return {}
    return dict(
        SmsMessage.objects.filter(
            **{f"{field}_id__in": ids, "dispatched_at__gte": since}
        )
        .values(f"{field}_id")
        .annotate(total=Sum("parts"))
        .values_list(f"{field}_id", "total")
    )


def sim_usage(sim_ids, now=None) -> tuple[dict, dict]:
    """(parts in the last minute, parts today) per SIM id."""
    now = now or timezone.now()
    return (
        _parts_by("sim", sim_ids, now - timedelta(minutes=1)),
        _parts_by("sim", sim_ids, day_start(now)),
    )


def _has_room(used: int, limit: int, parts: int) -> bool:
    """0 = unlimited. A message bigger than the whole limit still fits an idle lane."""
    if not limit:
        return True
    return used == 0 or used + parts <= limit


def pick_lane(
    message: SmsMessage, allow_tried: bool = False
) -> tuple[Node, NodeSim | None] | None:
    """Least loaded (node, SIM) lane with room for the message.

    Nodes that reported SIMs are scheduled per SIM: each enabled, present and
    unpaused SIM has its own parts-per-minute rate and daily cap. Nodes that
    reported no SIMs (e.g. no phone-state permission) are a single lane on
    the default SIM, paced by the node's own reported rate.
    """
    nodes = list(eligible_nodes(message, allow_tried).prefetch_related("sims"))
    if not nodes:
        return None
    now = timezone.now()
    parts = max(message.parts or 1, 1)
    node_ids = [n.pk for n in nodes]
    unacked = dict(
        SmsMessage.objects.filter(
            node_id__in=node_ids, status=Status.DISPATCHED, acked_at__isnull=True
        )
        .values("node_id")
        .annotate(c=Count("id"))
        .values_list("node_id", "c")
    )
    sims = [s for n in nodes for s in n.sims.all() if s.is_present]
    sim_minute, sim_today = sim_usage([s.pk for s in sims], now)
    node_minute = _parts_by("node", node_ids, now - timedelta(minutes=1))

    best = None
    for node in nodes:
        load = node.queue_depth + unacked.get(node.pk, 0)
        present = [s for s in node.sims.all() if s.is_present]
        if present:
            lanes = []
            for sim in present:
                if (
                    not sim.is_enabled
                    or sim.is_paused(now)
                    or not _sim_matches(message, sim)
                ):
                    continue
                minute = sim_minute.get(sim.pk, 0)
                today = sim_today.get(sim.pk, 0)
                if not _has_room(minute, sim.effective_rate_per_minute, parts):
                    continue
                if not _has_room(today, sim.effective_daily_limit, parts):
                    continue
                lanes.append(((load, minute, today, node.pk, sim.slot), sim))
        else:
            minute = node_minute.get(node.pk, 0)
            if not _has_room(minute, node.rate_per_minute, parts):
                continue
            lanes = [((load, minute, 0, node.pk, -1), None)]
        for key, sim in lanes:
            if best is None or key < best[0]:
                best = (key, node, sim)
    return (best[1], best[2]) if best else None


def pick_node(message: SmsMessage, allow_tried: bool = False) -> Node | None:
    lane = pick_lane(message, allow_tried)
    return lane[0] if lane else None


def push(message: SmsMessage, node_id: str) -> None:
    try:
        send_to_node(node_id, send_sms_event(message))
    except Exception:
        # Left dispatched/unacked; redispatch_unacked recovers it.
        logger.exception("pushing %s to %s failed", message.id, node_id)


def dispatch_message(message_id, allow_tried: bool = False) -> bool:
    """Assign one pending message to a node and push it. True if dispatched."""
    with transaction.atomic():
        message = (
            SmsMessage.objects.select_for_update(skip_locked=True)
            .filter(pk=message_id, status=Status.PENDING)
            .first()
        )
        if message is None:
            return False
        if message.dispatch_attempts >= settings.MESSAGE_MAX_DISPATCH_ATTEMPTS:
            services.fail(message, "Gave up after too many dispatch attempts.")
            return False
        lane = pick_lane(message, allow_tried)
        if lane is None:
            return False
        node, sim = lane
        message.status = Status.DISPATCHED
        message.node = node
        message.sim = sim
        message.dispatched_at = timezone.now()
        message.acked_at = None
        message.dispatch_attempts += 1
        message.save(
            update_fields=[
                "status",
                "node",
                "sim",
                "dispatched_at",
                "acked_at",
                "dispatch_attempts",
                "updated_at",
            ]
        )
        services.notify(message)
        node_id = node.node_id
        transaction.on_commit(lambda: push(message, node_id))
    return True


def dispatch_pending(limit: int = 200, allow_tried: bool = False) -> int:
    """Dispatch the oldest pending messages. Returns how many were assigned."""
    ids = list(
        SmsMessage.objects.filter(status=Status.PENDING)
        .order_by("created_at")
        .values_list("id", flat=True)[:limit]
    )
    count = misses = 0
    for message_id in ids:
        if dispatch_message(message_id, allow_tried):
            count += 1
            misses = 0
            continue
        misses += 1
        if not Node.objects.filter(is_active=True, connected=True).exists():
            break  # nobody online: stop early
        if misses >= DISPATCH_MAX_MISSES:
            break  # lanes are full (rate/daily caps): retry on the next run
    return count


def redispatch_unacked() -> int:
    """Handle jobs pushed to a node that never acked within the timeout.

    A node that is still connected gets the same job again (acks are idempotent
    by id, so no duplicate SMS); an offline node's job goes back to pending and
    to a different node.
    """
    cutoff = timezone.now() - timedelta(seconds=settings.MESSAGE_ACK_TIMEOUT_SEC)
    ids = list(
        SmsMessage.objects.filter(
            status=Status.DISPATCHED, acked_at__isnull=True, dispatched_at__lt=cutoff
        ).values_list("id", flat=True)[:500]
    )
    handled = 0
    for message_id in ids:
        released = False
        with transaction.atomic():
            message = (
                SmsMessage.objects.select_for_update(skip_locked=True)
                .select_related("node", "sim")
                .filter(pk=message_id, status=Status.DISPATCHED, acked_at__isnull=True)
                .first()
            )
            if message is None or message.dispatched_at >= cutoff:
                continue
            node = message.node
            if message.dispatch_attempts >= settings.MESSAGE_MAX_DISPATCH_ATTEMPTS:
                services.fail(message, "Node never acknowledged the message.")
            elif node is not None and node.is_active and node.connected:
                message.dispatched_at = timezone.now()
                message.dispatch_attempts += 1
                message.save(
                    update_fields=["dispatched_at", "dispatch_attempts", "updated_at"]
                )
                node_id = node.node_id
                transaction.on_commit(lambda m=message, n=node_id: push(m, n))
            else:
                tried = list(message.tried_nodes)
                if node is not None and node.node_id not in tried:
                    tried.append(node.node_id)
                message.tried_nodes = tried
                message.status = Status.PENDING
                message.node = None
                message.sim = None
                message.save(
                    update_fields=["tried_nodes", "status", "node", "sim", "updated_at"]
                )
                released = True
            handled += 1
        if released:
            dispatch_message(message_id)
    return handled
