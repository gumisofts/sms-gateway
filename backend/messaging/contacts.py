"""Import contacts from a CSV file, pasted lines or a list of rows.

A CSV may have a header row: the phone column is the one named
phone/msisdn/number/to/mobile (else the first), the name comes from ``name``
(or ``full_name``, or ``first_name`` + ``last_name``). Without a header each
line is a number and a name in either order (``Abebe, 0912345678``); a line
with only a number gets the number as its name.
"""

from dataclasses import dataclass, field

from django.db import transaction

from .bulk import _rows_from_csv, normalize_number
from .models import Contact

MAX_IMPORT_BYTES = 10 * 1024 * 1024
CHUNK = 1000


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0  # renamed, with update_existing
    existing: int = 0  # already there, name left alone
    duplicates: int = 0  # repeated in the import itself
    invalid: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"{self.created} contact(s) added"]
        if self.updated:
            lines.append(f"{self.updated} renamed")
        if self.existing:
            lines.append(f"{self.existing} already in your contacts")
        if self.duplicates:
            lines.append(f"{self.duplicates} duplicate line(s) skipped")
        if self.invalid:
            shown = ", ".join(repr(x) for x in self.invalid[:5])
            more = f" and {len(self.invalid) - 5} more" if len(self.invalid) > 5 else ""
            lines.append(
                f"{len(self.invalid)} invalid number(s) skipped: {shown}{more}"
            )
        return "; ".join(lines) + "."


def _name(values: dict) -> str:
    name = values.get("name") or values.get("full_name")
    if not name:
        name = " ".join(
            v for v in (values.get("first_name"), values.get("last_name")) if v
        )
    return (name or "").strip()[:120]


def rows_from_bytes(data: bytes) -> list[dict]:
    return list(_rows_from_csv(data, positional=True))


def import_contacts(owner, rows, groups=(), update_existing=False) -> ImportResult:
    """Add ``rows`` (dicts with ``phone`` and optionally ``name``) to the owner's
    contacts. A number the owner already has is kept (renamed only with
    ``update_existing``). Every imported contact, new or not, joins ``groups``,
    which the caller has checked belong to the owner."""
    result = ImportResult()
    wanted: dict[str, str] = {}
    for values in rows:
        raw = str(values.get("phone") or "")
        phone = normalize_number(raw)
        if phone is None:
            result.invalid.append(raw)
        elif phone in wanted:
            result.duplicates += 1
        else:
            wanted[phone] = _name(values)

    with transaction.atomic():
        known = {
            c.phone: c
            for c in Contact.objects.filter(owner=owner).only("id", "phone", "name")
        }
        new, renamed, touched = [], [], []
        for phone, name in wanted.items():
            contact = known.get(phone)
            if contact is None:
                new.append(Contact(owner=owner, phone=phone, name=name or phone))
                continue
            touched.append(contact)
            if update_existing and name and name != contact.name:
                contact.name = name
                renamed.append(contact)
            else:
                result.existing += 1
        for start in range(0, len(new), CHUNK):
            Contact.objects.bulk_create(new[start : start + CHUNK])
        Contact.objects.bulk_update(renamed, ["name"], batch_size=CHUNK)
        result.created, result.updated = len(new), len(renamed)

        through = Contact.groups.through
        links = [
            through(contact_id=c.pk, contactgroup_id=g.pk)
            for g in groups
            for c in (*new, *touched)
        ]
        for start in range(0, len(links), CHUNK):
            through.objects.bulk_create(
                links[start : start + CHUNK], ignore_conflicts=True
            )
    return result
