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

# Re-exported for callers that historically imported it from here (the classic
# CLI's original home for this rule); the definition itself lives in
# ``config.settings`` so the automation layer can depend on it without an
# inverted import back into ``cli`` (issue #65).
from config.settings import effective_daily_limit  # noqa: F401


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


def map_search_and_connect_result(results: dict) -> dict:
    """Map a ``search_and_connect`` result dict onto a UI status contract.

    Shared by the TUI (``campaign_detail.map_connect_results``) and the
    ``linkedin-run`` entry point (``CampaignRunner._run_campaign_automation``)
    so the two presentations of the same automation call can't drift apart.

    A user-requested stop (``stopped_reason == "cancelled"``) is a normal
    partial completion — checked first so it is never dressed up as a
    protective CAPTCHA/challenge stop; a protective stop is checked before the
    empty-scan mapping so a first-page CAPTCHA doesn't masquerade as
    "no profiles".
    """
    if results.get("stopped_reason") == "cancelled":
        return {**results, "status": "cancelled"}
    if results.get("stopped_reason"):
        return {**results, "status": "safety_stop"}
    if results.get("scanned", 0) == 0:
        return {**results, "status": "no_profiles"}
    return {**results, "status": "success"}


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
    # Campaign.name is unbounded; truncate so the final filename can't
    # exceed common filesystem limits (255 bytes).
    safe = safe[:80]
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


def mask_api_key(key: str | None) -> str:
    """'Set' / 'Not set' for display — an API key has no safe-to-show prefix

    (unlike an email's domain), so this stays binary, matching how
    ``LINKEDIN_PASSWORD`` is already shown in ``SettingsScreen``.
    """
    return "Set" if key else "Not set"
