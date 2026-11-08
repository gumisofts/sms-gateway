import hashlib
import secrets
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def generate_node_id() -> str:
    return f"node-{uuid.uuid4().hex[:12]}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Node(models.Model):
    """One Android phone running the gateway app."""

    node_id = models.CharField(
        max_length=64, unique=True, default=generate_node_id, editable=False
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="nodes",
        help_text="If set, only this user's messages are routed to the node.",
    )

    device_name = models.CharField(max_length=255, blank=True)
    manufacturer = models.CharField(max_length=255, blank=True)
    model = models.CharField(max_length=255, blank=True)
    android_version = models.CharField(max_length=32, blank=True)
    sdk_int = models.PositiveIntegerField(null=True, blank=True)
    app_version = models.CharField(max_length=32, blank=True)

    is_active = models.BooleanField(
        default=True, help_text="Disabled nodes cannot connect or receive jobs."
    )

    # Connection state. The owning connection is identified by its channel
    # name, so "newest wins" works across backend processes.
    connected = models.BooleanField(default=False)
    connection_id = models.CharField(max_length=255, blank=True)
    connected_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # Last heartbeat.
    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    charging = models.BooleanField(null=True, blank=True)
    signal_level = models.PositiveSmallIntegerField(null=True, blank=True)
    queue_depth = models.PositiveIntegerField(default=0)
    rate_per_minute = models.PositiveIntegerField(default=0, help_text="0 = unlimited")
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    # Android's own per-app SMS limit (SmsUsageMonitor, ~30 per 30 min by
    # default). None: the phone could not tell (older app or unreadable).
    sms_limit_raised = models.BooleanField(null=True, blank=True)
    sms_limit_max_count = models.PositiveIntegerField(null=True, blank=True)

    # Config pushed by an admin (sent on every connect and when changed).
    desired_rate_per_minute = models.PositiveIntegerField(null=True, blank=True)
    desired_heartbeat_interval_sec = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["is_active", "connected"])]

    def __str__(self):
        return f"{self.node_id} ({self.device_name or 'unknown'})"

    @property
    def group_name(self) -> str:
        return f"node_{self.node_id}"


class NodeSim(models.Model):
    """One SIM of a node: a sending lane with its own pace, daily cap and health.

    Rows are kept when a SIM disappears from a heartbeat (``is_present=False``)
    so admin settings and pause state survive a temporary SIM/radio glitch.
    Usage (parts per minute / per day) is counted from ``SmsMessage`` rows.
    """

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="sims")
    slot = models.IntegerField()
    subscription_id = models.IntegerField(null=True, blank=True)
    carrier = models.CharField(max_length=255, blank=True)
    label = models.CharField(
        max_length=64, blank=True, help_text="Optional name, e.g. the SIM's number."
    )

    is_present = models.BooleanField(
        default=True, help_text="Reported by the phone in its last update."
    )
    is_enabled = models.BooleanField(
        default=True, help_text="Disabled SIMs never get messages."
    )
    rate_per_minute = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="SMS parts per minute. Empty: server default. 0: unlimited.",
    )
    daily_limit = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="SMS parts per day. Empty: server default. 0: unlimited.",
    )

    # Health: consecutive failures auto-pause the SIM (likely carrier block).
    consecutive_failures = models.PositiveIntegerField(default=0)
    paused_until = models.DateTimeField(null=True, blank=True)
    pause_reason = models.CharField(max_length=255, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_failure_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["node_id", "slot"]
        verbose_name = "SIM"
        verbose_name_plural = "SIMs"
        constraints = [
            models.UniqueConstraint(fields=["node", "slot"], name="uniq_node_sim_slot")
        ]

    def __str__(self):
        name = self.label or self.carrier or "SIM"
        return (
            f"{self.node.device_name or self.node.node_id} / {name} (slot {self.slot})"
        )

    @property
    def effective_rate_per_minute(self) -> int:
        if self.rate_per_minute is not None:
            return self.rate_per_minute
        return settings.SIM_DEFAULT_RATE_PER_MINUTE

    @property
    def effective_daily_limit(self) -> int:
        if self.daily_limit is not None:
            return self.daily_limit
        return settings.SIM_DEFAULT_DAILY_LIMIT

    def is_paused(self, now=None) -> bool:
        return self.paused_until is not None and self.paused_until > (
            now or timezone.now()
        )


class NodeToken(models.Model):
    """Opaque bearer token of a node. Only a SHA-256 hash is stored."""

    node = models.ForeignKey(Node, on_delete=models.CASCADE, related_name="tokens")
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"token for {self.node.node_id}"

    @classmethod
    def issue(cls, node: "Node") -> str:
        """Replace all of the node's tokens with a fresh one; returns the plaintext."""
        token = secrets.token_urlsafe(32)
        cls.objects.filter(node=node).delete()
        cls.objects.create(node=node, token_hash=hash_token(token))
        return token

    @classmethod
    def authenticate(cls, token: str) -> "Node | None":
        if not token:
            return None
        row = (
            cls.objects.select_related("node")
            .filter(token_hash=hash_token(token), node__is_active=True)
            .first()
        )
        if row is None:
            return None
        cls.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
        return row.node
