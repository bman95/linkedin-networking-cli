"""Pure helper functions, shared by the TUI and the ``linkedin-run`` entry point.

Originally extracted from the classic InquirerPy ``linkedin_cli.py`` (retired
in the issue #47 cutover). These have no dependency on an interactive
terminal, a database, or a browser, so they are trivially unit-testable and
covered under ``source = ["src"]``.
"""

import csv
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


def campaign_get_field(campaign: Any, attr: str, default: Any = None) -> Any:
    """Read a campaign attribute regardless of the backing type.

    Campaigns flow through the CLI as either SQLModel objects or plain dicts
    (demo/mock mode), so field access has to work for both.
    """
    if isinstance(campaign, dict):
        return campaign.get(attr, default)
    return getattr(campaign, attr, default)


def csv_value(value: Any) -> str:
    """Normalize a value for CSV output.

    ``None`` becomes an empty cell, ``datetime`` is ISO-formatted, and anything
    else is stringified.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def acceptance_rate(sent: int, accepted: int) -> float:
    """Acceptance rate as a percentage; ``0.0`` when nothing was sent."""
    return (accepted / sent * 100) if sent > 0 else 0.0


def effective_daily_limit(campaign_limit: Any, fallback: int) -> int:
    """The daily invitation cap actually enforced for a campaign run.

    The per-campaign ``daily_limit`` — the value shown and edited in the CLI —
    is authoritative. It falls back to the ``DAILY_CONNECTION_LIMIT``
    setting/env default only when the campaign carries no valid positive value
    (so an unset/zeroed campaign still gets a sane cap). Shared by the
    automation enforcement and every display surface so copy can never drift
    from what a run actually enforces (issue #46).
    """
    if (
        isinstance(campaign_limit, int)
        and not isinstance(campaign_limit, bool)
        and campaign_limit > 0
    ):
        return campaign_limit
    return fallback


# Contact CSV export columns, shared by the classic CLI and the TUI so both
# produce the same file shape.
CONTACT_CSV_FIELDS = (
    "name",
    "profile_url",
    "headline",
    "location",
    "company",
    "status",
    "connection_sent_at",
    "connection_accepted_at",
    "notes",
)


def contacts_csv_filename(campaign_name: str) -> str:
    """Default ``<safe-name>_contacts_<timestamp>.csv`` filename for an export."""
    safe = "".join(
        c if c.isalnum() or c in ("-", "_") else "_" for c in campaign_name
    ).strip("_") or "campaign"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe}_contacts_{timestamp}.csv"


def write_contacts_csv(path: str | Path, contacts: Iterable[Any]) -> None:
    """Write contacts to ``path`` as CSV using :data:`CONTACT_CSV_FIELDS`.

    Path policy (where the file goes, prompting, directory creation) is the
    caller's concern; this owns only the writing. Contacts may be SQLModel
    objects or plain dicts (see :func:`campaign_get_field`).
    """
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CONTACT_CSV_FIELDS))
        writer.writeheader()
        for contact in contacts:
            writer.writerow(
                {
                    field: csv_value(campaign_get_field(contact, field))
                    for field in CONTACT_CSV_FIELDS
                }
            )


def mask_email(email: str | None) -> str:
    """Mask an email for display, e.g. ``'joh***@example.com'``."""
    if not email:
        return "Not set"
    if "@" in email:
        local, domain = email.split("@", 1)
        prefix = local[:3] if len(local) >= 3 else local
        return f"{prefix}***@{domain}"
    return f"{email[:3]}***"
