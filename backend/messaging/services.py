"""Message state changes. All writes that move a message go through here.

The status rules themselves live in ``SmsMessage.apply_node_status``; the other
transitions use row locks (``select_for_update``, a no-op on SQLite) so two
backend processes cannot both move the same message.
"""

import logging
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from nodes.models import Node, NodeSim

from .models import NODE_STATUSES, TERMINAL, SmsMessage, Status
from .segments import count_parts

logger = logging.getLogger(__name__)


class NotCancellable(Exception):
    pass


# ----------------------------------------------------------------- helpers


def _lock(message_id) -> SmsMessage | None:
    try:
        message_id = uuid.UUID(str(message_id))
    except ValueError:
        return None
    return SmsMessage.objects.select_for_update().filter(pk=message_id).first()


def notify(message: SmsMessage) -> None:
    """Queue a status webhook (after commit) if the message has a callback URL."""
    if not message.callback_url:
        return
    from .tasks import send_status_webhook

    transaction.on_commit(lambda: send_status_webhook.delay(str(message.id)))


def fail(message: SmsMessage, error_message: str, error_code=None) -> None:
    """Mark a message failed. Caller holds the lock and checked it is not terminal."""
    message.status = Status.FAILED
    message.failed_at = timezone.now()
    message.error_code = error_code
    message.error_message = error_message[:255]
    message.save()
    notify(message)


def _node_time(ms) -> datetime:
    """Node timestamp (ms) if plausible, else server time. Never in the future."""
    now = timezone.now()
    try:
        at = datetime.fromtimestamp(int(ms) / 1000, tz=dt_timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return now
    return min(at, now)


# ------------------------------------------------------------- client side


def create_message(owner, data: dict) -> tuple[SmsMessage, bool]:
    """Create a pending message and schedule dispatch. Returns (message, created).

    ``client_reference`` makes creation idempotent per owner: a repeat returns
    the original message with ``created=False``.
    """
    reference = data.get("client_reference") or None
    if reference:
        existing = SmsMessage.objects.filter(
            owner=owner, client_reference=reference
        ).first()
        if existing:
            return existing, False
    try:
        with transaction.atomic():
            message = SmsMessage.objects.create(
                owner=owner,
                to=data["to"],
                body=data["body"],
                parts=count_parts(data["body"]),
                requested_node=data.get("requested_node"),
                sim_slot=data.get("sim_slot"),
                subscription_id=data.get("subscription_id"),
                client_reference=reference,
                callback_url=data.get("callback_url") or "",
            )
    except IntegrityError:
        # Lost a race on (owner, client_reference).
        return SmsMessage.objects.get(owner=owner, client_reference=reference), False
    schedule_dispatch(message.id)
    # Outside an open transaction the dispatch above already ran: show its result.
    message.refresh_from_db()
    return message, True


def schedule_dispatch(*message_ids) -> None:
    from . import dispatch

    def run():
        for message_id in message_ids:
            try:
                dispatch.dispatch_message(message_id)
            except Exception:
                # The beat task picks it up later.
                logger.exception("dispatch of %s failed", message_id)

    transaction.on_commit(run)


@transaction.atomic
def cancel_message(message_id) -> SmsMessage:
    """Cancel while not yet acked by a node (pending, or dispatched and unacked).

    The protocol has no cancel frame, so a job already pushed to a node may still
    be sent by it; the late acks/updates are ignored.
    """
    message = _lock(message_id)
    if message is None:
        raise SmsMessage.DoesNotExist
    cancellable = message.status == Status.PENDING or (
        message.status == Status.DISPATCHED and message.acked_at is None
    )
    if not cancellable:
        raise NotCancellable(message.status)
    message.status = Status.CANCELLED
    message.save(update_fields=["status", "updated_at"])
    notify(message)
    return message


def expire_pending() -> int:
    now = timezone.now()
    cutoff = now - timedelta(seconds=settings.MESSAGE_PENDING_TTL_SEC)
    ids = list(
        SmsMessage.objects.filter(status=Status.PENDING)
        .filter(
            Q(expires_at__lt=now) | Q(expires_at__isnull=True, created_at__lt=cutoff)
        )
        .order_by("created_at")
        .values_list("id", flat=True)[:1000]
    )
    count = 0
    for message_id in ids:
        with transaction.atomic():
            message = _lock(message_id)
            if message is None or message.status != Status.PENDING:
                continue
            message.status = Status.EXPIRED
            message.error_message = "No node available before the message expired."
            message.save(update_fields=["status", "error_message", "updated_at"])
            notify(message)
            count += 1
    return count


# --------------------------------------------------------------- node side


def unacked_for_node(node: Node) -> list[SmsMessage]:
    """Jobs assigned to the node that it has not acked yet (resent on connect)."""
    return list(
        SmsMessage.objects.filter(
            node=node, status=Status.DISPATCHED, acked_at__isnull=True
        )
        # send_sms_event reads .sim from async code: no lazy queries there.
        .select_related("sim").order_by("dispatched_at")
    )


@transaction.atomic
def handle_ack(node: Node, message_id, accepted: bool, reason=None) -> bool:
    """Process a node `ack`. Returns True if a message went back to pending and
    the caller should run the dispatcher."""
    message = _lock(message_id)
    if message is None or message.node_id != node.pk or message.is_terminal:
        return False

    if accepted:
        # Duplicate acks (resend after reconnect) are harmless.
        if message.acked_at is None:
            message.acked_at = timezone.now()
        if message.status == Status.DISPATCHED:
            message.status = Status.QUEUED
        message.save(update_fields=["acked_at", "status", "updated_at"])
        notify(message)
        return False

    if message.acked_at is not None:
        return False  # the node already owns it; a late reject changes nothing

    if reason == "invalid_job":
        # Another node would reject it too.
        fail(message, "Rejected by node: invalid_job")
        return False

    tried = list(message.tried_nodes)
    if node.node_id not in tried:
        tried.append(node.node_id)
    message.tried_nodes = tried
    message.status = Status.PENDING
    message.node = None
    message.sim = None
    message.save(update_fields=["tried_nodes", "status", "node", "sim", "updated_at"])
    return True


@transaction.atomic
def apply_node_status(node: Node, data: dict) -> bool:
    """Process a node `status_update`. Returns True if the message changed."""
    status = data.get("status")
    if status not in {s.value for s in NODE_STATUSES}:
        return False
    message = _lock(data.get("id"))
    if message is None or message.node_id != node.pk:
        # Unknown job, or re-assigned to another node since.
        return False
    parts = data.get("parts")
    if isinstance(parts, int) and 0 < parts <= 255 and parts != message.parts:
        # The phone's real count (divideMessage) replaces the estimate.
        message.parts = parts
        message.save(update_fields=["parts", "updated_at"])
    changed = message.apply_node_status(
        status,
        data.get("attempts"),
        error_code=data.get("errorCode"),
        error_message=data.get("errorMessage") or "",
        at=_node_time(data.get("timestamp")),
    )
    if changed:
        message.save()
        notify(message)
        if message.sim_id and message.status in (
            Status.SENT,
            Status.DELIVERED,
            Status.FAILED,
        ):
            record_sim_result(message.sim_id, message)
    return changed


def record_sim_result(sim_id: int, message: SmsMessage) -> None:
    """Track SIM health: consecutive failed messages auto-pause the SIM.

    Only the final outcome of a message counts (the phone already retried
    transient errors). Any sent/delivered message resets the streak.
    """
    sim = NodeSim.objects.select_for_update().filter(pk=sim_id).first()
    if sim is None:
        return
    now = timezone.now()
    if message.status == Status.FAILED:
        sim.consecutive_failures += 1
        sim.last_failure_at = now
        sim.last_error = (message.error_message or f"error {message.error_code}")[:255]
        threshold = settings.SIM_PAUSE_AFTER_FAILURES
        if (
            threshold
            and sim.consecutive_failures >= threshold
            and not sim.is_paused(now)
        ):
            sim.paused_until = now + timedelta(minutes=settings.SIM_PAUSE_MINUTES)
            sim.pause_reason = (
                f"{sim.consecutive_failures} failed messages in a row; "
                f"last error: {sim.last_error}"
            )[:255]
            logger.warning("SIM %s auto-paused: %s", sim, sim.pause_reason)
    elif (
        sim.consecutive_failures
        or sim.last_success_at is None
        or (now - sim.last_success_at) > timedelta(seconds=30)
    ):
        # Throttled write: at most every 30 s per SIM unless a streak resets.
        sim.consecutive_failures = 0
        sim.last_success_at = now
    else:
        return
    sim.save()


def resume_sim(sim: NodeSim) -> None:
    """Clear an (auto-)pause; the streak restarts from zero."""
    sim.paused_until = None
    sim.pause_reason = ""
    sim.consecutive_failures = 0
    sim.save(update_fields=["paused_until", "pause_reason", "consecutive_failures"])


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL
