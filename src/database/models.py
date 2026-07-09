import json
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class ContactStatus(str, Enum):  # noqa: UP042 — str mix-in is the storage contract
    """Canonical set of ``Contact.status`` values.

    A ``str`` mix-in so each member *is* its plain string value
    (``ContactStatus.SENT == "sent"`` and it hashes identically), which keeps
    every existing string-literal consumer — and plain-string storage in
    SQLite — working unchanged while giving the codebase one authoritative
    definition instead of bare literals scattered across modules.

    - ``found``: profile located, no invite action taken (retryable).
    - ``reserved``: durable pre-send skip marker written BEFORE the irreversible
      Send click (#39); no invite is known to be out yet.
    - ``sent``: invitation confirmed sent.
    - ``possibly_sent``: ambiguous send after the irreversible click (#31);
      assumed sent (non-retryable).
    - ``accepted`` / ``declined``: terminal outcomes.
    - ``failed``: a clean, retryable send failure.
    """

    FOUND = "found"
    RESERVED = "reserved"
    SENT = "sent"
    POSSIBLY_SENT = "possibly_sent"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    FAILED = "failed"


# Single source of truth for the status GROUPS used in campaign/dashboard
# statistics (see DatabaseManager.get_dashboard_stats / update_campaign_stats).
# ``possibly_sent`` (an assumed-sent invite that consumed a daily slot, #31)
# counts as both sent and pending just like ``sent``; ``reserved`` (a pre-send
# skip marker only, #39) is deliberately excluded from both. Because the members
# are ``str`` values, these sets look up cleanly against the plain-string status
# keys returned by a GROUP BY.
SENT_STATUSES = frozenset(
    {
        ContactStatus.SENT,
        ContactStatus.POSSIBLY_SENT,
        ContactStatus.ACCEPTED,
        ContactStatus.DECLINED,
    }
)
PENDING_STATUSES = frozenset({ContactStatus.SENT, ContactStatus.POSSIBLY_SENT})
ACCEPTED_STATUSES = frozenset({ContactStatus.ACCEPTED})


class Campaign(SQLModel, table=True):
    """Campaign model for storing LinkedIn networking campaigns"""
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str | None = None

    # Targeting criteria - Core filters
    keywords: str | None = None

    # Location fields (new format)
    geo_urn: str | None = None  # LinkedIn geoUrn code (e.g., "90000084")
    location_display: str | None = None  # Human-readable location name

    # Industry fields (new format)
    industry_ids: str | None = None  # Comma-separated industry IDs (e.g., "4,6,96")
    industry_display: str | None = None  # Human-readable industry names

    # Network filter (connection degree)
    network: str | None = Field(default='["F","S"]')  # Default: 1st + 2nd connections
    network_display: str | None = Field(default="1st + 2nd degree connections")

    # Legacy fields (deprecated, kept for backward compatibility)
    location: str | None = None  # DEPRECATED: Use geo_urn instead
    industry: str | None = None  # DEPRECATED: Use industry_ids instead

    # Other filters (not implemented yet)
    company_size: str | None = None
    experience_level: str | None = None

    # Campaign settings
    daily_limit: int = Field(default=20)
    message_template: str = Field(default="Hi {name}, I'd like to connect with you!")
    active: bool = Field(default=True)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None
    last_run: datetime | None = None

    # Statistics
    total_sent: int = Field(default=0)
    total_accepted: int = Field(default=0)
    total_pending: int = Field(default=0)


class Contact(SQLModel, table=True):
    """Contact model for storing individual LinkedIn connections"""
    # One canonical row per profile within a campaign. The resilient send tail
    # (#39) writes a pre-send marker and later reconciles the SAME row to its
    # final status; this constraint lets that be an atomic INSERT ... ON CONFLICT
    # DO UPDATE (see DatabaseManager.upsert_contact) so two overlapping runs on
    # one profile can never double-insert a duplicate skip marker. Existing DBs
    # may already hold duplicates from the pre-existing non-atomic create_contact;
    # DatabaseManager de-duplicates them before creating the unique index.
    __table_args__ = (UniqueConstraint("campaign_id", "profile_url"),)

    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id")

    # Contact info
    name: str
    profile_url: str = Field(index=True)
    headline: str | None = None
    location: str | None = None
    company: str | None = None

    # Connection status
    # reserved: a durable pre-send skip marker written BEFORE the irreversible
    #   Send click (issue #39). It means "a future run must not re-contact this
    #   profile" but the invite is NOT yet (known) out — so it does NOT count as
    #   a sent invite in stats, is NOT polled for acceptance, and is the only
    #   pre-send status a retryable cleanup may delete/downgrade. It is reconciled
    #   to sent / possibly_sent / found after the click resolves.
    # possibly_sent: a renderer wedge struck AFTER the irreversible Send click,
    #   so we assume sent (non-retryable) rather than re-contact (issue #31).
    #   Unlike reserved, it means the invite may already be out, so it is never
    #   deleted/downgraded by a retryable cleanup.
    # ContactStatus.FOUND is a str-Enum member equal to the plain string
    # "found"; SQLite stores/reads it as that plain string (existing rows and
    # readers are unaffected). See ContactStatus for the full value set.
    status: str = Field(default=ContactStatus.FOUND)
    connection_sent_at: datetime | None = None
    connection_accepted_at: datetime | None = None

    # Per-attempt ownership token for the pre-send ``reserved`` marker (#39
    # concurrency). Two overlapping attempts on one profile share a single
    # canonical row (UniqueConstraint); this token records WHICH attempt's
    # reservation is live, so a retryable cleanup/downgrade in one attempt can
    # never erase or clobber a reservation the OTHER attempt may already have
    # turned into a clicked send. Null on every non-reserved (and legacy) row.
    reservation_token: str | None = Field(default=None)

    # Additional data
    notes: str | None = None
    contact_info: str = Field(default="{}")  # JSON string for email, phone, etc.

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None

    def get_contact_info(self) -> dict[str, Any]:
        """Parse contact info JSON string"""
        try:
            return json.loads(self.contact_info)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_contact_info(self, info: dict[str, Any]) -> None:
        """Set contact info as JSON string"""
        self.contact_info = json.dumps(info)


class Analytics(SQLModel, table=True):
    """Analytics model for tracking campaign performance"""
    id: int | None = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id")

    # Daily metrics
    date: str = Field(index=True)  # YYYY-MM-DD format
    connections_sent: int = Field(default=0)
    connections_accepted: int = Field(default=0)
    connections_declined: int = Field(default=0)

    # Response metrics
    response_rate: float = Field(default=0.0)
    acceptance_rate: float = Field(default=0.0)

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


class Settings(SQLModel, table=True):
    """Settings model for storing app configuration"""
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str
    description: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None


class DailyConnectionCount(SQLModel, table=True):
    """Per-local-day connection counter for restart-safe rate limiting.

    One row per local day (``date`` is a YYYY-MM-DD string derived from
    ``date.today()`` so the bucket follows the user's wall-clock day). The
    cumulative count persists across CLI restarts so the daily connection cap
    cannot be exceeded by quitting and reopening the app; a new local day
    simply starts at a fresh row with count 0.
    """
    id: int | None = Field(default=None, primary_key=True)
    date: str = Field(index=True, unique=True)  # YYYY-MM-DD format (local day)
    count: int = Field(default=0)
    last_action_at: datetime | None = None  # timestamp of the last sent request
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime | None = None