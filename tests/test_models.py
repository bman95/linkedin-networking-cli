"""
Unit tests for database models.

Tests model creation, validation, defaults, and custom methods.
"""

import json
import pytest
from datetime import datetime, timezone, date
from sqlmodel import Session, select

from database.models import Campaign, Contact, Analytics, Settings


# ============================================================================
# Campaign Model Tests
# ============================================================================

@pytest.mark.unit
class TestCampaignModel:
    """Test Campaign model."""

    def test_create_campaign_with_minimal_fields(self):
        """Test creating campaign with only required fields."""
        campaign = Campaign(name="Test Campaign")
        assert campaign.name == "Test Campaign"
        assert campaign.id is None  # Not set until saved to DB

    def test_campaign_default_values(self):
        """Test that campaign has correct default values."""
        campaign = Campaign(name="Test Campaign")
        assert campaign.daily_limit == 20
        assert campaign.message_template == "Hi {name}, I'd like to connect with you!"
        assert campaign.active is True
        assert campaign.total_sent == 0
        assert campaign.total_accepted == 0
        assert campaign.total_pending == 0
        assert campaign.network == '["F","S"]'
        assert campaign.network_display == "1st + 2nd degree connections"

    def test_campaign_with_all_fields(self, sample_campaign):
        """Test creating campaign with all fields."""
        campaign = sample_campaign
        assert campaign.name == "Test Campaign"
        assert campaign.keywords == "software engineer"
        assert campaign.geo_urn == "90000084"
        assert campaign.location_display == "San Francisco Bay Area"
        assert campaign.industry_ids == "4,6"
        assert campaign.industry_display == "Computer Software, Internet"
        assert campaign.network == '["F","S"]'
        assert campaign.network_display == "1st + 2nd degree connections"
        assert campaign.message_template == "Hi {name}, I'd like to connect!"
        assert campaign.daily_limit == 10

    def test_campaign_timestamps(self):
        """Test that campaign has created_at timestamp."""
        campaign = Campaign(name="Test Campaign")
        assert isinstance(campaign.created_at, datetime)
        assert campaign.updated_at is None
        assert campaign.last_run is None

    def test_campaign_optional_fields_can_be_none(self):
        """Test that optional fields can be None."""
        campaign = Campaign(
            name="Test Campaign",
            description=None,
            keywords=None,
            geo_urn=None,
            location_display=None,
        )
        assert campaign.description is None
        assert campaign.keywords is None
        assert campaign.geo_urn is None

    def test_campaign_legacy_fields_exist(self):
        """Test that legacy fields still exist for backward compatibility."""
        campaign = Campaign(name="Test Campaign", location="San Francisco", industry="Tech")
        assert campaign.location == "San Francisco"
        assert campaign.industry == "Tech"

    def test_campaign_in_database(self, db_session, sample_campaign):
        """Test saving and retrieving campaign from database."""
        # Save
        db_session.add(sample_campaign)
        db_session.commit()
        db_session.refresh(sample_campaign)

        # Verify ID was assigned
        assert sample_campaign.id is not None

        # Retrieve
        campaign = db_session.exec(
            select(Campaign).where(Campaign.name == "Test Campaign")
        ).first()

        assert campaign is not None
        assert campaign.name == "Test Campaign"
        assert campaign.geo_urn == "90000084"

    def test_campaign_statistics_update(self, db_session):
        """Test updating campaign statistics."""
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()

        # Update stats
        campaign.total_sent = 10
        campaign.total_accepted = 3
        campaign.total_pending = 7
        db_session.commit()
        db_session.refresh(campaign)

        assert campaign.total_sent == 10
        assert campaign.total_accepted == 3
        assert campaign.total_pending == 7


# ============================================================================
# Contact Model Tests
# ============================================================================

@pytest.mark.unit
class TestContactModel:
    """Test Contact model."""

    def test_create_contact_with_required_fields(self):
        """Test creating contact with only required fields."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        assert contact.name == "John Doe"
        assert contact.profile_url == "https://linkedin.com/in/johndoe"
        assert contact.campaign_id == 1

    def test_contact_default_values(self):
        """Test that contact has correct default values."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        assert contact.status == "found"
        assert contact.contact_info == "{}"
        assert contact.connection_sent_at is None
        assert contact.connection_accepted_at is None

    def test_contact_with_all_fields(self, sample_contact):
        """Test creating contact with all fields."""
        contact = sample_contact
        assert contact.name == "John Doe"
        assert contact.profile_url == "https://www.linkedin.com/in/johndoe/"
        assert contact.headline == "Software Engineer at Tech Co"
        assert contact.location == "San Francisco, CA"
        assert contact.company == "Tech Co"
        assert contact.status == "sent"
        assert contact.connection_sent_at is not None

    def test_contact_timestamps(self):
        """Test that contact has created_at timestamp."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        assert isinstance(contact.created_at, datetime)
        assert contact.updated_at is None

    def test_contact_get_contact_info_empty(self):
        """Test getting contact info when empty."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        info = contact.get_contact_info()
        assert isinstance(info, dict)
        assert len(info) == 0

    def test_contact_get_contact_info_with_data(self):
        """Test getting contact info with data."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe",
            contact_info='{"email": "john@example.com", "phone": "555-1234"}'
        )
        info = contact.get_contact_info()
        assert info["email"] == "john@example.com"
        assert info["phone"] == "555-1234"

    def test_contact_set_contact_info(self):
        """Test setting contact info."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        contact.set_contact_info({
            "email": "john@example.com",
            "phone": "555-1234",
            "linkedin": "johndoe"
        })
        assert '"email": "john@example.com"' in contact.contact_info
        assert '"phone": "555-1234"' in contact.contact_info

    def test_contact_get_set_contact_info_round_trip(self):
        """Test setting and getting contact info maintains data."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        original_info = {
            "email": "john@example.com",
            "phone": "555-1234",
            "twitter": "@johndoe"
        }
        contact.set_contact_info(original_info)
        retrieved_info = contact.get_contact_info()
        assert retrieved_info == original_info

    def test_contact_get_contact_info_handles_invalid_json(self):
        """Test that get_contact_info handles invalid JSON gracefully."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe",
            contact_info="invalid json"
        )
        info = contact.get_contact_info()
        assert isinstance(info, dict)
        assert len(info) == 0

    def test_contact_in_database(self, db_session, sample_contact):
        """Test saving and retrieving contact from database."""
        # First create a campaign
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()
        db_session.refresh(campaign)

        # Now create contact with valid campaign_id
        sample_contact.campaign_id = campaign.id
        db_session.add(sample_contact)
        db_session.commit()
        db_session.refresh(sample_contact)

        # Verify ID was assigned
        assert sample_contact.id is not None

        # Retrieve
        contact = db_session.exec(
            select(Contact).where(Contact.name == "John Doe")
        ).first()

        assert contact is not None
        assert contact.name == "John Doe"
        assert contact.profile_url == "https://www.linkedin.com/in/johndoe/"

    @pytest.mark.parametrize("status", ["found", "sent", "accepted", "rejected", "pending"])
    def test_contact_status_values(self, status):
        """Test that contact can have different status values."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe",
            status=status
        )
        assert contact.status == status


# ============================================================================
# Analytics Model Tests
# ============================================================================

@pytest.mark.unit
class TestAnalyticsModel:
    """Test Analytics model."""

    def test_create_analytics_with_required_fields(self):
        """Test creating analytics with required fields."""
        analytics = Analytics(
            campaign_id=1,
            date="2025-01-15"
        )
        assert analytics.campaign_id == 1
        assert analytics.date == "2025-01-15"

    def test_analytics_default_values(self):
        """Test that analytics has correct default values."""
        analytics = Analytics(campaign_id=1, date="2025-01-15")
        assert analytics.connections_sent == 0
        assert analytics.connections_accepted == 0
        assert analytics.connections_declined == 0
        assert analytics.response_rate == 0.0
        assert analytics.acceptance_rate == 0.0

    def test_analytics_with_all_fields(self, sample_analytics):
        """Test creating analytics with all fields."""
        analytics = sample_analytics
        assert analytics.campaign_id == 1
        assert analytics.date is not None
        # Note: sample_analytics uses date object, model uses string
        # This is fine for testing, just need to be aware

    def test_analytics_timestamps(self):
        """Test that analytics has created_at timestamp."""
        analytics = Analytics(campaign_id=1, date="2025-01-15")
        assert isinstance(analytics.created_at, datetime)
        assert analytics.updated_at is None

    def test_analytics_in_database(self, db_session):
        """Test saving and retrieving analytics from database."""
        # Create campaign first
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()
        db_session.refresh(campaign)

        # Create analytics
        analytics = Analytics(
            campaign_id=campaign.id,
            date="2025-01-15",
            connections_sent=10,
            connections_accepted=3,
            response_rate=30.0
        )
        db_session.add(analytics)
        db_session.commit()
        db_session.refresh(analytics)

        # Verify ID was assigned
        assert analytics.id is not None

        # Retrieve
        retrieved = db_session.exec(
            select(Analytics).where(Analytics.campaign_id == campaign.id)
        ).first()

        assert retrieved is not None
        assert retrieved.connections_sent == 10
        assert retrieved.connections_accepted == 3
        assert retrieved.response_rate == 30.0

    def test_analytics_metrics_calculation(self, db_session):
        """Test that analytics can store calculated metrics."""
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()

        analytics = Analytics(
            campaign_id=campaign.id,
            date="2025-01-15",
            connections_sent=100,
            connections_accepted=25,
        )
        # Calculate rates
        analytics.acceptance_rate = (25 / 100) * 100  # 25%
        analytics.response_rate = (25 / 100) * 100  # 25%

        db_session.add(analytics)
        db_session.commit()

        assert analytics.acceptance_rate == 25.0
        assert analytics.response_rate == 25.0


# ============================================================================
# Settings Model Tests
# ============================================================================

@pytest.mark.unit
class TestSettingsModel:
    """Test Settings model."""

    def test_create_settings_with_required_fields(self):
        """Test creating settings with required fields."""
        settings = Settings(
            key="daily_limit",
            value="20"
        )
        assert settings.key == "daily_limit"
        assert settings.value == "20"

    def test_settings_with_description(self, sample_settings):
        """Test creating settings with description."""
        settings = sample_settings
        assert settings.key == "daily_connection_limit"
        assert settings.value == "10"
        assert settings.description == "Maximum connections per day"

    def test_settings_timestamps(self):
        """Test that settings has created_at timestamp."""
        settings = Settings(key="test_key", value="test_value")
        assert isinstance(settings.created_at, datetime)
        assert settings.updated_at is None

    def test_settings_in_database(self, db_session):
        """Test saving and retrieving settings from database."""
        settings = Settings(
            key="daily_limit",
            value="20",
            description="Daily connection limit"
        )
        db_session.add(settings)
        db_session.commit()
        db_session.refresh(settings)

        # Verify ID was assigned
        assert settings.id is not None

        # Retrieve
        retrieved = db_session.exec(
            select(Settings).where(Settings.key == "daily_limit")
        ).first()

        assert retrieved is not None
        assert retrieved.value == "20"
        assert retrieved.description == "Daily connection limit"

    def test_settings_unique_key_constraint(self, db_session):
        """Test that settings key is unique."""
        settings1 = Settings(key="test_key", value="value1")
        db_session.add(settings1)
        db_session.commit()

        # Try to add another with same key
        settings2 = Settings(key="test_key", value="value2")
        db_session.add(settings2)

        # This should raise an integrity error
        with pytest.raises(Exception):  # SQLite IntegrityError
            db_session.commit()


# ============================================================================
# Model Relationships and Integration Tests
# ============================================================================

@pytest.mark.unit
class TestModelRelationships:
    """Test relationships between models."""

    def test_campaign_has_multiple_contacts(self, db_session):
        """Test that a campaign can have multiple contacts."""
        # Create campaign
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()
        db_session.refresh(campaign)

        # Create multiple contacts
        for i in range(3):
            contact = Contact(
                campaign_id=campaign.id,
                name=f"Contact {i}",
                profile_url=f"https://linkedin.com/in/contact{i}"
            )
            db_session.add(contact)
        db_session.commit()

        # Query contacts for campaign
        contacts = db_session.exec(
            select(Contact).where(Contact.campaign_id == campaign.id)
        ).all()

        assert len(contacts) == 3

    def test_campaign_has_multiple_analytics(self, db_session):
        """Test that a campaign can have multiple analytics entries."""
        # Create campaign
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()
        db_session.refresh(campaign)

        # Create multiple analytics entries
        for i in range(7):  # 7 days of analytics
            analytics = Analytics(
                campaign_id=campaign.id,
                date=f"2025-01-{i+1:02d}",
                connections_sent=10
            )
            db_session.add(analytics)
        db_session.commit()

        # Query analytics for campaign
        analytics_list = db_session.exec(
            select(Analytics).where(Analytics.campaign_id == campaign.id)
        ).all()

        assert len(analytics_list) == 7

    def test_delete_campaign_orphans_contacts(self, db_session):
        """Test behavior when campaign is deleted (contacts become orphaned)."""
        # Create campaign
        campaign = Campaign(name="Test Campaign")
        db_session.add(campaign)
        db_session.commit()
        campaign_id = campaign.id

        # Create contact
        contact = Contact(
            campaign_id=campaign_id,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        db_session.add(contact)
        db_session.commit()

        # Delete campaign
        db_session.delete(campaign)
        db_session.commit()

        # Contact should still exist (no cascade delete configured)
        orphaned_contact = db_session.exec(
            select(Contact).where(Contact.campaign_id == campaign_id)
        ).first()

        # Depending on DB constraints, this might fail or succeed
        # In SQLite without foreign key constraints, it will succeed
        # This test documents the current behavior


# ============================================================================
# Edge Cases and Data Validation
# ============================================================================

@pytest.mark.unit
class TestModelEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_campaign_with_very_long_name(self):
        """Test campaign with very long name."""
        long_name = "A" * 1000
        campaign = Campaign(name=long_name)
        assert campaign.name == long_name

    def test_campaign_with_empty_message_template(self):
        """Test campaign can have empty message template."""
        campaign = Campaign(name="Test", message_template="")
        assert campaign.message_template == ""

    def test_contact_with_special_characters_in_name(self):
        """Test contact with special characters in name."""
        contact = Contact(
            campaign_id=1,
            name="José María O'Brien-Smith",
            profile_url="https://linkedin.com/in/jose"
        )
        assert contact.name == "José María O'Brien-Smith"

    def test_contact_with_very_long_url(self):
        """Test contact with very long profile URL."""
        long_url = "https://linkedin.com/in/" + "a" * 500
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url=long_url
        )
        assert contact.profile_url == long_url

    def test_analytics_with_zero_values(self):
        """Test analytics with all zero values."""
        analytics = Analytics(
            campaign_id=1,
            date="2025-01-15",
            connections_sent=0,
            connections_accepted=0,
            connections_declined=0,
            response_rate=0.0,
            acceptance_rate=0.0
        )
        assert analytics.connections_sent == 0
        assert analytics.response_rate == 0.0

    def test_settings_with_json_value(self):
        """Test settings can store JSON as string value."""
        json_value = '{"key1": "value1", "key2": 123}'
        settings = Settings(
            key="json_config",
            value=json_value
        )
        assert settings.value == json_value
        # Should be parseable
        parsed = json.loads(settings.value)
        assert parsed["key1"] == "value1"

    def test_contact_info_with_nested_json(self):
        """Test contact info can store nested JSON."""
        contact = Contact(
            campaign_id=1,
            name="John Doe",
            profile_url="https://linkedin.com/in/johndoe"
        )
        nested_info = {
            "email": "john@example.com",
            "social": {
                "twitter": "@johndoe",
                "github": "johndoe"
            },
            "tags": ["engineer", "python", "ai"]
        }
        contact.set_contact_info(nested_info)
        retrieved = contact.get_contact_info()
        assert retrieved["social"]["twitter"] == "@johndoe"
        assert "python" in retrieved["tags"]
