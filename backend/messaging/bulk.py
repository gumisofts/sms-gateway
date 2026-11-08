"""Bulk send from the admin: parse recipients, render the text, create a batch.

Recipients come from a pasted list (one number per line, or separated by
commas/semicolons) and/or a CSV file. A CSV may have a header row; its first
column named phone/msisdn/number/to/mobile (else the first column) is the
number, a ``message`` column overrides the text for that row, and every other
column can be used as a ``{column}`` placeholder in the text.
"""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from .models import SmsBatch, SmsMessage
from .segments import count_parts

E164 = re.compile(r"^\+[1-9]\d{6,14}$")
PHONE_COLUMNS = ("phone", "msisdn", "number", "phone_number", "to", "mobile")
PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
CHUNK = 1000


def normalize_number(raw: str, country_code: str | None = None) -> str | None:
    """E.164 or None. Local numbers get the configured country code:
    ``0912345678`` / ``912345678`` / ``251912345678`` -> ``+251912345678``."""
    cc = country_code or settings.BULK_SEND_COUNTRY_CODE
    value = re.sub(r"[\s\-().]", "", raw or "")
    if not value:
        return None
    if value.startswith("00"):
        value = "+" + value[2:]
    if not value.startswith("+"):
        if not value.isdigit():
            return None
        if value.startswith(cc) and len(value) > len(cc) + 6:
            value = "+" + value
        elif value.startswith("0"):
            value = f"+{cc}{value[1:]}"
        else:
            value = f"+{cc}{value}"
    return value if E164.match(value) else None


@dataclass
class Recipient:
    to: str
    body: str
    parts: int


@dataclass
class Plan:
    recipients: list[Recipient] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    duplicates: int = 0
    missing_placeholders: set[str] = field(default_factory=set)

    @property
    def total_parts(self) -> int:
        return sum(r.parts for r in self.recipients)

    @property
    def skipped(self) -> int:
        return len(self.invalid) + self.duplicates


def _rows_from_text(text: str):
    for token in re.split(r"[\n,;]+", text or ""):
        token = token.strip()
        if token:
            yield {"phone": token}


def _rows_from_csv(data: bytes):
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if not rows:
        return
    header = [h.strip().lower() for h in rows[0]]
    has_header = not any(normalize_number(cell) for cell in rows[0]) and any(header)
    if not has_header:
        for row in rows:
            if row and row[0].strip():
                yield {"phone": row[0].strip()}
        return
    phone_col = next((i for i, h in enumerate(header) if h in PHONE_COLUMNS), 0)
    for row in rows[1:]:
        if not row or not any(cell.strip() for cell in row):
            continue
        values = {
            header[i]: cell.strip() for i, cell in enumerate(row) if i < len(header)
        }
        values["phone"] = row[phone_col].strip() if phone_col < len(row) else ""
        yield values


def render(template: str, values: dict, missing: set) -> str:
    def repl(match):
        key = match.group(1).lower()
        if key in values:
            return values[key]
        missing.add(match.group(1))
        return match.group(0)

    return PLACEHOLDER.sub(repl, template)


def build_plan(body: str, pasted: str = "", csv_data: bytes | None = None) -> Plan:
    plan = Plan()
    seen = set()
    rows = list(_rows_from_text(pasted))
    if csv_data:
        rows += list(_rows_from_csv(csv_data))
    for values in rows:
        to = normalize_number(values.get("phone", ""))
        if to is None:
            plan.invalid.append(values.get("phone", ""))
            continue
        if to in seen:
            plan.duplicates += 1
            continue
        seen.add(to)
        text = values.get("message") or render(body, values, plan.missing_placeholders)
        plan.recipients.append(Recipient(to, text, count_parts(text)))
    return plan


def capacity_left_today() -> int | None:
    """Parts the online, active SIMs can still send today (None = unlimited)."""
    from nodes.models import NodeSim

    from .dispatch import day_start

    total = 0
    sims = NodeSim.objects.filter(
        is_present=True, is_enabled=True, node__is_active=True, node__connected=True
    )
    for sim in sims:
        if sim.is_paused():
            continue
        cap = sim.effective_daily_limit
        if not cap:
            return None
        used = (
            SmsMessage.objects.filter(
                sim=sim, dispatched_at__gte=day_start()
            ).aggregate(t=Sum("parts"))["t"]
            or 0
        )
        total += max(cap - used, 0)
    return total


def summary(plan: Plan) -> str:
    parts = plan.total_parts
    lines = [
        f"{len(plan.recipients)} recipients, {parts} SMS parts in total.",
    ]
    if plan.recipients:
        sizes = sorted({r.parts for r in plan.recipients})
        each = (
            f"{sizes[0]} part(s) each"
            if len(sizes) == 1
            else f"{sizes[0]}-{sizes[-1]} parts per message"
        )
        lines.append(
            f"{each}; first: {plan.recipients[0].to}: “{plan.recipients[0].body[:80]}”"
        )
    if plan.invalid:
        shown = ", ".join(repr(x) for x in plan.invalid[:5])
        more = f" and {len(plan.invalid) - 5} more" if len(plan.invalid) > 5 else ""
        lines.append(f"{len(plan.invalid)} invalid number(s) skipped: {shown}{more}.")
    if plan.duplicates:
        lines.append(f"{plan.duplicates} duplicate(s) removed.")
    if plan.missing_placeholders:
        names = ", ".join("{%s}" % n for n in sorted(plan.missing_placeholders))
        lines.append(f"Warning: no CSV column for {names}; left as-is.")
    cap = capacity_left_today()
    if cap is not None:
        lines.append(f"Capacity left today on online SIMs: {cap} parts.")
        if parts > cap:
            lines.append(
                "More than today's capacity: the rest waits for free capacity "
                "(next minutes / tomorrow's caps) until the batch's validity ends."
            )
    return " ".join(lines)


def create_batch(batch: SmsBatch, plan: Plan) -> SmsBatch:
    """Save the batch and its messages (pending), then kick the dispatcher."""
    from . import services

    with transaction.atomic():
        batch.total = len(plan.recipients)
        batch.skipped = plan.skipped
        batch.save()
        expires_at = batch.created_at + timedelta(hours=batch.valid_hours)
        ids = []
        for start in range(0, len(plan.recipients), CHUNK):
            chunk = [
                SmsMessage(
                    owner=batch.created_by,
                    batch=batch,
                    to=r.to,
                    body=r.body,
                    parts=r.parts,
                    requested_node=batch.requested_node,
                    sim_slot=batch.sim_slot,
                    callback_url=batch.callback_url,
                    expires_at=expires_at,
                )
                for r in plan.recipients[start : start + CHUNK]
            ]
            SmsMessage.objects.bulk_create(chunk)
            ids.extend(m.id for m in chunk)
        # Dispatch the first ones right away; the beat task does the rest.
        services.schedule_dispatch(*ids[:200])
    return batch
