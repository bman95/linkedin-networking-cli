import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import and_, event, func, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, SQLModel, create_engine, delete, select, update

from utils.logging import get_logger

from .models import (
    PENDING_STATUSES,
    SENT_STATUSES,
    Analytics,
    Campaign,
    Contact,
    ContactStatus,
    DailyConnectionCount,
    Settings,
)

logger = get_logger(__name__)


def _stats_from_status_counts(status_counts: dict[str, int]) -> dict[str, int]:
    """Fold per-status contact counts into the sent/accepted/pending totals.

    The single place the status-group math lives for the read paths
    (``get_campaign_contact_stats`` and its batch variant), using the same
    ``SENT_STATUSES``/``PENDING_STATUSES`` groups as ``update_campaign_stats``
    so derived and stored totals can never disagree in definition (issue #66).
    """
    return {
        "total_sent": sum(status_counts.get(s, 0) for s in SENT_STATUSES),
        "total_accepted": status_counts.get(ContactStatus.ACCEPTED, 0),
        "total_pending": sum(status_counts.get(s, 0) for s in PENDING_STATUSES),
    }


class DatabaseManager:
    """Database operations manager for LinkedIn networking CLI"""

    def __init__(self, db_path: str = "linkedin_networking.db"):
        self.db_path = Path(db_path)
        logger.info(f"Initializing database at: {self.db_path}")
        # check_same_thread=False: the engine is shared across the sync CLI,
        # Textual worker threads and the async automation thread; SQLAlchemy's
        # pool hands each thread its own connection, so this is safe.
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        self._configure_sqlite_pragmas()
        self.create_tables()

    def _configure_sqlite_pragmas(self) -> None:
        """Register per-connection SQLite PRAGMAs for safe concurrent access.

        Applied on EVERY new DBAPI connection: ``busy_timeout`` and
        ``foreign_keys`` are connection-scoped in SQLite, so they must be
        re-issued each time. ``journal_mode=WAL`` is persistent per database
        file (re-issuing is a cheap no-op) and lets concurrent readers coexist
        with a writer; an in-memory DB cannot use WAL, so it is skipped there.
        """
        is_memory = str(self.db_path) == ":memory:"

        @event.listens_for(self.engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA busy_timeout=5000")
                cursor.execute("PRAGMA foreign_keys=ON")
                if not is_memory:
                    cursor.execute("PRAGMA journal_mode=WAL")
            finally:
                cursor.close()

    def create_tables(self):
        """Create all database tables, migrating an existing DB if needed."""
        try:
            # Add the reservation_token column to an existing contact table FIRST
            # (#39 concurrency): the de-dup below selects Contact ORM objects,
            # which reference reservation_token, so the column must exist before
            # that read. A no-op on a fresh DB (no table) and on an up-to-date DB.
            self._ensure_contact_reservation_token_column()
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

    def _ensure_contact_reservation_token_column(self) -> None:
        """Add the nullable ``reservation_token`` column if an existing DB lacks it.

        Idempotent additive migration (#39 concurrency). ``create_all`` only
        stamps the column onto a freshly created table; an existing contact table
        needs an explicit ``ALTER TABLE ... ADD COLUMN``. SQLite has no
        ``ADD COLUMN IF NOT EXISTS``, so check the existing columns first. A
        no-op when the table is absent (fresh DB) or already has the column.
        """
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

        inspector = sa_inspect(self.engine)
        if not inspector.has_table("contact"):
            return
        columns = {c["name"] for c in inspector.get_columns("contact")}
        if "reservation_token" in columns:
            return
        with self.engine.begin() as conn:
            conn.execute(
                text("ALTER TABLE contact ADD COLUMN reservation_token VARCHAR")
            )
        logger.info("Added reservation_token column to contact table")

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
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

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

    def close(self) -> None:
        """Dispose the engine, closing all pooled SQLite connections.

        SQLAlchemy's pool keeps the underlying ``sqlite3.Connection`` objects
        open until they are garbage-collected, which surfaces as
        ``ResourceWarning: unclosed database`` at interpreter/pytest teardown.
        Call this when the manager is no longer needed.
        """
        self.engine.dispose()

    # Campaign operations
    def create_campaign(self, campaign_data: dict[str, Any]) -> Campaign:
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

    def get_campaigns(self, active_only: bool = True) -> list[Campaign]:
        """Get all campaigns"""
        try:
            with self.get_session() as session:
                statement = select(Campaign)
                if active_only:
                    # noqa applies to the SQLAlchemy column expression, not a truth test.
                    statement = statement.where(Campaign.active == True)  # noqa: E712
                campaigns = session.exec(statement).all()
                logger.debug(f"Retrieved {len(campaigns)} campaigns (active_only={active_only})")
                return list(campaigns)
        except Exception as e:
            logger.error(f"Failed to get campaigns: {e}")
            raise

    def get_campaign(self, campaign_id: int) -> Campaign | None:
        """Get campaign by ID"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            return campaign

    def update_campaign(self, campaign_id: int, updates: dict[str, Any]) -> Campaign | None:
        """Update campaign"""
        try:
            with self.get_session() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign:
                    for key, value in updates.items():
                        setattr(campaign, key, value)
                    campaign.updated_at = datetime.now(UTC)
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
        """Delete campaign and its contacts and analytics"""
        try:
            with self.get_session() as session:
                campaign = session.get(Campaign, campaign_id)
                if campaign:
                    # Bulk-delete dependents first so the FK constraint
                    # (PRAGMA foreign_keys=ON) never sees an orphaned child.
                    contact_result = session.exec(
                        delete(Contact).where(Contact.campaign_id == campaign_id)
                    )
                    contact_count = contact_result.rowcount or 0
                    analytics_result = session.exec(
                        delete(Analytics).where(Analytics.campaign_id == campaign_id)
                    )
                    analytics_count = analytics_result.rowcount or 0

                    # Delete campaign
                    session.delete(campaign)
                    session.commit()
                    logger.info(
                        f"Deleted campaign {campaign_id}, {contact_count} associated "
                        f"contacts and {analytics_count} analytics rows"
                    )
                    return True
                logger.warning(f"Campaign {campaign_id} not found for deletion")
                return False
        except Exception as e:
            logger.error(f"Failed to delete campaign {campaign_id}: {e}")
            raise

    # Contact operations
    def create_contact(self, contact_data: dict[str, Any]) -> Contact:
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
        self,
        contact_data: dict[str, Any],
        protect_finalized: bool = False,
        protect_other_reservation: bool = False,
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

        With ``protect_other_reservation=True`` the guard ALSO refuses to write
        when the existing row is a ``reserved`` marker owned by a DIFFERENT
        attempt (its ``reservation_token`` is non-null and differs from the token
        in ``contact_data``). This is how a second concurrent attempt on the same
        profile avoids stealing a live reservation the first attempt may already
        have clicked Send on (#39 concurrency). An un-owned (legacy/null-token)
        ``reserved`` row, or one we already own, is still claimable. The caller
        re-reads the returned row and compares tokens to decide proceed-vs-abort.

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
            now = datetime.now(UTC)
            # The conflict update sets every provided mutable column plus
            # updated_at; never the conflict keys or created_at.
            update_set = {
                key: value
                for key, value in contact_data.items()
                if key not in self._UPSERT_PRESERVE_COLUMNS
            }
            update_set["updated_at"] = now
            with self.get_session() as session:
                stmt = sqlite_insert(Contact).values(**contact_data)
                on_conflict_kwargs = {
                    "index_elements": ["campaign_id", "profile_url"],
                    "set_": update_set,
                }
                guards = []
                if protect_finalized:
                    # Make the update a no-op when the existing row is finalized,
                    # so a concurrent run's confirmed/ambiguous send is never
                    # downgraded to a reservation/retryable marker.
                    guards.append(
                        Contact.status.notin_(self._FINALIZED_CONTACT_STATUSES)
                    )
                if protect_other_reservation:
                    # Don't steal a live reservation owned by another attempt:
                    # block the update when the existing row is reserved with a
                    # non-null token that isn't ours. A null-token (legacy) or
                    # our-own reserved row stays claimable.
                    new_token = contact_data.get("reservation_token")
                    guards.append(
                        or_(
                            Contact.status != "reserved",
                            Contact.reservation_token.is_(None),
                            Contact.reservation_token == new_token,
                        )
                    )
                if guards:
                    on_conflict_kwargs["where"] = (
                        guards[0] if len(guards) == 1 else and_(*guards)
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
        reservation_token: str | None = None,
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

        When ``reservation_token`` is also given, the delete is scoped to the
        ``reserved`` row we OWN (its token matches): a concurrent attempt's live
        reservation — which it may already have clicked Send on — is never
        deleted (#39 concurrency, finding 1). A null token (general cleanup)
        leaves the behaviour token-agnostic.

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
                    if (
                        reservation_token is not None
                        and row.status == "reserved"
                        and row.reservation_token != reservation_token
                    ):
                        # A reservation owned by another attempt — never delete it.
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

    def downgrade_own_reservation_to_found(
        self,
        campaign_id: int,
        profile_url: str,
        reservation_token: str,
        notes: str | None = None,
    ) -> int:
        """Flip a ``reserved`` row we OWN back to retryable ``found``; return count.

        Clean pre-send failure (#39 concurrency, finding 2): only the attempt that
        holds the reservation may downgrade it. The ``reservation_token`` WHERE
        clause means a concurrent attempt's live reservation — which it may
        already have clicked Send on — is never clobbered to a retryable
        ``found`` (which a later dedup would treat as re-contactable). A no-op
        (``rowcount == 0``) when our reservation was already reconciled or claimed
        away; that is correct — there is nothing of ours to downgrade and the
        other attempt's row must be left alone. Best-effort: logged, not raised.
        """
        try:
            with self.get_session() as session:
                stmt = (
                    update(Contact)
                    .where(
                        Contact.campaign_id == campaign_id,
                        Contact.profile_url == profile_url,
                        Contact.status == "reserved",
                        Contact.reservation_token == reservation_token,
                    )
                    .values(
                        status="found",
                        reservation_token=None,
                        notes=notes,
                        updated_at=datetime.now(UTC),
                    )
                )
                result = session.exec(stmt)
                session.commit()
                downgraded = result.rowcount or 0
                if downgraded:
                    logger.debug(
                        f"Downgraded own reserved marker to found for {profile_url}"
                    )
                return downgraded
        except Exception as e:
            logger.error(
                f"Failed to downgrade own reservation for {profile_url}: {e}"
            )
            return 0

    def promote_reserved_to_possibly_sent(
        self,
        campaign_id: int,
        profile_url: str,
        reservation_token: str | None = None,
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
        protects it. When ``reservation_token`` is given the promotion is scoped
        to the reservation we OWN, so it never flips a concurrent attempt's live
        reservation. A no-op when no matching ``reserved`` row remains (already
        reconciled by a concurrent run). Best-effort: logged, not raised.
        """
        try:
            with self.get_session() as session:
                where_clauses = [
                    Contact.campaign_id == campaign_id,
                    Contact.profile_url == profile_url,
                    Contact.status == "reserved",
                ]
                if reservation_token is not None:
                    where_clauses.append(
                        Contact.reservation_token == reservation_token
                    )
                stmt = (
                    update(Contact)
                    .where(*where_clauses)
                    .values(
                        status="possibly_sent",
                        connection_sent_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
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

    def get_contacts(self, campaign_id: int | None = None) -> list[Contact]:
        """Get contacts, optionally filtered by campaign"""
        with self.get_session() as session:
            statement = select(Contact)
            if campaign_id:
                statement = statement.where(Contact.campaign_id == campaign_id)
            contacts = session.exec(statement).all()
            return list(contacts)

    def get_contact(self, contact_id: int) -> Contact | None:
        """Get contact by ID"""
        with self.get_session() as session:
            contact = session.get(Contact, contact_id)
            return contact

    def update_contact(self, contact_id: int, updates: dict[str, Any]) -> Contact | None:
        """Update contact"""
        try:
            with self.get_session() as session:
                contact = session.get(Contact, contact_id)
                if contact:
                    for key, value in updates.items():
                        setattr(contact, key, value)
                    contact.updated_at = datetime.now(UTC)
                    session.commit()
                    session.refresh(contact)
                    logger.debug(f"Updated contact {contact_id}: {list(updates.keys())}")
                    return contact
                logger.warning(f"Contact {contact_id} not found for update")
                return None
        except Exception as e:
            logger.error(f"Failed to update contact {contact_id}: {e}")
            raise

    def get_contacts_by_status(self, campaign_id: int, status: str) -> list[Contact]:
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
    def record_daily_analytics(self, campaign_id: int, date_str: str, metrics: dict[str, Any]):
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
                    existing.updated_at = datetime.now(UTC)
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

    def get_campaign_analytics(self, campaign_id: int, days: int = 30) -> list[Analytics]:
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
                    existing.updated_at = datetime.now(UTC)
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

    def get_weekly_connection_count(
        self, reference_date: date | None = None
    ) -> int:
        """Sum the connection counts over the trailing 7 local days.

        LinkedIn's binding invitation cap is a rolling ~weekly one, so this adds
        up ``DailyConnectionCount.count`` for ``reference_date`` (default the
        local ``date.today()`` the daily counter uses) plus the previous 6 days,
        keyed by the same ``YYYY-MM-DD`` local-day strings. Days outside that
        7-day window are excluded; absent days simply contribute 0. Returns the
        cumulative weekly total so a run can proactively stop before hitting the
        weekly-limit modal.
        """
        try:
            ref = reference_date or date.today()
            day_keys = [(ref - timedelta(days=n)).isoformat() for n in range(7)]
            with self.get_session() as session:
                total = session.exec(
                    select(func.coalesce(func.sum(DailyConnectionCount.count), 0))
                    .where(DailyConnectionCount.date.in_(day_keys))
                ).one()
                logger.debug(
                    f"Weekly connection count ending {ref.isoformat()}: {total}"
                )
                return total or 0
        except Exception as e:
            logger.error(f"Failed to get weekly connection count: {e}")
            raise

    def increment_daily_connection_count(self, date_str: str) -> int:
        """Atomically increment the connection count for a given local day.

        A single SQLite upsert (``INSERT ... ON CONFLICT DO UPDATE
        SET count = count + 1``) so two same-day runs cannot race a
        read-modify-write. Unconditional — use :meth:`reserve_daily_slot` when
        the increment must respect the daily limit. Returns the new count.
        """
        try:
            now = datetime.now(UTC)
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
                        "updated_at": now,
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

    def reserve_daily_slot(self, date_str: str, limit: int) -> int | None:
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
            now = datetime.now(UTC)
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
                        "updated_at": now,
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
                        updated_at=datetime.now(UTC),
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
                    .values(last_action_at=datetime.now(UTC))
                )
                session.exec(stmt)
                session.commit()
                logger.debug(f"Marked a connection sent for {date_str}")
        except Exception as e:
            logger.error(f"Failed to mark connection sent for {date_str}: {e}")

    def get_last_connection_at(self) -> datetime | None:
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

                # "possibly_sent" (issue #31) is an assumed-sent invite that
                # consumed a daily slot, so it counts as sent and as pending
                # (awaiting acceptance) just like "sent" — otherwise an ambiguous
                # send would under-report totals and overstate the acceptance rate.
                # "reserved" (issue #39) is a pre-send skip marker only (no invite
                # is known to be out), so it is deliberately excluded from both.
                status_counts = dict(
                    session.exec(
                        select(Contact.status, func.count())
                        .where(Contact.campaign_id == campaign_id)
                        .group_by(Contact.status)
                    ).all()
                )
                total_sent = sum(status_counts.get(s, 0) for s in SENT_STATUSES)
                total_accepted = status_counts.get(ContactStatus.ACCEPTED, 0)
                total_pending = sum(
                    status_counts.get(s, 0) for s in PENDING_STATUSES
                )

                campaign.total_sent = total_sent
                campaign.total_accepted = total_accepted
                campaign.total_pending = total_pending
                campaign.updated_at = datetime.now(UTC)

                session.commit()
                logger.debug(f"Updated stats for campaign {campaign_id}: sent={total_sent}, accepted={total_accepted}, pending={total_pending}")
        except Exception as e:
            logger.error(f"Failed to update campaign stats for {campaign_id}: {e}")
            raise

    def get_campaign_contact_stats(self, campaign_id: int) -> dict[str, int]:
        """Live sent/accepted/pending counts for one campaign, derived directly
        from ``contacts`` via SQL COUNT/GROUP BY.

        This is the read-only counterpart of ``update_campaign_stats``: same
        status-group definitions, same query shape, no write. Screens that show
        campaign numbers read this (or the batch variant below) instead of the
        denormalized ``Campaign.total_*`` columns, so they can never contradict
        each other when those columns have drifted stale (issue #66).
        """
        with self.get_session() as session:
            status_counts = dict(
                session.exec(
                    select(Contact.status, func.count())
                    .where(Contact.campaign_id == campaign_id)
                    .group_by(Contact.status)
                ).all()
            )
        return _stats_from_status_counts(status_counts)

    def get_all_campaign_contact_stats(self) -> dict[int, dict[str, int]]:
        """Live per-campaign sent/accepted/pending counts for every campaign.

        Batch variant of ``get_campaign_contact_stats`` for list views: one
        GROUP BY over ``contacts`` regardless of campaign count, instead of a
        query per campaign. Campaigns with no contact rows are absent from the
        result — callers default their counts to zero.
        """
        with self.get_session() as session:
            rows = session.exec(
                select(Contact.campaign_id, Contact.status, func.count())
                .group_by(Contact.campaign_id, Contact.status)
            ).all()
        per_campaign: dict[int, dict[str, int]] = {}
        for campaign_id, status, count in rows:
            per_campaign.setdefault(campaign_id, {})[status] = count
        return {
            campaign_id: _stats_from_status_counts(status_counts)
            for campaign_id, status_counts in per_campaign.items()
        }

    def get_dashboard_stats(self) -> dict[str, Any]:
        """Get overall dashboard statistics"""
        try:
            with self.get_session() as session:
                total_campaigns = session.exec(
                    select(func.count()).select_from(Campaign)
                ).one()
                active_campaigns = session.exec(
                    select(func.count())
                    .select_from(Campaign)
                    .where(Campaign.active == True)  # noqa: E712
                ).one()
                # See update_campaign_stats: "possibly_sent" counts as sent and
                # pending (assumed-sent, awaiting acceptance) so an ambiguous send
                # doesn't under-report or skew the acceptance rate. "reserved"
                # (issue #39, a pre-send skip marker only) is excluded from both.
                status_counts = dict(
                    session.exec(
                        select(Contact.status, func.count()).group_by(Contact.status)
                    ).all()
                )
                total_contacts = sum(status_counts.values())
                total_sent = sum(status_counts.get(s, 0) for s in SENT_STATUSES)
                total_accepted = status_counts.get(ContactStatus.ACCEPTED, 0)
                total_pending = sum(
                    status_counts.get(s, 0) for s in PENDING_STATUSES
                )

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