from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any
from sqlmodel import SQLModel, create_engine, Session, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from pathlib import Path
import json
import sys

sys.path.append(str(Path(__file__).parent.parent))

from utils.logging import get_logger
from .models import Campaign, Contact, Analytics, Settings, DailyConnectionCount

logger = get_logger(__name__)


class DatabaseManager:
    """Database operations manager for LinkedIn networking CLI"""

    def __init__(self, db_path: str = "linkedin_networking.db"):
        self.db_path = Path(db_path)
        logger.info(f"Initializing database at: {self.db_path}")
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.create_tables()

    def create_tables(self):
        """Create all database tables, migrating an existing DB if needed."""
        try:
            # De-duplicate any existing (campaign_id, profile_url) rows BEFORE
            # the unique index is enforced. A new DB has no contact table yet, so
            # this is a no-op there; an existing DB may carry duplicates from the
            # pre-existing non-atomic create_contact (#39 retry).
            self._dedupe_contacts_before_unique_index()
            SQLModel.metadata.create_all(self.engine)
            # create_all only stamps the UniqueConstraint onto a freshly created
            # table; for a pre-existing contact table it is a no-op, so add the
            # equivalent unique index explicitly. Both are idempotent.
            self._ensure_contact_unique_index()
            logger.info("Database tables created/verified successfully")
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}")
            raise

    def _dedupe_contacts_before_unique_index(self) -> int:
        """Collapse duplicate (campaign_id, profile_url) contact rows.

        Idempotent startup migration (#39 retry): existing user DBs may already
        hold duplicate contact rows for one profile (the pre-existing
        ``create_contact`` was a non-atomic read-then-write with no uniqueness),
        which would block the new ``UniqueConstraint(campaign_id, profile_url)``.
        For each duplicate group keep the single best row by status precedence
        (``_STATUS_KEEPER_RANK``): a terminal ``accepted``/``declined`` wins over
        a later but weaker ``sent``/``pending``, which win over a pre-send
        ``reserved``/plain marker; ties are broken by the largest id (the most
        recent write). The rest are deleted, so the surviving row is always the
        strongest recorded outcome and a safe skip-key. A no-op when the contact
        table is absent (fresh DB) or already free of duplicates. Returns the
        number of rows deleted.
        """
        from sqlalchemy import inspect as sa_inspect, text

        inspector = sa_inspect(self.engine)
        if not inspector.has_table("contact"):
            return 0

        rank = self._STATUS_KEEPER_RANK
        deleted = 0
        with self.get_session() as session:
            groups = session.exec(
                select(Contact.campaign_id, Contact.profile_url)
                .group_by(Contact.campaign_id, Contact.profile_url)
                .having(text("COUNT(*) > 1"))
            ).all()
            for campaign_id, profile_url in groups:
                rows = session.exec(
                    select(Contact).where(
                        Contact.campaign_id == campaign_id,
                        Contact.profile_url == profile_url,
                    )
                ).all()
                # Keep the strongest outcome by status precedence, then the most
                # recent (highest id) among equal-precedence rows.
                keeper = max(
                    rows,
                    key=lambda r: (rank.get(r.status, -1), r.id or 0),
                )
                for row in rows:
                    if row.id != keeper.id:
                        session.delete(row)
                        deleted += 1
            session.commit()
        if deleted:
            logger.info(
                f"De-duplicated {deleted} duplicate contact row(s) before "
                f"applying the unique index"
            )
        return deleted

    def _ensure_contact_unique_index(self) -> None:
        """Create the (campaign_id, profile_url) unique index if it is missing.

        Idempotent: ``CREATE UNIQUE INDEX IF NOT EXISTS``. Needed for existing
        DBs whose contact table predates the model's ``UniqueConstraint`` —
        ``create_all`` will not retrofit a constraint onto an existing table, so
        the index is what actually enforces uniqueness there.
        """
        from sqlalchemy import text

        with self.engine.begin() as conn:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "ix_contact_campaign_profile "
                    "ON contact (campaign_id, profile_url)"
                )
            )

    def get_session(self) -> Session:
        """Get database session"""
        return Session(self.engine)

    # Campaign operations
    def create_campaign(self, campaign_data: Dict[str, Any]) -> Campaign:
        """Create a new campaign"""
        try:
            campaign = Campaign(**campaign_data)
            with self.get_session() as session:
                session.add(campaign)
                session.commit()
                session.refresh(campaign)
                logger.info(f"Created campaign: {campaign.name} (ID: {campaign.id})")
                return campaign
        except Exception as e:
            logger.error(f"Failed to create campaign: {e}")
            raise

    def get_campaigns(self, active_only: bool = True) -> List[Campaign]:
        """Get all campaigns"""
        try:
            with self.get_session() as session:
                statement = select(Campaign)
                if active_only:
                    statement = statement.where(Campaign.active == True)
                campaigns = session.exec(statement).all()
                logger.debug(f"Retrieved {len(campaigns)} campaigns (active_only={active_only})")
                return list(campaigns)
        except Exception as e:
            logger.error(f"Failed to get campaigns: {e}")
            raise

    def get_campaign(self, campaign_id: int) -> Optional[Campaign]:
        """Get campaign by ID"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            return campaign

    def update_campaign(self, campaign_id: int, updates: Dict[str, Any]) -> Optional[Campaign]:
        """Update campaign"""
        try:
            with self.get_session() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign:
                    for key, value in updates.items():
                        setattr(campaign, key, value)
                    campaign.updated_at = datetime.now()
                    session.commit()
                    session.refresh(campaign)
                    logger.info(f"Updated campaign {campaign_id}: {list(updates.keys())}")
                    return campaign
                logger.warning(f"Campaign {campaign_id} not found for update")
                return None
        except Exception as e:
            logger.error(f"Failed to update campaign {campaign_id}: {e}")
            raise

    def delete_campaign(self, campaign_id: int) -> bool:
        """Delete campaign and its contacts"""
        try:
            with self.get_session() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign:
                    # Delete associated contacts first
                    contacts = session.exec(
                        select(Contact).where(Contact.campaign_id == campaign_id)
                    ).all()
                    contact_count = len(list(contacts))
                    for contact in contacts:
                        session.delete(contact)

                    # Delete campaign
                    session.delete(campaign)
                    session.commit()
                    logger.info(f"Deleted campaign {campaign_id} and {contact_count} associated contacts")
                    return True
                logger.warning(f"Campaign {campaign_id} not found for deletion")
                return False
        except Exception as e:
            logger.error(f"Failed to delete campaign {campaign_id}: {e}")
            raise

    # Contact operations
    def create_contact(self, contact_data: Dict[str, Any]) -> Contact:
        """Create a new contact"""
        try:
            contact = Contact(**contact_data)
            with self.get_session() as session:
                session.add(contact)
                session.commit()
                session.refresh(contact)
                logger.debug(f"Created contact: {contact.name} (ID: {contact.id}, Campaign: {contact.campaign_id})")
                return contact
        except Exception as e:
            logger.error(f"Failed to create contact: {e}")
            raise

    # A contact row in one of these statuses represents a real, recorded
    # outcome (an invite is out or may be out, or a terminal
    # acceptance/decline/pending). The send tail (#39) must never delete or
    # downgrade such a row from a retryable cleanup path — under concurrent runs
    # on the same profile that would erase another run's confirmed send and
    # re-open re-contact.
    #
    # ``possibly_sent`` is finalized UNCONDITIONALLY: with the dedicated
    # ``reserved`` pre-send status (#39 retry) it can only be the post-click
    # ambiguous-send outcome (the invite may be out), never a pre-send
    # reservation. The only clobberable pre-send marker is ``reserved`` (and the
    # plain retryable ``found``/``failed``), which is NOT in this set.
    _FINALIZED_CONTACT_STATUSES = frozenset(
        {"sent", "possibly_sent", "accepted", "declined", "pending"}
    )

    # Status precedence for the de-dup migration's keeper selection (higher wins).
    # A terminal acceptance/decline must never be discarded for a later but
    # weaker send/pending row, so the order is: terminal outcomes > pending >
    # confirmed send > ambiguous send > pre-send reservation > plain markers.
    # An unknown status sorts at the bottom (treated as a plain marker).
    _STATUS_KEEPER_RANK = {
        "accepted": 6,
        "declined": 6,
        "pending": 5,
        "sent": 4,
        "possibly_sent": 3,
        "reserved": 2,
        "found": 1,
        "failed": 0,
    }

    def _is_finalized_contact(self, contact: Contact) -> bool:
        """True if the row is a recorded outcome that must not be clobbered.

        ``reserved`` (the durable pre-send skip marker) and the plain retryable
        statuses are clobberable; everything in
        ``_FINALIZED_CONTACT_STATUSES`` — including any ``possibly_sent``, which
        now unambiguously means the invite may be out — is preserved.
        """
        return contact.status in self._FINALIZED_CONTACT_STATUSES

    # Columns the upsert conflict-update must never overwrite: the conflict keys
    # themselves and the immutable creation timestamp.
    _UPSERT_PRESERVE_COLUMNS = frozenset(
        {"id", "campaign_id", "profile_url", "created_at"}
    )

    def upsert_contact(
        self, contact_data: Dict[str, Any], protect_finalized: bool = False
    ) -> Contact:
        """Create a contact, or update the existing one for this profile.

        Keyed on ``(campaign_id, profile_url)`` (a ``UniqueConstraint`` on the
        ``Contact`` model). Lets the resilient send tail (#39) write a durable
        per-profile skip marker BEFORE the irreversible Send click and then
        reconcile that same row to its final status afterward, without ever
        creating a duplicate row for one profile. On a fresh profile it behaves
        exactly like ``create_contact``.

        Atomic: a single SQLite ``INSERT ... ON CONFLICT DO UPDATE`` (mirroring
        :meth:`reserve_daily_slot`) so two overlapping runs on the same profile
        cannot both pass a SELECT and double-insert — one inserts, the other
        takes the conflict-update path against the single canonical row.

        With ``protect_finalized=True`` the conflict update is guarded so a row
        that already records a real outcome (see ``_is_finalized_contact``) is
        left unchanged — a retryable downgrade (e.g. a clean send-failure writing
        ``found``) must not overwrite a confirmed/ambiguous send a concurrent run
        may have just recorded.

        Note: the connect flow's dedup that decides whether to skip a profile
        looks up by ``profile_url`` ALONE (not scoped by campaign), so it always
        finds this marker regardless of the campaign scoping here. The connect
        path only reaches this write when no row exists for that ``profile_url``
        in any campaign (the dedup skipped otherwise), so the scoped lookup
        normally creates fresh and never collides cross-campaign.
        """
        try:
            campaign_id = contact_data.get("campaign_id")
            profile_url = contact_data.get("profile_url")
            now_naive = datetime.now()
            # The conflict update sets every provided mutable column plus
            # updated_at; never the conflict keys or created_at.
            update_set = {
                key: value
                for key, value in contact_data.items()
                if key not in self._UPSERT_PRESERVE_COLUMNS
            }
            update_set["updated_at"] = now_naive
            with self.get_session() as session:
                stmt = sqlite_insert(Contact).values(**contact_data)
                on_conflict_kwargs = {
                    "index_elements": ["campaign_id", "profile_url"],
                    "set_": update_set,
                }
                if protect_finalized:
                    # Make the update a no-op when the existing row is finalized,
                    # so a concurrent run's confirmed/ambiguous send is never
                    # downgraded to a reservation/retryable marker.
                    on_conflict_kwargs["where"] = Contact.status.notin_(
                        self._FINALIZED_CONTACT_STATUSES
                    )
                stmt = stmt.on_conflict_do_update(**on_conflict_kwargs)
                session.exec(stmt)
                session.commit()
                contact = session.exec(
                    select(Contact).where(
                        Contact.campaign_id == campaign_id,
                        Contact.profile_url == profile_url,
                    )
                ).first()
                logger.debug(
                    f"Upserted contact for {profile_url}: "
                    f"status={contact.status if contact else None}"
                )
                return contact
        except Exception as e:
            logger.error(f"Failed to upsert contact: {e}")
            raise

    def delete_contacts_by_profile(
        self,
        campaign_id: int,
        profile_url: str,
        only_unfinalized: bool = False,
        reserved_only: bool = False,
    ) -> int:
        """Delete contact rows for a profile in a campaign; return the count.

        Used by the send tail (#39) to clear a durable pre-send marker on a
        genuinely-not-sent, retryable outcome (e.g. the LinkedIn weekly limit
        modal), so a future run can legitimately re-contact that profile.

        ``reserved_only=True`` deletes ONLY the temporary ``reserved`` pre-send
        marker — the send-tail cleanup paths must not erase any other status. In
        particular a concurrent run on the same profile can legitimately reconcile
        the shared row to a durable ``found``/``failed`` skip record (e.g. email
        required, no Connect button); deleting that would make the next run retry
        an already-classified non-contactable profile. This is the mode the send
        tail uses.

        ``only_unfinalized=True`` (kept for the general cleanup case) preserves
        rows that record a real outcome (see ``_is_finalized_contact``) but still
        deletes other unfinalized statuses. With no flags, all rows are deleted.
        """
        try:
            with self.get_session() as session:
                rows = session.exec(
                    select(Contact).where(
                        Contact.campaign_id == campaign_id,
                        Contact.profile_url == profile_url,
                    )
                ).all()
                deleted = 0
                for row in rows:
                    if reserved_only and row.status != "reserved":
                        continue
                    if only_unfinalized and self._is_finalized_contact(row):
                        continue
                    session.delete(row)
                    deleted += 1
                session.commit()
                if deleted:
                    logger.debug(
                        f"Deleted {deleted} contact row(s) for {profile_url}"
                    )
                return deleted
        except Exception as e:
            logger.error(f"Failed to delete contacts for {profile_url}: {e}")
            raise

    def promote_reserved_to_possibly_sent(
        self, campaign_id: int, profile_url: str
    ) -> int:
        """Flip a lingering ``reserved`` marker to ``possibly_sent``; return count.

        Last-ditch reconcile for the send tail (#39): the post-click outcome is
        an (assumed) send, so the durable marker MUST become non-deletable. When
        the full reconcile upsert fails (DB locked / disk full) the row is left
        ``reserved`` — which a concurrent ``only_unfinalized`` cleanup on the same
        profile could still delete, re-opening re-contact of an invite that may
        already be out. This minimal single-row UPDATE (much likelier to succeed
        than the full upsert if the lock was transient) promotes only a
        ``reserved`` row to a finalized ``possibly_sent`` with a stamped
        ``connection_sent_at`` so the cleanup's ``_is_finalized_contact`` guard
        protects it. A no-op when no ``reserved`` row remains (already reconciled
        by a concurrent run). Best-effort: logged, not raised.
        """
        try:
            with self.get_session() as session:
                stmt = (
                    update(Contact)
                    .where(
                        Contact.campaign_id == campaign_id,
                        Contact.profile_url == profile_url,
                        Contact.status == "reserved",
                    )
                    .values(
                        status="possibly_sent",
                        connection_sent_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(),
                    )
                )
                result = session.exec(stmt)
                session.commit()
                promoted = result.rowcount or 0
                if promoted:
                    logger.debug(
                        f"Promoted {promoted} reserved marker(s) to "
                        f"possibly_sent for {profile_url}"
                    )
                return promoted
        except Exception as e:
            logger.error(
                f"Failed to promote reserved marker for {profile_url}: {e}"
            )
            return 0

    def get_contacts(self, campaign_id: Optional[int] = None) -> List[Contact]:
        """Get contacts, optionally filtered by campaign"""
        with self.get_session() as session:
            statement = select(Contact)
            if campaign_id:
                statement = statement.where(Contact.campaign_id == campaign_id)
            contacts = session.exec(statement).all()
            return list(contacts)

    def get_contact(self, contact_id: int) -> Optional[Contact]:
        """Get contact by ID"""
        with self.get_session() as session:
            contact = session.get(Contact, contact_id)
            return contact

    def update_contact(self, contact_id: int, updates: Dict[str, Any]) -> Optional[Contact]:
        """Update contact"""
        try:
            with self.get_session() as session:
                contact = session.get(Contact, contact_id)
                if contact:
                    for key, value in updates.items():
                        setattr(contact, key, value)
                    contact.updated_at = datetime.now()
                    session.commit()
                    session.refresh(contact)
                    logger.debug(f"Updated contact {contact_id}: {list(updates.keys())}")
                    return contact
                logger.warning(f"Contact {contact_id} not found for update")
                return None
        except Exception as e:
            logger.error(f"Failed to update contact {contact_id}: {e}")
            raise

    def get_contacts_by_status(self, campaign_id: int, status: str) -> List[Contact]:
        """Get contacts by status for a campaign"""
        with self.get_session() as session:
            contacts = session.exec(
                select(Contact).where(
                    Contact.campaign_id == campaign_id,
                    Contact.status == status
                )
            ).all()
            return list(contacts)

    # Analytics operations
    def record_daily_analytics(self, campaign_id: int, date_str: str, metrics: Dict[str, Any]):
        """Record or update daily analytics"""
        try:
            with self.get_session() as session:
                # Check if analytics record exists for this date
                existing = session.exec(
                    select(Analytics).where(
                        Analytics.campaign_id == campaign_id,
                        Analytics.date == date_str
                    )
                ).first()

                if existing:
                    # Update existing record
                    for key, value in metrics.items():
                        setattr(existing, key, value)
                    existing.updated_at = datetime.now()
                    session.commit()
                    session.refresh(existing)
                    logger.debug(f"Updated analytics for campaign {campaign_id} on {date_str}")
                    return existing
                else:
                    # Create new record
                    analytics = Analytics(
                        campaign_id=campaign_id,
                        date=date_str,
                        **metrics
                    )
                    session.add(analytics)
                    session.commit()
                    session.refresh(analytics)
                    logger.debug(f"Created analytics for campaign {campaign_id} on {date_str}")
                    return analytics
        except Exception as e:
            logger.error(f"Failed to record analytics for campaign {campaign_id}: {e}")
            raise

    def get_campaign_analytics(self, campaign_id: int, days: int = 30) -> List[Analytics]:
        """Get analytics for a campaign"""
        with self.get_session() as session:
            analytics = session.exec(
                select(Analytics)
                .where(Analytics.campaign_id == campaign_id)
                .order_by(Analytics.date.desc())
                .limit(days)
            ).all()
            return list(analytics)

    # Settings operations
    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get setting value"""
        try:
            with self.get_session() as session:
                setting = session.exec(
                    select(Settings).where(Settings.key == key)
                ).first()
                if setting:
                    try:
                        value = json.loads(setting.value)
                        logger.debug(f"Retrieved setting '{key}': {value}")
                        return value
                    except (json.JSONDecodeError, TypeError):
                        logger.debug(f"Retrieved setting '{key}': {setting.value}")
                        return setting.value
                logger.debug(f"Setting '{key}' not found, returning default: {default}")
                return default
        except Exception as e:
            logger.error(f"Failed to get setting '{key}': {e}")
            return default

    def set_setting(self, key: str, value: Any, description: str = None):
        """Set setting value"""
        try:
            with self.get_session() as session:
                existing = session.exec(
                    select(Settings).where(Settings.key == key)
                ).first()

                value_str = json.dumps(value) if not isinstance(value, str) else value

                if existing:
                    existing.value = value_str
                    existing.updated_at = datetime.now()
                    if description:
                        existing.description = description
                    logger.debug(f"Updated setting '{key}' = {value}")
                else:
                    setting = Settings(
                        key=key,
                        value=value_str,
                        description=description
                    )
                    session.add(setting)
                    logger.debug(f"Created setting '{key}' = {value}")

                session.commit()
        except Exception as e:
            logger.error(f"Failed to set setting '{key}': {e}")
            raise

    # Persisted daily rate-limiting operations
    def get_daily_connection_count(self, date_str: str) -> int:
        """Get the persisted connection count for a given local day.

        Returns 0 when no row exists for that day, so a new local day always
        starts at zero (the counter self-clears on date rollover).
        """
        try:
            with self.get_session() as session:
                row = session.exec(
                    select(DailyConnectionCount).where(
                        DailyConnectionCount.date == date_str
                    )
                ).first()
                count = row.count if row else 0
                logger.debug(f"Daily connection count for {date_str}: {count}")
                return count
        except Exception as e:
            logger.error(f"Failed to get daily connection count for {date_str}: {e}")
            raise

    def increment_daily_connection_count(self, date_str: str) -> int:
        """Atomically increment the connection count for a given local day.

        A single SQLite upsert (``INSERT ... ON CONFLICT DO UPDATE
        SET count = count + 1``) so two same-day runs cannot race a
        read-modify-write. Unconditional — use :meth:`reserve_daily_slot` when
        the increment must respect the daily limit. Returns the new count.
        """
        try:
            now = datetime.now(timezone.utc)
            now_naive = datetime.now()
            with self.get_session() as session:
                stmt = sqlite_insert(DailyConnectionCount).values(
                    date=date_str,
                    count=1,
                    last_action_at=now,
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["date"],
                    set_={
                        "count": DailyConnectionCount.count + 1,
                        "last_action_at": now,
                        "updated_at": now_naive,
                    },
                )
                session.exec(stmt)
                session.commit()
                count = session.exec(
                    select(DailyConnectionCount.count).where(
                        DailyConnectionCount.date == date_str
                    )
                ).first()
                logger.debug(
                    f"Incremented daily connection count for {date_str} to {count}"
                )
                return count
        except Exception as e:
            logger.error(
                f"Failed to increment daily connection count for {date_str}: {e}"
            )
            raise

    def reserve_daily_slot(self, date_str: str, limit: int) -> Optional[int]:
        """Atomically claim one connection slot for the day if under ``limit``.

        Performed as a single conditional SQLite upsert
        (``INSERT ... ON CONFLICT DO UPDATE SET count = count + 1
        WHERE count < :limit``) so the check and the increment cannot be raced
        by a concurrent run: only one process can claim the slot that brings the
        count to ``limit``. Reserving *before* the network send closes the
        check-then-send window that would otherwise let two runs both send while
        at ``limit - 1``.

        Returns the new cumulative count when a slot was claimed, or ``None``
        when the day is already at the limit (caller must stop). This does NOT
        touch ``last_action_at`` — that timestamp drives the inter-session
        cooldown and must reflect an actual send, so it is stamped separately
        by :meth:`mark_connection_sent` only on confirmed success.

        If a reserved slot is not used (e.g. the send fails), call
        :meth:`release_daily_slot` to give it back.
        """
        if limit <= 0:
            # No slots exist; the initial INSERT path can't honour the WHERE
            # guard, so refuse explicitly.
            return None
        try:
            now_naive = datetime.now()
            with self.get_session() as session:
                stmt = sqlite_insert(DailyConnectionCount).values(
                    date=date_str,
                    count=1,
                )
                # The WHERE guard makes the conflict update a no-op once the day
                # is at the limit. rowcount distinguishes the two count==limit
                # cases that a value read alone cannot: claiming the final slot
                # (1 row affected) vs. an already-full day blocking the update
                # (0 rows affected).
                stmt = stmt.on_conflict_do_update(
                    index_elements=["date"],
                    set_={
                        "count": DailyConnectionCount.count + 1,
                        "updated_at": now_naive,
                    },
                    where=DailyConnectionCount.count < limit,
                )
                result = session.exec(stmt)
                session.commit()
                if result.rowcount == 0:
                    # Guard blocked the update: the day is already at the limit.
                    logger.debug(
                        f"Daily slot reservation refused for {date_str}: "
                        f"already at limit {limit}"
                    )
                    return None
                count = session.exec(
                    select(DailyConnectionCount.count).where(
                        DailyConnectionCount.date == date_str
                    )
                ).first()
                logger.debug(
                    f"Reserved daily connection slot for {date_str}: {count}/{limit}"
                )
                return count
        except Exception as e:
            logger.error(
                f"Failed to reserve daily connection slot for {date_str}: {e}"
            )
            raise

    def release_daily_slot(self, date_str: str) -> None:
        """Give back one previously reserved slot (e.g. when a send fails).

        Atomically decrements the day's count, never below zero. Best-effort:
        a failure here only over-counts the day slightly (fails safe toward the
        cap), so it is logged rather than raised.
        """
        try:
            with self.get_session() as session:
                stmt = (
                    update(DailyConnectionCount)
                    .where(
                        DailyConnectionCount.date == date_str,
                        DailyConnectionCount.count > 0,
                    )
                    .values(
                        count=DailyConnectionCount.count - 1,
                        updated_at=datetime.now(),
                    )
                )
                session.exec(stmt)
                session.commit()
                logger.debug(f"Released a daily connection slot for {date_str}")
        except Exception as e:
            logger.error(f"Failed to release daily connection slot for {date_str}: {e}")

    def mark_connection_sent(self, date_str: str) -> None:
        """Stamp the last-action timestamp after a request was actually sent.

        Kept separate from slot reservation so the inter-session cooldown
        reflects real sends only: a reserved-then-released slot (failed send)
        must not leave a stale ``last_action_at`` that would falsely trigger a
        cooldown on the next run. Best-effort; logged rather than raised.
        """
        try:
            with self.get_session() as session:
                stmt = (
                    update(DailyConnectionCount)
                    .where(DailyConnectionCount.date == date_str)
                    .values(last_action_at=datetime.now(timezone.utc))
                )
                session.exec(stmt)
                session.commit()
                logger.debug(f"Marked a connection sent for {date_str}")
        except Exception as e:
            logger.error(f"Failed to mark connection sent for {date_str}: {e}")

    def get_last_connection_at(self) -> Optional[datetime]:
        """Return the most recent connection timestamp across all days.

        Used to enforce the optional inter-session cooldown when a new run
        starts shortly after a previous one ended. Returns None if no
        connections have ever been recorded.
        """
        try:
            with self.get_session() as session:
                row = session.exec(
                    select(DailyConnectionCount)
                    .where(DailyConnectionCount.last_action_at != None)  # noqa: E711
                    .order_by(DailyConnectionCount.last_action_at.desc())
                ).first()
                return row.last_action_at if row else None
        except Exception as e:
            logger.error(f"Failed to get last connection timestamp: {e}")
            raise

    # Campaign statistics
    def update_campaign_stats(self, campaign_id: int):
        """Update campaign statistics based on contacts"""
        try:
            with self.get_session() as session:
                campaign = session.get(Campaign, campaign_id)
                if not campaign:
                    logger.warning(f"Campaign {campaign_id} not found for stats update")
                    return

                contacts = session.exec(
                    select(Contact).where(Contact.campaign_id == campaign_id)
                ).all()

                # "possibly_sent" (issue #31) is an assumed-sent invite that
                # consumed a daily slot, so it counts as sent and as pending
                # (awaiting acceptance) just like "sent" — otherwise an ambiguous
                # send would under-report totals and overstate the acceptance rate.
                # "reserved" (issue #39) is a pre-send skip marker only (no invite
                # is known to be out), so it is deliberately excluded from both.
                total_sent = len([c for c in contacts if c.status in ["sent", "possibly_sent", "accepted", "declined"]])
                total_accepted = len([c for c in contacts if c.status == "accepted"])
                total_pending = len([c for c in contacts if c.status in ["sent", "possibly_sent"]])

                campaign.total_sent = total_sent
                campaign.total_accepted = total_accepted
                campaign.total_pending = total_pending
                campaign.updated_at = datetime.now()

                session.commit()
                logger.debug(f"Updated stats for campaign {campaign_id}: sent={total_sent}, accepted={total_accepted}, pending={total_pending}")
        except Exception as e:
            logger.error(f"Failed to update campaign stats for {campaign_id}: {e}")
            raise

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get overall dashboard statistics"""
        try:
            with self.get_session() as session:
                campaigns = session.exec(select(Campaign)).all()
                contacts = session.exec(select(Contact)).all()

                total_campaigns = len(campaigns)
                active_campaigns = len([c for c in campaigns if c.active])
                total_contacts = len(contacts)
                # See update_campaign_stats: "possibly_sent" counts as sent and
                # pending (assumed-sent, awaiting acceptance) so an ambiguous send
                # doesn't under-report or skew the acceptance rate. "reserved"
                # (issue #39, a pre-send skip marker only) is excluded from both.
                total_sent = len([c for c in contacts if c.status in ["sent", "possibly_sent", "accepted", "declined"]])
                total_accepted = len([c for c in contacts if c.status == "accepted"])
                total_pending = len([c for c in contacts if c.status in ["sent", "possibly_sent"]])

                acceptance_rate = (total_accepted / total_sent * 100) if total_sent > 0 else 0

                logger.debug(f"Generated dashboard stats: {total_campaigns} campaigns, {total_contacts} contacts")

                return {
                    "total_campaigns": total_campaigns,
                    "active_campaigns": active_campaigns,
                    "total_contacts": total_contacts,
                    "total_sent": total_sent,
                    "total_accepted": total_accepted,
                    "total_pending": total_pending,
                    "acceptance_rate": round(acceptance_rate, 1)
                }
        except Exception as e:
            logger.error(f"Failed to get dashboard stats: {e}")
            raise