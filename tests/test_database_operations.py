"""
Unit tests for database operations.

Tests DatabaseManager CRUD operations and business logic.
"""

import pytest
from datetime import datetime, timezone
from pathlib import Path

from database.operations import DatabaseManager
from database.models import Campaign, Contact, Analytics, Settings


# ============================================================================
# DatabaseManager Initialization Tests
# ============================================================================

@pytest.mark.unit
class TestDatabaseManagerInit:
    """Test DatabaseManager initialization."""

    def test_init_with_default_path(self):
        """Test initialization with default database path."""
        db_manager = DatabaseManager()
        assert db_manager.db_path == Path("linkedin_networking.db")
        assert db_manager.engine is not None

    def test_init_with_custom_path(self, temp_db_path):
        """Test initialization with custom database path."""
        db_manager = DatabaseManager(str(temp_db_path))
        assert db_manager.db_path == temp_db_path
        assert db_manager.engine is not None

    def test_create_tables(self, temp_db_path):
        """Test that tables are created on initialization."""
        db_manager = DatabaseManager(str(temp_db_path))
        # Verify database file was created
        assert temp_db_path.exists()

    def test_get_session(self, db_manager):
        """Test getting a database session."""
        session = db_manager.get_session()
        assert session is not None
        session.close()


# ============================================================================
# Campaign Operations Tests
# ============================================================================

@pytest.mark.unit
class TestCampaignOperations:
    """Test Campaign CRUD operations."""

    def test_create_campaign(self, db_manager):
        """Test creating a campaign."""
        campaign_data = {
            "name": "Test Campaign",
            "keywords": "software engineer",
            "geo_urn": "90000084",
            "daily_limit": 10,
        }
        campaign = db_manager.create_campaign(campaign_data)

        assert campaign.id is not None
        assert campaign.name == "Test Campaign"
        assert campaign.keywords == "software engineer"
        assert campaign.geo_urn == "90000084"
        assert campaign.daily_limit == 10

    def test_create_campaign_with_minimal_data(self, db_manager):
        """Test creating campaign with minimal required data."""
        campaign_data = {"name": "Minimal Campaign"}
        campaign = db_manager.create_campaign(campaign_data)

        assert campaign.id is not None
        assert campaign.name == "Minimal Campaign"
        assert campaign.active is True  # Default value

    def test_get_campaigns(self, db_manager):
        """Test retrieving all campaigns."""
        # Create multiple campaigns
        db_manager.create_campaign({"name": "Campaign 1"})
        db_manager.create_campaign({"name": "Campaign 2"})
        db_manager.create_campaign({"name": "Campaign 3", "active": False})

        # Get active campaigns only
        campaigns = db_manager.get_campaigns(active_only=True)
        assert len(campaigns) == 2
        assert all(c.active for c in campaigns)

        # Get all campaigns
        all_campaigns = db_manager.get_campaigns(active_only=False)
        assert len(all_campaigns) == 3

    def test_get_campaign_by_id(self, db_manager):
        """Test retrieving a campaign by ID."""
        created = db_manager.create_campaign({"name": "Test Campaign"})

        campaign = db_manager.get_campaign(created.id)
        assert campaign is not None
        assert campaign.id == created.id
        assert campaign.name == "Test Campaign"

    def test_get_campaign_by_nonexistent_id(self, db_manager):
        """Test retrieving campaign with non-existent ID."""
        campaign = db_manager.get_campaign(99999)
        assert campaign is None

    def test_update_campaign(self, db_manager):
        """Test updating a campaign."""
        created = db_manager.create_campaign({"name": "Original Name"})

        updates = {
            "name": "Updated Name",
            "keywords": "new keywords",
            "daily_limit": 30,
        }
        updated = db_manager.update_campaign(created.id, updates)

        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.keywords == "new keywords"
        assert updated.daily_limit == 30
        assert updated.updated_at is not None

    def test_update_nonexistent_campaign(self, db_manager):
        """Test updating a non-existent campaign."""
        result = db_manager.update_campaign(99999, {"name": "Updated"})
        assert result is None

    def test_delete_campaign(self, db_manager):
        """Test deleting a campaign."""
        created = db_manager.create_campaign({"name": "To Delete"})
        campaign_id = created.id

        # Delete campaign
        result = db_manager.delete_campaign(campaign_id)
        assert result is True

        # Verify it's deleted
        campaign = db_manager.get_campaign(campaign_id)
        assert campaign is None

    def test_delete_campaign_with_contacts(self, db_manager):
        """Test that deleting a campaign also deletes its contacts."""
        # Create campaign
        campaign = db_manager.create_campaign({"name": "Campaign with Contacts"})

        # Create contacts for the campaign
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 1",
            "profile_url": "https://linkedin.com/in/contact1",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 2",
            "profile_url": "https://linkedin.com/in/contact2",
        })

        # Delete campaign
        db_manager.delete_campaign(campaign.id)

        # Verify contacts are deleted
        contacts = db_manager.get_contacts(campaign_id=campaign.id)
        assert len(contacts) == 0

    def test_delete_nonexistent_campaign(self, db_manager):
        """Test deleting a non-existent campaign."""
        result = db_manager.delete_campaign(99999)
        assert result is False


# ============================================================================
# Contact Operations Tests
# ============================================================================

@pytest.mark.unit
class TestContactOperations:
    """Test Contact CRUD operations."""

    def test_create_contact(self, db_manager):
        """Test creating a contact."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        contact_data = {
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": "https://linkedin.com/in/johndoe",
            "headline": "Software Engineer",
            "status": "sent",
        }
        contact = db_manager.create_contact(contact_data)

        assert contact.id is not None
        assert contact.name == "John Doe"
        assert contact.profile_url == "https://linkedin.com/in/johndoe"
        assert contact.status == "sent"

    def test_get_contacts(self, db_manager):
        """Test retrieving all contacts."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create multiple contacts
        for i in range(3):
            db_manager.create_contact({
                "campaign_id": campaign.id,
                "name": f"Contact {i}",
                "profile_url": f"https://linkedin.com/in/contact{i}",
            })

        contacts = db_manager.get_contacts()
        assert len(contacts) == 3

    def test_get_contacts_by_campaign(self, db_manager):
        """Test retrieving contacts for a specific campaign."""
        campaign1 = db_manager.create_campaign({"name": "Campaign 1"})
        campaign2 = db_manager.create_campaign({"name": "Campaign 2"})

        # Create contacts for campaign 1
        db_manager.create_contact({
            "campaign_id": campaign1.id,
            "name": "Contact 1",
            "profile_url": "https://linkedin.com/in/contact1",
        })
        db_manager.create_contact({
            "campaign_id": campaign1.id,
            "name": "Contact 2",
            "profile_url": "https://linkedin.com/in/contact2",
        })

        # Create contact for campaign 2
        db_manager.create_contact({
            "campaign_id": campaign2.id,
            "name": "Contact 3",
            "profile_url": "https://linkedin.com/in/contact3",
        })

        # Get contacts for campaign 1
        contacts = db_manager.get_contacts(campaign_id=campaign1.id)
        assert len(contacts) == 2
        assert all(c.campaign_id == campaign1.id for c in contacts)

    def test_get_contact_by_id(self, db_manager):
        """Test retrieving a contact by ID."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        created = db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": "https://linkedin.com/in/johndoe",
        })

        contact = db_manager.get_contact(created.id)
        assert contact is not None
        assert contact.id == created.id
        assert contact.name == "John Doe"

    def test_get_contact_by_nonexistent_id(self, db_manager):
        """Test retrieving contact with non-existent ID."""
        contact = db_manager.get_contact(99999)
        assert contact is None

    def test_update_contact(self, db_manager):
        """Test updating a contact."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        created = db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": "https://linkedin.com/in/johndoe",
            "status": "sent",
        })

        updates = {
            "status": "accepted",
            "connection_accepted_at": datetime.now(timezone.utc),
        }
        updated = db_manager.update_contact(created.id, updates)

        assert updated is not None
        assert updated.status == "accepted"
        assert updated.connection_accepted_at is not None
        assert updated.updated_at is not None

    def test_update_nonexistent_contact(self, db_manager):
        """Test updating a non-existent contact."""
        result = db_manager.update_contact(99999, {"status": "accepted"})
        assert result is None

    def test_get_contacts_by_status(self, db_manager):
        """Test retrieving contacts by status."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create contacts with different statuses
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 1",
            "profile_url": "https://linkedin.com/in/contact1",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 2",
            "profile_url": "https://linkedin.com/in/contact2",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 3",
            "profile_url": "https://linkedin.com/in/contact3",
            "status": "accepted",
        })

        # Get sent contacts
        sent_contacts = db_manager.get_contacts_by_status(campaign.id, "sent")
        assert len(sent_contacts) == 2
        assert all(c.status == "sent" for c in sent_contacts)

        # Get accepted contacts
        accepted_contacts = db_manager.get_contacts_by_status(campaign.id, "accepted")
        assert len(accepted_contacts) == 1
        assert accepted_contacts[0].status == "accepted"


# ============================================================================
# Analytics Operations Tests
# ============================================================================

@pytest.mark.unit
class TestAnalyticsOperations:
    """Test Analytics operations."""

    def test_record_daily_analytics_new(self, db_manager):
        """Test recording new daily analytics."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        metrics = {
            "connections_sent": 10,
            "connections_accepted": 3,
            "response_rate": 30.0,
        }
        analytics = db_manager.record_daily_analytics(
            campaign.id,
            "2025-01-15",
            metrics
        )

        assert analytics.id is not None
        assert analytics.campaign_id == campaign.id
        assert analytics.date == "2025-01-15"
        assert analytics.connections_sent == 10
        assert analytics.connections_accepted == 3
        assert analytics.response_rate == 30.0

    def test_record_daily_analytics_update_existing(self, db_manager):
        """Test updating existing daily analytics."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create initial analytics
        db_manager.record_daily_analytics(
            campaign.id,
            "2025-01-15",
            {"connections_sent": 10}
        )

        # Update with new metrics
        updated = db_manager.record_daily_analytics(
            campaign.id,
            "2025-01-15",
            {"connections_sent": 15, "connections_accepted": 5}
        )

        assert updated.connections_sent == 15
        assert updated.connections_accepted == 5
        assert updated.updated_at is not None

    def test_get_campaign_analytics(self, db_manager):
        """Test retrieving campaign analytics."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create analytics for multiple days
        for day in range(1, 8):
            db_manager.record_daily_analytics(
                campaign.id,
                f"2025-01-{day:02d}",
                {"connections_sent": day * 10}
            )

        # Get last 30 days
        analytics = db_manager.get_campaign_analytics(campaign.id, days=30)
        assert len(analytics) == 7

        # Get last 3 days
        analytics = db_manager.get_campaign_analytics(campaign.id, days=3)
        assert len(analytics) == 3

    def test_get_campaign_analytics_empty(self, db_manager):
        """Test getting analytics for campaign with no data."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        analytics = db_manager.get_campaign_analytics(campaign.id)
        assert len(analytics) == 0


# ============================================================================
# Settings Operations Tests
# ============================================================================

@pytest.mark.unit
class TestSettingsOperations:
    """Test Settings operations."""

    def test_set_setting_new(self, db_manager):
        """Test setting a new setting."""
        db_manager.set_setting("daily_limit", 20, "Daily connection limit")

        value = db_manager.get_setting("daily_limit")
        assert value == 20

    def test_set_setting_update_existing(self, db_manager):
        """Test updating an existing setting."""
        db_manager.set_setting("daily_limit", 20)
        db_manager.set_setting("daily_limit", 30)

        value = db_manager.get_setting("daily_limit")
        assert value == 30

    def test_get_setting_default(self, db_manager):
        """Test getting setting with default value."""
        value = db_manager.get_setting("nonexistent_key", default="default_value")
        assert value == "default_value"

    def test_set_setting_with_string(self, db_manager):
        """Test setting with string value."""
        db_manager.set_setting("username", "john_doe")
        value = db_manager.get_setting("username")
        assert value == "john_doe"

    def test_set_setting_with_dict(self, db_manager):
        """Test setting with dict value (stored as JSON)."""
        config = {"key1": "value1", "key2": 123}
        db_manager.set_setting("config", config)

        value = db_manager.get_setting("config")
        assert isinstance(value, dict)
        assert value["key1"] == "value1"
        assert value["key2"] == 123

    def test_set_setting_with_list(self, db_manager):
        """Test setting with list value (stored as JSON)."""
        tags = ["tag1", "tag2", "tag3"]
        db_manager.set_setting("tags", tags)

        value = db_manager.get_setting("tags")
        assert isinstance(value, list)
        assert len(value) == 3
        assert "tag2" in value

    def test_set_setting_with_description(self, db_manager):
        """Test that description is stored."""
        db_manager.set_setting("test_key", "test_value", "Test description")

        # Verify description was stored
        with db_manager.get_session() as session:
            from sqlmodel import select
            setting = session.exec(
                select(Settings).where(Settings.key == "test_key")
            ).first()
            assert setting.description == "Test description"


# ============================================================================
# Campaign Statistics Tests
# ============================================================================

@pytest.mark.unit
class TestCampaignStatistics:
    """Test campaign statistics calculations."""

    def test_update_campaign_stats(self, db_manager):
        """Test updating campaign statistics."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create contacts with different statuses
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 1",
            "profile_url": "https://linkedin.com/in/contact1",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 2",
            "profile_url": "https://linkedin.com/in/contact2",
            "status": "accepted",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 3",
            "profile_url": "https://linkedin.com/in/contact3",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Contact 4",
            "profile_url": "https://linkedin.com/in/contact4",
            "status": "found",  # Not sent
        })

        # Update stats
        db_manager.update_campaign_stats(campaign.id)

        # Verify stats
        updated_campaign = db_manager.get_campaign(campaign.id)
        assert updated_campaign.total_sent == 3  # sent + accepted
        assert updated_campaign.total_accepted == 1
        assert updated_campaign.total_pending == 2  # sent only

    def test_update_campaign_stats_nonexistent_campaign(self, db_manager):
        """Test updating stats for non-existent campaign."""
        # Should not raise an error
        db_manager.update_campaign_stats(99999)


# ============================================================================
# Dashboard Statistics Tests
# ============================================================================

@pytest.mark.unit
class TestDashboardStatistics:
    """Test dashboard statistics."""

    def test_get_dashboard_stats_empty(self, db_manager):
        """Test dashboard stats with no data."""
        stats = db_manager.get_dashboard_stats()

        assert stats["total_campaigns"] == 0
        assert stats["active_campaigns"] == 0
        assert stats["total_contacts"] == 0
        assert stats["total_sent"] == 0
        assert stats["total_accepted"] == 0
        assert stats["total_pending"] == 0
        assert stats["acceptance_rate"] == 0

    def test_get_dashboard_stats_with_data(self, db_manager):
        """Test dashboard stats with data."""
        # Create campaigns
        campaign1 = db_manager.create_campaign({"name": "Campaign 1"})
        campaign2 = db_manager.create_campaign({"name": "Campaign 2", "active": False})

        # Create contacts
        db_manager.create_contact({
            "campaign_id": campaign1.id,
            "name": "Contact 1",
            "profile_url": "https://linkedin.com/in/contact1",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign1.id,
            "name": "Contact 2",
            "profile_url": "https://linkedin.com/in/contact2",
            "status": "accepted",
        })
        db_manager.create_contact({
            "campaign_id": campaign1.id,
            "name": "Contact 3",
            "profile_url": "https://linkedin.com/in/contact3",
            "status": "found",
        })

        stats = db_manager.get_dashboard_stats()

        assert stats["total_campaigns"] == 2
        assert stats["active_campaigns"] == 1
        assert stats["total_contacts"] == 3
        assert stats["total_sent"] == 2
        assert stats["total_accepted"] == 1
        assert stats["total_pending"] == 1
        assert stats["acceptance_rate"] == 50.0

    def test_get_dashboard_stats_acceptance_rate_calculation(self, db_manager):
        """Test acceptance rate calculation."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})

        # Create 10 sent, 3 accepted
        for i in range(10):
            status = "accepted" if i < 3 else "sent"
            db_manager.create_contact({
                "campaign_id": campaign.id,
                "name": f"Contact {i}",
                "profile_url": f"https://linkedin.com/in/contact{i}",
                "status": status,
            })

        stats = db_manager.get_dashboard_stats()
        assert stats["acceptance_rate"] == 30.0  # 3/10 * 100


# ============================================================================
# Integration and Edge Cases
# ============================================================================

@pytest.mark.unit
class TestDatabaseEdgeCases:
    """Test edge cases and error handling."""

    def test_campaign_with_special_characters(self, db_manager):
        """Test campaign with special characters in fields."""
        campaign_data = {
            "name": "Campaign with 'quotes' and \"double quotes\"",
            "keywords": "engineer's role, O'Brien",
        }
        campaign = db_manager.create_campaign(campaign_data)
        assert campaign.id is not None

    def test_contact_with_unicode(self, db_manager):
        """Test contact with unicode characters."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        contact_data = {
            "campaign_id": campaign.id,
            "name": "José María 李明 Müller",
            "profile_url": "https://linkedin.com/in/jose",
        }
        contact = db_manager.create_contact(contact_data)
        assert contact.name == "José María 李明 Müller"

    def test_multiple_sessions_concurrently(self, db_manager):
        """Test that multiple sessions can be created."""
        session1 = db_manager.get_session()
        session2 = db_manager.get_session()

        assert session1 is not session2

        session1.close()
        session2.close()

    def test_empty_update_dict(self, db_manager):
        """Test updating with empty dict."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        original_name = campaign.name

        updated = db_manager.update_campaign(campaign.id, {})
        assert updated.name == original_name
        assert updated.updated_at is not None  # Still updates timestamp
