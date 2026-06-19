from datetime import datetime, date, timezone
from typing import List, Optional, Dict, Any
from sqlmodel import SQLModel, create_engine, Session, select
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
        """Create all database tables"""
        try:
            SQLModel.metadata.create_all(self.engine)
            logger.info("Database tables created/verified successfully")
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}")
            raise

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

        Uses a single SQLite upsert (``INSERT ... ON CONFLICT DO UPDATE
        SET count = count + 1``) so two same-day runs cannot race a
        read-modify-write and under-count, and the unique-date insert race is
        resolved in the database rather than raising. Records the last-action
        timestamp (used for the optional inter-session cooldown) and returns
        the new cumulative count.
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

                total_sent = len([c for c in contacts if c.status in ["sent", "accepted", "declined"]])
                total_accepted = len([c for c in contacts if c.status == "accepted"])
                total_pending = len([c for c in contacts if c.status == "sent"])

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
                total_sent = len([c for c in contacts if c.status in ["sent", "accepted", "declined"]])
                total_accepted = len([c for c in contacts if c.status == "accepted"])
                total_pending = len([c for c in contacts if c.status == "sent"])

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