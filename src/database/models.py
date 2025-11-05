from datetime import datetime
from typing import Optional, Dict, Any
from sqlmodel import SQLModel, Field, create_engine, Session, select
import json


class Campaign(SQLModel, table=True):
    """Campaign model for storing LinkedIn networking campaigns"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: Optional[str] = None

    # Targeting criteria
    keywords: Optional[str] = None
    location: Optional[str] = None
    industry: Optional[str] = None
    company_size: Optional[str] = None
    experience_level: Optional[str] = None

    # Campaign settings
    daily_limit: int = Field(default=20)
    message_template: str = Field(default="Hi {name}, I'd like to connect with you!")
    active: bool = Field(default=True)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None
    last_run: Optional[datetime] = None

    # Statistics
    total_sent: int = Field(default=0)
    total_accepted: int = Field(default=0)
    total_pending: int = Field(default=0)


class Contact(SQLModel, table=True):
    """Contact model for storing individual LinkedIn connections"""
    id: Optional[int] = Field(default=None, primary_key=True)
    campaign_id: int = Field(foreign_key="campaign.id")

    # Contact info
    name: str
    profile_url: str = Field(index=True)
    headline: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None

    # Connection status
    status: str = Field(default="found")  # found, sent, accepted, declined, failed
    connection_sent_at: Optional[datetime] = None
    connection_accepted_at: Optional[datetime] = None

    # Additional data
    notes: Optional[str] = None
    contact_info: str = Field(default="{}")  # JSON string for email, phone, etc.

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None

    def get_contact_info(self) -> Dict[str, Any]:
        """Parse contact info JSON string"""
        try:
            return json.loads(self.contact_info)
        except (json.JSONDecodeError, TypeError):
            return {}

    def set_contact_info(self, info: Dict[str, Any]) -> None:
        """Set contact info as JSON string"""
        self.contact_info = json.dumps(info)


class Analytics(SQLModel, table=True):
    """Analytics model for tracking campaign performance"""
    id: Optional[int] = Field(default=None, primary_key=True)
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
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None


class Settings(SQLModel, table=True):
    """Settings model for storing app configuration"""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)
    value: str
    description: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: Optional[datetime] = None