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
from django.db.models import Count, Q
from django.utils import timezone

from nodes.channels import send_sms_event, send_to_node
from nodes.models import Node

from . import services
from .models import SmsMessage, Status

logger = logging.getLogger(__name__)


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


def pick_node(message: SmsMessage, allow_tried: bool = False) -> Node | None:
    """Least loaded eligible node that is not at its per-minute rate."""
    nodes = list(eligible_nodes(message, allow_tried))
    if not nodes:
        return None
    ids = [n.pk for n in nodes]
    unacked = dict(
        SmsMessage.objects.filter(
            node_id__in=ids, status=Status.DISPATCHED, acked_at__isnull=True
        )
        .values("node_id")
        .annotate(c=Count("id"))
        .values_list("node_id", "c")
    )
    minute_ago = timezone.now() - timedelta(minutes=1)
    recent = dict(
        SmsMessage.objects.filter(node_id__in=ids, dispatched_at__gte=minute_ago)
        .values("node_id")
        .annotate(c=Count("id"))
        .values_list("node_id", "c")
    )
    best = None
    for node in nodes:
        sent_last_minute = recent.get(node.pk, 0)
        if node.rate_per_minute and sent_last_minute >= node.rate_per_minute:
            continue
        load = node.queue_depth + unacked.get(node.pk, 0)
        key = (load, sent_last_minute, node.pk)
        if best is None or key < best[0]:
            best = (key, node)
    return best[1] if best else None


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
        node = pick_node(message, allow_tried)
        if node is None:
            return False
        message.status = Status.DISPATCHED
        message.node = node
        message.dispatched_at = timezone.now()
        message.acked_at = None
        message.dispatch_attempts += 1
        message.save(
            update_fields=[
                "status",
                "node",
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
    count = 0
    for message_id in ids:
        if dispatch_message(message_id, allow_tried):
            count += 1
        elif not Node.objects.filter(is_active=True, connected=True).exists():
            break  # nobody online: stop early
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
                .select_related("node")
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
                message.save(
                    update_fields=["tried_nodes", "status", "node", "updated_at"]
                )
                released = True
            handled += 1
        if released:
            dispatch_message(message_id)
    return handled
