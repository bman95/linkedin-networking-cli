"""Pure helper functions extracted from ``linkedin_cli.py``.

These have no dependency on an interactive terminal, a database, or a browser,
so they are trivially unit-testable and covered under ``source = ["src"]``.
``linkedin_cli.LinkedInCLI`` keeps thin static-method delegators to these so
its existing call sites and public surface are unchanged.
"""

from datetime import datetime
from typing import Any, Optional


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


def mask_email(email: Optional[str]) -> str:
    """Mask an email for display, e.g. ``'joh***@example.com'``."""
    if not email:
        return "Not set"
    if "@" in email:
        local, domain = email.split("@", 1)
        prefix = local[:3] if len(local) >= 3 else local
        return f"{prefix}***@{domain}"
    return f"{email[:3]}***"
