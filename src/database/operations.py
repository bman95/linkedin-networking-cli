from datetime import datetime, date
from typing import List, Optional, Dict, Any
from sqlmodel import SQLModel, create_engine, Session, select
from pathlib import Path
import json

from .models import Campaign, Contact, Analytics, Settings


class DatabaseManager:
    """Database operations manager for LinkedIn networking CLI"""

    def __init__(self, db_path: str = "linkedin_networking.db"):
        self.db_path = Path(db_path)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self.create_tables()

    def create_tables(self):
        """Create all database tables"""
        SQLModel.metadata.create_all(self.engine)

    def get_session(self) -> Session:
        """Get database session"""
        return Session(self.engine)

    # Campaign operations
    def create_campaign(self, campaign_data: Dict[str, Any]) -> Campaign:
        """Create a new campaign"""
        campaign = Campaign(**campaign_data)
        with self.get_session() as session:
            session.add(campaign)
            session.commit()
            session.refresh(campaign)
            return campaign

    def get_campaigns(self, active_only: bool = True) -> List[Campaign]:
        """Get all campaigns"""
        with self.get_session() as session:
            statement = select(Campaign)
            if active_only:
                statement = statement.where(Campaign.active == True)
            campaigns = session.exec(statement).all()
            return list(campaigns)

    def get_campaign(self, campaign_id: int) -> Optional[Campaign]:
        """Get campaign by ID"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            return campaign

    def update_campaign(self, campaign_id: int, updates: Dict[str, Any]) -> Optional[Campaign]:
        """Update campaign"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign:
                for key, value in updates.items():
                    setattr(campaign, key, value)
                campaign.updated_at = datetime.now()
                session.commit()
                session.refresh(campaign)
                return campaign
            return None

    def delete_campaign(self, campaign_id: int) -> bool:
        """Delete campaign and its contacts"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            if campaign:
                # Delete associated contacts first
                contacts = session.exec(
                    select(Contact).where(Contact.campaign_id == campaign_id)
                ).all()
                for contact in contacts:
                    session.delete(contact)

                # Delete campaign
                session.delete(campaign)
                session.commit()
                return True
            return False

    # Contact operations
    def create_contact(self, contact_data: Dict[str, Any]) -> Contact:
        """Create a new contact"""
        contact = Contact(**contact_data)
        with self.get_session() as session:
            session.add(contact)
            session.commit()
            session.refresh(contact)
            return contact

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
        with self.get_session() as session:
            contact = session.get(Contact, contact_id)
            if contact:
                for key, value in updates.items():
                    setattr(contact, key, value)
                contact.updated_at = datetime.now()
                session.commit()
                session.refresh(contact)
                return contact
            return None

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
                return analytics

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
        with self.get_session() as session:
            setting = session.exec(
                select(Settings).where(Settings.key == key)
            ).first()
            if setting:
                try:
                    return json.loads(setting.value)
                except (json.JSONDecodeError, TypeError):
                    return setting.value
            return default

    def set_setting(self, key: str, value: Any, description: str = None):
        """Set setting value"""
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
            else:
                setting = Settings(
                    key=key,
                    value=value_str,
                    description=description
                )
                session.add(setting)

            session.commit()

    # Campaign statistics
    def update_campaign_stats(self, campaign_id: int):
        """Update campaign statistics based on contacts"""
        with self.get_session() as session:
            campaign = session.get(Campaign, campaign_id)
            if not campaign:
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

    def get_dashboard_stats(self) -> Dict[str, Any]:
        """Get overall dashboard statistics"""
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

            return {
                "total_campaigns": total_campaigns,
                "active_campaigns": active_campaigns,
                "total_contacts": total_contacts,
                "total_sent": total_sent,
                "total_accepted": total_accepted,
                "total_pending": total_pending,
                "acceptance_rate": round(acceptance_rate, 1)
            }