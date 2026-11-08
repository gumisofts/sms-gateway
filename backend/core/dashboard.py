"""Admin dashboard (django-unfold DASHBOARD_CALLBACK) and environment badge."""

import json
from datetime import timedelta

from django.conf import settings
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncHour
from django.urls import reverse
from django.utils import timezone

from auth.roles import can_view_all
from messaging.dispatch import day_start
from messaging.models import IN_FLIGHT, SmsMessage, Status
from nodes.admin import sim_state
from nodes.models import Node, NodeSim


def environment_callback(request):
    if settings.DEBUG:
        return ["Development", "warning"]
    return ["Production", "danger"]


def _pct(part, whole) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "–"


def _today_stats(base, today):
    agg = base.filter(created_at__gte=today).aggregate(
        total=Count("id"),
        parts=Sum("parts"),
        delivered=Count("id", filter=Q(status=Status.DELIVERED)),
        sent=Count("id", filter=Q(status__in=[Status.SENT, Status.DELIVERED])),
        failed=Count("id", filter=Q(status__in=[Status.FAILED, Status.EXPIRED])),
    )
    agg["parts"] = agg["parts"] or 0
    # Waiting / in flight right now, whenever they were created.
    agg["pending"] = base.filter(status=Status.PENDING).count()
    agg["in_flight"] = base.filter(status__in=IN_FLIGHT).count()
    finished = agg["sent"] + agg["failed"]
    agg["success_rate"] = _pct(agg["sent"], finished)
    agg["delivery_rate"] = _pct(agg["delivered"], agg["sent"])
    return agg


def _hourly_chart(base, now):
    start = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)
    rows = (
        base.filter(created_at__gte=start)
        .annotate(hour=TruncHour("created_at"))
        .values("hour")
        .annotate(
            ok=Count("id", filter=Q(status__in=[Status.SENT, Status.DELIVERED])),
            failed=Count("id", filter=Q(status__in=[Status.FAILED, Status.EXPIRED])),
            other=Count(
                "id",
                filter=~Q(
                    status__in=[
                        Status.SENT,
                        Status.DELIVERED,
                        Status.FAILED,
                        Status.EXPIRED,
                    ]
                ),
            ),
        )
    )
    by_hour = {timezone.localtime(r["hour"]).strftime("%d %H"): r for r in rows}
    labels, ok, failed, other = [], [], [], []
    for i in range(24):
        hour = timezone.localtime(start + timedelta(hours=i))
        row = by_hour.get(hour.strftime("%d %H"), {})
        labels.append(hour.strftime("%H:00"))
        ok.append(row.get("ok", 0))
        failed.append(row.get("failed", 0))
        other.append(row.get("other", 0))
    return json.dumps(
        {
            "labels": labels,
            "datasets": [
                {
                    "label": "Sent / delivered",
                    "data": ok,
                    "backgroundColor": "#0284c7",
                },
                {
                    "label": "Failed / expired",
                    "data": failed,
                    "backgroundColor": "#ef4444",
                },
                {
                    "label": "Pending / in flight",
                    "data": other,
                    "backgroundColor": "#94a3b8",
                },
            ],
        }
    )


def _sims(now, today):
    sims = list(
        NodeSim.objects.filter(is_present=True, node__is_active=True)
        .select_related("node")
        .annotate(
            today=Sum("messages__parts", filter=Q(messages__dispatched_at__gte=today)),
            delivered=Count(
                "messages",
                filter=Q(
                    messages__dispatched_at__gte=today,
                    messages__status=Status.DELIVERED,
                ),
            ),
            failed=Count(
                "messages",
                filter=Q(
                    messages__dispatched_at__gte=today,
                    messages__status=Status.FAILED,
                ),
            ),
        )
    )
    capacity_left = 0
    unlimited = False
    rows = []
    for sim in sims:
        used = sim.today or 0
        cap = sim.effective_daily_limit
        state, variant = sim_state(sim, now)
        usable = state == "Active" and sim.node.connected
        if usable:
            if cap:
                capacity_left += max(cap - used, 0)
            else:
                unlimited = True
        rows.append(
            {
                "sim": sim,
                "state": state,
                "variant": variant,
                "online": sim.node.connected,
                "used": used,
                "cap": cap,
                "pct": min(round(100 * used / cap), 100) if cap else 0,
                "usage_text": f"{used} / {cap}" if cap else f"{used} / ∞",
                "delivered": sim.delivered,
                "failed": sim.failed,
                "url": reverse("admin:nodes_nodesim_change", args=[sim.pk]),
            }
        )
    rows.sort(key=lambda r: (-r["used"], r["sim"].node_id, r["sim"].slot))
    return rows, capacity_left, unlimited


def dashboard_callback(request, context):
    now = timezone.now()
    today = day_start(now)
    base = SmsMessage.objects.all()
    scoped = not can_view_all(request.user)
    if scoped:
        base = base.filter(owner=request.user)
    stats = _today_stats(base, today)
    sim_rows, capacity_left, unlimited = _sims(now, today)

    nodes = Node.objects.filter(is_active=True)
    online = nodes.filter(connected=True)
    offline = list(nodes.filter(connected=False).order_by("-last_seen_at")[:10])
    limit_not_raised = list(online.exclude(sms_limit_raised=True))
    paused = [r for r in sim_rows if r["state"] == "Paused"]

    context.update(
        {
            "today_label": timezone.localtime(now).strftime("%A %d %B"),
            "tz_name": settings.TIME_ZONE,
            "stats": stats,
            "kpis": [
                {
                    "title": "Sent today",
                    "value": stats["sent"],
                    "footer": f"{stats['parts']} parts · {stats['total']} created",
                },
                {
                    "title": "Delivered",
                    "value": stats["delivered"],
                    "footer": f"Delivery rate {stats['delivery_rate']} of sent",
                },
                {
                    "title": "Failed / expired",
                    "value": stats["failed"],
                    "footer": f"Success rate {stats['success_rate']}",
                },
                {
                    "title": "Waiting now",
                    "value": stats["pending"],
                    "footer": f"{stats['in_flight']} on phones",
                },
            ],
            "fleet": [
                {
                    "title": "Phones online",
                    "value": f"{online.count()} / {nodes.count()}",
                    "url": reverse("admin:nodes_node_changelist")
                    + "?connected__exact=1",
                },
                {
                    "title": "Active SIMs",
                    "value": sum(
                        1 for r in sim_rows if r["state"] == "Active" and r["online"]
                    ),
                    "url": reverse("admin:nodes_nodesim_changelist"),
                },
                {
                    "title": "Capacity left today",
                    "value": "Unlimited" if unlimited else f"{capacity_left} parts",
                    "url": reverse("admin:nodes_nodesim_changelist"),
                },
            ],
            "chart": _hourly_chart(base, now),
            "scoped": scoped,
            "can_bulk_send": request.user.has_perm("messaging.add_smsbatch"),
            "bulk_url": reverse("admin:messaging_smsbatch_add"),
            "sim_rows": sim_rows[:50],
            "paused": paused,
            "offline": offline,
            "limit_not_raised": limit_not_raised,
            "messages_url": reverse("admin:messaging_smsmessage_changelist"),
        }
    )
    return context
