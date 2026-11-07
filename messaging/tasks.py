import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.request

from celery import shared_task
from django.conf import settings

from . import dispatch, services
from .models import SmsMessage

logger = logging.getLogger(__name__)


@shared_task
def dispatch_pending_messages() -> int:
    """Safety net for new messages and for nodes that came online or freed up."""
    return dispatch.dispatch_pending(allow_tried=True)


@shared_task
def redispatch_unacked_messages() -> int:
    return dispatch.redispatch_unacked()


@shared_task
def expire_pending_messages() -> int:
    return services.expire_pending()


def webhook_payload(message: SmsMessage) -> dict:
    return {
        "id": str(message.id),
        "status": message.status,
        "to": message.to,
        "clientReference": message.client_reference,
        "node": message.node.node_id if message.node_id else None,
        "attempts": message.attempts,
        "errorCode": message.error_code,
        "errorMessage": message.error_message or None,
        "sentAt": message.sent_at.isoformat() if message.sent_at else None,
        "deliveredAt": (
            message.delivered_at.isoformat() if message.delivered_at else None
        ),
        "failedAt": message.failed_at.isoformat() if message.failed_at else None,
        "updatedAt": message.updated_at.isoformat(),
    }


@shared_task(
    bind=True,
    autoretry_for=(urllib.error.URLError, OSError),
    retry_backoff=10,
    retry_backoff_max=900,
    retry_jitter=True,
    max_retries=6,
)
def send_status_webhook(self, message_id: str) -> None:
    """POST the message's current state to its callback URL (at-least-once).

    TODO: the URL is client-supplied; for a multi-tenant deployment add an SSRF
    guard (block private/loopback targets or allow-list hosts).
    """
    message = SmsMessage.objects.select_related("node").filter(pk=message_id).first()
    if message is None or not message.callback_url:
        return
    body = json.dumps(webhook_payload(message)).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "sms-gateway/1"}
    if settings.WEBHOOK_SECRET:
        digest = hmac.new(settings.WEBHOOK_SECRET.encode(), body, hashlib.sha256)
        headers["X-Signature"] = f"sha256={digest.hexdigest()}"
    request = urllib.request.Request(
        message.callback_url, data=body, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass
    except urllib.error.HTTPError as exc:
        if exc.code < 500:
            logger.warning(
                "webhook %s -> %s: HTTP %s", message_id, message.callback_url, exc.code
            )
            return  # client error: retrying will not help
        raise
