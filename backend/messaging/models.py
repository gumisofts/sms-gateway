import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Status(models.TextChoices):
    PENDING = "pending"
    DISPATCHED = "dispatched"  # pushed to a node, not acked yet
    QUEUED = "queued"  # acked / queued on the node
    SENDING = "sending"
    SENT = "sent"  # handed to the network
    DELIVERED = "delivered"  # carrier delivery report
    FAILED = "failed"
    EXPIRED = "expired"  # sat pending past the TTL
    CANCELLED = "cancelled"


TERMINAL = frozenset(
    {Status.DELIVERED, Status.FAILED, Status.EXPIRED, Status.CANCELLED}
)
IN_FLIGHT = (Status.DISPATCHED, Status.QUEUED, Status.SENDING)

# Statuses a node may report (docs/PROTOCOL.md).
NODE_STATUSES = frozenset(
    {Status.QUEUED, Status.SENDING, Status.SENT, Status.FAILED, Status.DELIVERED}
)


class SmsBatch(models.Model):
    """A bulk send from the admin: one row per send, messages link back to it."""

    name = models.CharField(max_length=120)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sms_batches",
        help_text="Owner of the batch's messages.",
    )
    body = models.TextField(
        help_text="Message text. {column} placeholders are filled from the CSV."
    )
    total = models.PositiveIntegerField(default=0, editable=False)
    skipped = models.PositiveIntegerField(
        default=0, editable=False, help_text="Invalid or duplicate recipients."
    )
    requested_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="Send only through this phone (optional).",
    )
    sim_slot = models.IntegerField(
        null=True, blank=True, help_text="Send only from this SIM slot (optional)."
    )
    callback_url = models.URLField(max_length=500, blank=True)
    valid_hours = models.PositiveIntegerField(
        default=24,
        help_text="Messages still waiting after this many hours expire.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "bulk send"
        verbose_name_plural = "bulk sends"

    def __str__(self):
        return f"{self.name} ({self.total})"


class SmsMessage(models.Model):
    # The UUID is the protocol job `id` (the node's idempotency key).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sms_messages"
    )
    to = models.CharField(max_length=32)
    body = models.TextField()

    # What the client asked for.
    requested_node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="requested_messages",
    )
    sim_slot = models.IntegerField(null=True, blank=True)
    subscription_id = models.IntegerField(null=True, blank=True)
    client_reference = models.CharField(max_length=128, null=True, blank=True)
    callback_url = models.URLField(max_length=500, blank=True)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING
    )
    node = models.ForeignKey(
        "nodes.Node",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    sim = models.ForeignKey(
        "nodes.NodeSim",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
        help_text="SIM lane the message was dispatched on.",
    )
    parts = models.PositiveSmallIntegerField(
        default=1,
        help_text="SMS parts: estimated on creation, then as reported by the phone.",
    )
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Expire if still pending at this time (default: created + pending TTL).",
    )
    batch = models.ForeignKey(
        SmsBatch,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messages",
    )
    # node_ids that rejected or timed out, skipped on re-dispatch.
    tried_nodes = models.JSONField(default=list, blank=True)
    dispatch_attempts = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(
        default=0, help_text="Send attempts reported by the node."
    )
    error_code = models.IntegerField(null=True, blank=True)
    error_message = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    acked_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("view_all_messages", "Can see every user's messages and batches"),
        ]
        indexes = [
            models.Index(
                fields=["status", "created_at"], name="msg_status_created_idx"
            ),
            models.Index(fields=["batch", "status"], name="msg_batch_status_idx"),
            models.Index(fields=["node", "status"], name="msg_node_status_idx"),
            models.Index(
                fields=["sim", "dispatched_at"], name="msg_sim_dispatched_idx"
            ),
            models.Index(fields=["owner", "-created_at"], name="msg_owner_created_idx"),
            models.Index(
                fields=["dispatched_at"],
                condition=Q(status="dispatched"),
                name="msg_unacked_idx",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "client_reference"], name="uniq_owner_client_reference"
            )
        ]

    def __str__(self):
        return f"{self.id} -> {self.to} [{self.status}]"

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    # ------------------------------------------------------------------
    # The one place that decides whether a node-reported status is applied.
    # Callers must hold a row lock (select_for_update) and save afterwards;
    # see services.apply_node_status.
    # ------------------------------------------------------------------
    def apply_node_status(
        self, status, attempts, error_code=None, error_message="", at=None
    ) -> bool:
        """Apply a node status update. Returns True if the row changed.

        Idempotent: replaying the same (status, attempts) is a no-op. Terminal
        states (delivered, failed, expired, cancelled) never change. `queued`
        and `sending` repeat on retries; a lower `attempts` than known is stale.
        """
        status = Status(status)
        if status not in NODE_STATUSES:
            raise ValueError(f"not a node status: {status}")
        at = at or timezone.now()
        attempts = max(int(attempts or 0), 0)

        if self.is_terminal:
            return False
        if status == self.status and attempts <= self.attempts:
            return False  # duplicate / stale replay

        if status in (Status.QUEUED, Status.SENDING):
            # The node re-queues a failed try with the same attempts count
            # (sending#1 -> queued#1 -> sending#2), so equal attempts may move
            # between these two states; only older attempts and `sent` are stale.
            if self.status == Status.SENT or attempts < self.attempts:
                return False

        self.status = status
        self.attempts = max(self.attempts, attempts)
        if status == Status.SENT:
            self.sent_at = self.sent_at or at
        elif status == Status.DELIVERED:
            self.sent_at = self.sent_at or at
            self.delivered_at = at
            self.error_code, self.error_message = None, ""
        elif status == Status.FAILED:
            self.failed_at = at
        if self.acked_at is None:
            self.acked_at = at  # a status from the node implies it took the job
        if error_code is not None or error_message:
            self.error_code = error_code
            self.error_message = (error_message or "")[:255]
        return True


class ApiKey(models.Model):
    """Client credential sent as `X-API-Key`. Only a SHA-256 hash is stored."""

    PREFIX = "sgk_"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_keys"
    )
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=16, editable=False)
    key_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.prefix}...)"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    @staticmethod
    def hash_key(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    @classmethod
    def create_for(cls, user, name: str) -> tuple["ApiKey", str]:
        """Returns (row, plaintext key). The plaintext is never stored."""
        key = cls.PREFIX + secrets.token_urlsafe(32)
        row = cls.objects.create(
            user=user, name=name, prefix=key[:12], key_hash=cls.hash_key(key)
        )
        return row, key
