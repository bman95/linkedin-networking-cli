"""
Unit tests for database operations.

Tests DatabaseManager CRUD operations and business logic.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from database.models import Settings
from database.operations import DatabaseManager

# ============================================================================
# DatabaseManager Initialization Tests
# ============================================================================

@pytest.mark.unit
class TestDatabaseManagerInit:
    """Test DatabaseManager initialization."""

    def test_init_with_default_path(self, tmp_path, monkeypatch):
        """Test initialization with default database path."""
        # Run in a temp cwd so the default *relative* path creates the SQLite
        # file there instead of leaving a stray linkedin_networking.db at the
        # repo root (the Path assertion is cwd-independent).
        monkeypatch.chdir(tmp_path)
        db_manager = DatabaseManager()
        try:
            assert db_manager.db_path == Path("linkedin_networking.db")
            assert db_manager.engine is not None
        finally:
            db_manager.close()

    def test_init_with_custom_path(self, temp_db_path):
        """Test initialization with custom database path."""
        db_manager = DatabaseManager(str(temp_db_path))
        try:
            assert db_manager.db_path == temp_db_path
            assert db_manager.engine is not None
        finally:
            db_manager.close()

    def test_create_tables(self, temp_db_path):
        """Test that tables are created on initialization."""
        db_manager = DatabaseManager(str(temp_db_path))
        db_manager.close()
        # Verify database file was created
        assert temp_db_path.exists()

    def test_get_session(self, db_manager):
        """Test getting a database session."""
        session = db_manager.get_session()
        assert session is not None
        session.close()

    def test_close_disposes_pooled_connections(self, temp_db_path):
        """close() must dispose the engine, releasing pooled connections.

        Nothing else asserts this directly — close() is otherwise only
        exercised via fixture teardown, where a regression (an engine left
        undisposed) would surface only as a GC-time ResourceWarning that a
        plain test run silently swallows (issue #69).
        """
        import sqlite3

        db_manager = DatabaseManager(str(temp_db_path))
        # Hold a reference to a pooled DBAPI connection: dispose() replaces
        # the pool either way, so only the raw connection's state can prove
        # the old connections were CLOSED rather than merely dereferenced
        # (engine.dispose(close=False) would leave them for GC — the exact
        # leak this issue is about).
        raw = db_manager.engine.raw_connection()
        dbapi_conn = raw.dbapi_connection
        raw.close()  # return it to the pool, still open
        assert db_manager.engine.pool.checkedin() >= 1
        db_manager.close()
        assert db_manager.engine.pool.checkedin() == 0
        with pytest.raises(sqlite3.ProgrammingError):
            dbapi_conn.execute("SELECT 1")


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
            "connection_accepted_at": datetime.now(UTC),
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

    def test_count_contacts_by_statuses(self, db_manager):
        """A SQL-COUNT sibling of get_contacts_by_status, for callers that
        only need a size (issue #65) — one campaign's contacts across
        multiple statuses; another campaign's contacts are excluded."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        other_campaign = db_manager.create_campaign({"name": "Other Campaign"})
        for i, status in enumerate(("sent", "sent", "possibly_sent", "accepted")):
            db_manager.create_contact({
                "campaign_id": campaign.id,
                "name": f"Contact {status}",
                "profile_url": f"https://linkedin.com/in/{status}-{i}",
                "status": status,
            })
        db_manager.create_contact({
            "campaign_id": other_campaign.id,
            "name": "Other Campaign Contact",
            "profile_url": "https://linkedin.com/in/other",
            "status": "sent",
        })

        assert db_manager.count_contacts_by_statuses(
            campaign.id, ["sent", "possibly_sent"]
        ) == 3
        assert db_manager.count_contacts_by_statuses(campaign.id, ["accepted"]) == 1
        assert db_manager.count_contacts_by_statuses(campaign.id, ["declined"]) == 0

    def test_upsert_contact_creates_when_absent(self, db_manager):
        """upsert_contact creates a fresh row, like create_contact."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        contact = db_manager.upsert_contact({
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": "https://linkedin.com/in/johndoe",
            "status": "possibly_sent",
        })
        assert contact.id is not None
        assert contact.status == "possibly_sent"
        assert len(db_manager.get_contacts(campaign.id)) == 1

    def test_upsert_contact_updates_existing_no_duplicate(self, db_manager):
        """upsert_contact updates the existing row for the same profile."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        first = db_manager.upsert_contact({
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": url,
            "status": "possibly_sent",
            "notes": "pre-send marker",
        })
        second = db_manager.upsert_contact({
            "campaign_id": campaign.id,
            "name": "John Doe",
            "profile_url": url,
            "status": "sent",
            "notes": None,
        })
        # Same row reconciled, not duplicated.
        assert second.id == first.id
        assert len(db_manager.get_contacts(campaign.id)) == 1
        assert second.status == "sent"
        assert second.notes is None

    def test_upsert_contact_scoped_per_campaign(self, db_manager):
        """The same profile_url in two campaigns yields two distinct rows."""
        c1 = db_manager.create_campaign({"name": "C1"})
        c2 = db_manager.create_campaign({"name": "C2"})
        url = "https://linkedin.com/in/shared"
        db_manager.upsert_contact({
            "campaign_id": c1.id, "name": "Shared", "profile_url": url,
            "status": "sent",
        })
        db_manager.upsert_contact({
            "campaign_id": c2.id, "name": "Shared", "profile_url": url,
            "status": "possibly_sent",
        })
        assert len(db_manager.get_contacts(c1.id)) == 1
        assert len(db_manager.get_contacts(c2.id)) == 1

    def test_delete_contacts_by_profile(self, db_manager):
        """delete_contacts_by_profile removes the rows and returns the count."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "possibly_sent",
        })
        deleted = db_manager.delete_contacts_by_profile(campaign.id, url)
        assert deleted == 1
        assert db_manager.get_contacts(campaign.id) == []

    def test_delete_contacts_by_profile_absent_is_noop(self, db_manager):
        """Deleting a profile with no rows returns 0 and does not raise."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, "https://linkedin.com/in/nobody"
        )
        assert deleted == 0

    def test_delete_only_unfinalized_preserves_confirmed_send(self, db_manager):
        """only_unfinalized never deletes a confirmed send (concurrency guard)."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "sent",
            "connection_sent_at": datetime.now(UTC),
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, only_unfinalized=True
        )
        assert deleted == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "sent"

    def test_delete_only_unfinalized_clears_reservation_marker(self, db_manager):
        """only_unfinalized clears a ``reserved`` pre-send reservation marker."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        # Pre-send reservation marker (#39 retry): the dedicated reserved status.
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, only_unfinalized=True
        )
        assert deleted == 1
        assert db_manager.get_contacts(campaign.id) == []

    def test_delete_only_unfinalized_preserves_possibly_sent(
        self, db_manager
    ):
        """#39 finding 1: a possibly_sent row is NEVER deleted by a cleanup.

        With the dedicated ``reserved`` pre-send status, ``possibly_sent`` can
        only mean an ambiguous send whose invite may be out, so it is finalized
        unconditionally — a concurrent ``only_unfinalized`` cleanup must not
        erase the durable marker of an invite that already went out, even when
        ``connection_sent_at`` did not get stamped (a failed/partial reconcile).
        """
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        # No connection_sent_at: the post-send reconcile partially failed, yet
        # the invite may be out — this must still be protected.
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "possibly_sent",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, only_unfinalized=True
        )
        assert deleted == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "possibly_sent"

    def test_upsert_protect_finalized_does_not_downgrade_sent(self, db_manager):
        """protect_finalized leaves a confirmed send unchanged on downgrade."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "sent",
            "connection_sent_at": datetime.now(UTC),
        })
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "found", "notes": "downgrade attempt",
        }, protect_finalized=True)
        # The confirmed send wins; the downgrade is a no-op.
        assert result.status == "sent"
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "sent"

    def test_upsert_protect_finalized_still_updates_reservation(self, db_manager):
        """protect_finalized still updates a ``reserved`` reservation marker."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
        })
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "found",
        }, protect_finalized=True)
        assert result.status == "found"

    def test_upsert_protect_finalized_does_not_downgrade_possibly_sent(
        self, db_manager
    ):
        """#39 finding 1: protect_finalized leaves a possibly_sent untouched.

        A possibly_sent (invite may be out) must not be downgraded by a
        concurrent run's retryable cleanup, regardless of connection_sent_at.
        """
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "possibly_sent",  # no connection_sent_at
        })
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "found",
        }, protect_finalized=True)
        assert result.status == "possibly_sent"

    def test_promote_reserved_flips_marker_to_possibly_sent(self, db_manager):
        """#39: the fallback promotes a reserved marker to a finalized status."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
        })
        promoted = db_manager.promote_reserved_to_possibly_sent(campaign.id, url)
        assert promoted == 1
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1
        assert rows[0].status == "possibly_sent"
        assert rows[0].connection_sent_at is not None

    def test_promote_reserved_is_noop_when_already_finalized(self, db_manager):
        """A row already reconciled to possibly_sent/sent is left untouched."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "sent",
            "connection_sent_at": datetime.now(UTC),
        })
        promoted = db_manager.promote_reserved_to_possibly_sent(campaign.id, url)
        assert promoted == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "sent"

    def test_delete_reserved_only_clears_reservation(self, db_manager):
        """reserved_only deletes the temporary reserved marker."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, reserved_only=True
        )
        assert deleted == 1
        assert db_manager.get_contacts(campaign.id) == []

    def test_delete_reserved_only_preserves_found_skip_record(self, db_manager):
        """#39: reserved_only must NOT delete a concurrent run's found/failed row.

        The send-tail cleanup only intends to clear THIS run's reserved marker. A
        concurrent run can legitimately reconcile the shared row to a durable
        ``found`` skip record (email required / no Connect button); deleting it
        would make the next run retry an already-non-contactable profile.
        """
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "found",
            "notes": "Email required for connection",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, reserved_only=True
        )
        assert deleted == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "found"

    def test_delete_reserved_only_token_preserves_other_attempts_reservation(
        self, db_manager
    ):
        """#39 finding 1: a token-scoped cleanup never deletes another attempt's
        reservation (which that attempt may already have clicked Send on)."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-A",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, reserved_only=True, reservation_token="attempt-B"
        )
        assert deleted == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1
        assert rows[0].status == "reserved"
        assert rows[0].reservation_token == "attempt-A"

    def test_delete_reserved_only_token_deletes_own_reservation(self, db_manager):
        """A token-scoped cleanup deletes the reservation it owns."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-A",
        })
        deleted = db_manager.delete_contacts_by_profile(
            campaign.id, url, reserved_only=True, reservation_token="attempt-A"
        )
        assert deleted == 1
        assert db_manager.get_contacts(campaign.id) == []

    def test_downgrade_own_reservation_only_touches_own(self, db_manager):
        """#39 finding 2: the downgrade is scoped to the reservation we own."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-A",
        })
        # A foreign attempt's downgrade is a no-op (never clobbers A's live row).
        n = db_manager.downgrade_own_reservation_to_found(
            campaign.id, url, "attempt-B", notes="not mine"
        )
        assert n == 0
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "reserved"
        assert rows[0].reservation_token == "attempt-A"
        # The owning attempt's downgrade flips it to found and clears the token.
        n = db_manager.downgrade_own_reservation_to_found(
            campaign.id, url, "attempt-A", notes="clean send-failure"
        )
        assert n == 1
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1 and rows[0].status == "found"
        assert rows[0].reservation_token is None

    def test_upsert_protect_other_reservation_does_not_steal_foreign(
        self, db_manager
    ):
        """#39 finding 1/2: protect_other_reservation refuses to steal a foreign
        live reservation, but still claims an un-owned (legacy) one."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-A",
        })
        # Attempt B tries to reserve the same profile — A's row must stand.
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-B",
        }, protect_finalized=True, protect_other_reservation=True)
        assert result.reservation_token == "attempt-A"
        # An un-owned (null-token) reserved row IS claimable.
        db_manager.delete_contacts_by_profile(campaign.id, url)
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",  # no token (legacy)
        })
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-C",
        }, protect_finalized=True, protect_other_reservation=True)
        assert result.reservation_token == "attempt-C"

    def test_promote_reserved_with_token_scopes_to_owner(self, db_manager):
        """The promote fallback only flips the reservation it owns."""
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/johndoe"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "John Doe",
            "profile_url": url, "status": "reserved",
            "reservation_token": "attempt-A",
        })
        assert db_manager.promote_reserved_to_possibly_sent(
            campaign.id, url, reservation_token="attempt-B"
        ) == 0
        assert db_manager.get_contacts(campaign.id)[0].status == "reserved"
        assert db_manager.promote_reserved_to_possibly_sent(
            campaign.id, url, reservation_token="attempt-A"
        ) == 1
        assert db_manager.get_contacts(campaign.id)[0].status == "possibly_sent"


# ============================================================================
# Contact uniqueness + atomic upsert (issue #39 finding 2)
# ============================================================================

@pytest.mark.unit
class TestContactUniqueness:
    """``(campaign_id, profile_url)`` is unique and the upsert is atomic."""

    def test_unique_constraint_blocks_duplicate_insert(self, db_manager):
        """A second create for the same (campaign, profile) is rejected by the DB.

        With the UniqueConstraint, the non-atomic create_contact can no longer
        leave two rows for one profile — the second raises rather than inserting.
        """
        from sqlalchemy.exc import IntegrityError

        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/dup"
        db_manager.create_contact({
            "campaign_id": campaign.id, "name": "Dup", "profile_url": url,
            "status": "reserved",
        })
        with pytest.raises(IntegrityError):
            db_manager.create_contact({
                "campaign_id": campaign.id, "name": "Dup", "profile_url": url,
                "status": "found",
            })

    def test_upsert_is_single_row_under_repeated_calls(self, db_manager):
        """Repeated upserts for one profile keep exactly one canonical row.

        Models two overlapping runs both writing a marker then reconciling: the
        atomic INSERT ... ON CONFLICT DO UPDATE collapses onto one row.
        """
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/race"
        for status in ("reserved", "reserved", "possibly_sent", "sent"):
            db_manager.upsert_contact({
                "campaign_id": campaign.id, "name": "Race",
                "profile_url": url, "status": status,
            })
        rows = db_manager.get_contacts(campaign.id)
        assert len(rows) == 1
        assert rows[0].status == "sent"

    def test_upsert_protect_finalized_atomic_guard_blocks_downgrade(
        self, db_manager
    ):
        """The ON CONFLICT WHERE guard, not a pre-read, protects a finalized row.

        Exercises the atomic path: an existing possibly_sent is left intact by a
        protect_finalized upsert even though the conflict update is attempted.
        """
        campaign = db_manager.create_campaign({"name": "Test Campaign"})
        url = "https://linkedin.com/in/guard"
        db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "Guard",
            "profile_url": url, "status": "possibly_sent",
            "connection_sent_at": datetime.now(UTC),
        })
        result = db_manager.upsert_contact({
            "campaign_id": campaign.id, "name": "Guard",
            "profile_url": url, "status": "reserved",
        }, protect_finalized=True)
        assert result.status == "possibly_sent"
        assert len(db_manager.get_contacts(campaign.id)) == 1


# ============================================================================
# Idempotent contact de-dup migration (issue #39 finding 2)
# ============================================================================

@pytest.mark.unit
class TestContactDedupeMigration:
    """Existing duplicate contact rows are de-duped, then uniqueness applies."""

    @pytest.fixture
    def make_manager(self, temp_db_path):
        """Build DatabaseManagers over the temp DB, closing them all at teardown.

        These tests intentionally construct several managers over the same
        file (each run triggers the startup migration); disposing every engine
        afterwards keeps pooled sqlite3 connections from leaking past the test
        (ResourceWarning at pytest teardown).
        """
        managers = []

        def _make():
            manager = DatabaseManager(str(temp_db_path))
            managers.append(manager)
            return manager

        yield _make
        for manager in managers:
            manager.close()

    def _seed_duplicates(self, db_path, campaign_id, url, statuses):
        """Reconstruct a legacy DB state: a contact table WITHOUT uniqueness.

        Existing user DBs predate the UniqueConstraint, so their contact table
        has no (campaign_id, profile_url) uniqueness and can hold duplicates.
        The model's create_all builds the table WITH an inline UNIQUE constraint
        (SQLite's un-droppable sqlite_autoindex), so to reproduce the legacy
        shape we drop and recreate the table without the constraint, then insert
        one duplicate row per status.
        """
        from sqlalchemy import create_engine, text

        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE contact"))
            # Legacy schema: same columns, NO unique constraint/index.
            conn.execute(
                text(
                    "CREATE TABLE contact ("
                    " id INTEGER PRIMARY KEY,"
                    " campaign_id INTEGER NOT NULL,"
                    " name VARCHAR NOT NULL,"
                    " profile_url VARCHAR NOT NULL,"
                    " headline VARCHAR, location VARCHAR, company VARCHAR,"
                    " status VARCHAR NOT NULL,"
                    " connection_sent_at DATETIME,"
                    " connection_accepted_at DATETIME,"
                    " notes VARCHAR,"
                    " contact_info VARCHAR NOT NULL,"
                    " created_at DATETIME NOT NULL,"
                    " updated_at DATETIME"
                    ")"
                )
            )
            for status in statuses:
                conn.execute(
                    text(
                        "INSERT INTO contact "
                        "(campaign_id, name, profile_url, status, "
                        " contact_info, created_at) "
                        "VALUES (:cid, :name, :url, :status, '{}', :now)"
                    ),
                    {
                        "cid": campaign_id, "name": "Dup", "url": url,
                        "status": status, "now": datetime.now().isoformat(),
                    },
                )
        engine.dispose()

    def test_migration_dedupes_existing_duplicates(self, temp_db_path, make_manager):
        """A DB carrying duplicate rows is collapsed to one canonical row.

        The keeper is the safest skip-key: a finalized outcome (an invite that
        is or may be out) wins over a pre-send reservation, so the surviving row
        is the one that prevents re-contact.
        """
        manager = make_manager()
        campaign = manager.create_campaign({"name": "Migrate"})
        url = "https://linkedin.com/in/dup"
        # Reconstruct a legacy state: three rows for one profile, the middle a
        # confirmed possibly_sent (invite may be out).
        self._seed_duplicates(
            temp_db_path, campaign.id, url,
            ["reserved", "possibly_sent", "found"],
        )

        # A fresh manager over the same file runs the startup migration.
        migrated = make_manager()
        # The additive migration added the reservation_token column to the
        # legacy table (the dedupe's ORM read would otherwise fail).
        from sqlalchemy import inspect as sa_inspect
        cols = {c["name"] for c in sa_inspect(migrated.engine).get_columns("contact")}
        assert "reservation_token" in cols
        rows = migrated.get_contacts(campaign.id)
        assert len(rows) == 1
        # The finalized row (the invite-may-be-out marker) is the keeper.
        assert rows[0].status == "possibly_sent"

    def test_migration_keeps_highest_id_when_no_finalized(
        self, temp_db_path, make_manager
    ):
        """With no finalized row, the most recent (highest id) write wins."""
        manager = make_manager()
        campaign = manager.create_campaign({"name": "Migrate"})
        url = "https://linkedin.com/in/dup"
        self._seed_duplicates(
            temp_db_path, campaign.id, url, ["found", "reserved"],
        )
        migrated = make_manager()
        rows = migrated.get_contacts(campaign.id)
        assert len(rows) == 1
        # Both clobberable; the later insert (reserved, higher id) is kept.
        assert rows[0].status == "reserved"

    def test_migration_preserves_terminal_status_over_later_send(
        self, temp_db_path, make_manager
    ):
        """#39: a terminal accepted/declined wins over a later but weaker row.

        Legacy duplicates may hold an ``accepted`` row followed by a stale
        ``sent``/``pending`` (higher id). The keeper must be the terminal outcome
        by status precedence, not merely the most recent write — otherwise the
        migration would regress an accepted contact back to pending and skew
        stats.
        """
        manager = make_manager()
        campaign = manager.create_campaign({"name": "Migrate"})
        url = "https://linkedin.com/in/dup"
        # accepted first (lower id), then a stale sent + pending (higher ids).
        self._seed_duplicates(
            temp_db_path, campaign.id, url, ["accepted", "sent", "pending"],
        )
        migrated = make_manager()
        rows = migrated.get_contacts(campaign.id)
        assert len(rows) == 1
        assert rows[0].status == "accepted"

    def test_migration_applies_unique_index(self, temp_db_path, make_manager):
        """After migration the unique index rejects a fresh duplicate insert."""
        from sqlalchemy.exc import IntegrityError

        manager = make_manager()
        campaign = manager.create_campaign({"name": "Migrate"})
        url = "https://linkedin.com/in/dup"
        self._seed_duplicates(
            temp_db_path, campaign.id, url, ["reserved", "reserved"],
        )
        migrated = make_manager()
        # The de-duped DB now enforces uniqueness on new writes.
        with pytest.raises(IntegrityError):
            migrated.create_contact({
                "campaign_id": campaign.id, "name": "Dup",
                "profile_url": url, "status": "found",
            })

    def test_migration_is_idempotent_on_rerun(self, temp_db_path, make_manager):
        """Re-running the migration on an already-clean DB changes nothing."""
        manager = make_manager()
        campaign = manager.create_campaign({"name": "Migrate"})
        url = "https://linkedin.com/in/dup"
        self._seed_duplicates(
            temp_db_path, campaign.id, url, ["reserved", "possibly_sent"],
        )
        # First migration de-dupes.
        make_manager()
        # Second migration over the now-clean DB is a no-op (no raise, one row).
        again = make_manager()
        rows = again.get_contacts(campaign.id)
        assert len(rows) == 1
        assert rows[0].status == "possibly_sent"
        # And the dedupe helper itself reports zero deletions on a clean DB.
        assert again._dedupe_contacts_before_unique_index() == 0


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


# ============================================================================
# Daily Connection Count (Persisted Rate-Limiting) Tests
# ============================================================================

@pytest.mark.unit
class TestDailyConnectionCount:
    """Test persisted per-local-day connection counter operations."""

    def test_get_count_absent_day_is_zero(self, db_manager):
        """An untouched day starts at zero (self-clearing on date rollover)."""
        assert db_manager.get_daily_connection_count("2025-01-15") == 0

    def test_increment_creates_row(self, db_manager):
        """Incrementing an absent day creates a row at count 1."""
        new_count = db_manager.increment_daily_connection_count("2025-01-15")
        assert new_count == 1
        assert db_manager.get_daily_connection_count("2025-01-15") == 1

    def test_increment_accumulates(self, db_manager):
        """Repeated increments accumulate within the same day."""
        for expected in range(1, 6):
            assert (
                db_manager.increment_daily_connection_count("2025-01-15") == expected
            )
        assert db_manager.get_daily_connection_count("2025-01-15") == 5

    def test_counts_are_independent_per_day(self, db_manager):
        """Each local day keeps its own count; a new day starts at zero."""
        db_manager.increment_daily_connection_count("2025-01-15")
        db_manager.increment_daily_connection_count("2025-01-15")
        db_manager.increment_daily_connection_count("2025-01-16")

        assert db_manager.get_daily_connection_count("2025-01-15") == 2
        assert db_manager.get_daily_connection_count("2025-01-16") == 1
        assert db_manager.get_daily_connection_count("2025-01-17") == 0

    def test_increment_records_last_action_at(self, db_manager):
        """Incrementing records a timezone-aware last-action timestamp."""
        before = datetime.now(UTC)
        db_manager.increment_daily_connection_count("2025-01-15")
        after = datetime.now(UTC)

        last = db_manager.get_last_connection_at()
        assert last is not None
        # SQLite drops tzinfo on round-trip; compare as naive UTC.
        last_naive = last.replace(tzinfo=None) if last.tzinfo else last
        assert before.replace(tzinfo=None) <= last_naive <= after.replace(tzinfo=None)

    def test_get_last_connection_at_none_when_empty(self, db_manager):
        """No recorded connections yields no last-action timestamp."""
        assert db_manager.get_last_connection_at() is None

    def test_get_last_connection_at_returns_most_recent(self, db_manager):
        """The latest timestamp across all days is returned."""
        db_manager.increment_daily_connection_count("2025-01-15")
        db_manager.increment_daily_connection_count("2025-01-16")
        last = db_manager.get_last_connection_at()
        assert last is not None

    def test_reserve_slot_on_fresh_day(self, db_manager):
        """Reserving on an empty day claims slot 1."""
        assert db_manager.reserve_daily_slot("2025-01-15", 20) == 1
        assert db_manager.get_daily_connection_count("2025-01-15") == 1

    def test_reserve_slot_accumulates_up_to_limit(self, db_manager):
        """Reservations accumulate and the final slot is claimable."""
        for expected in range(1, 21):
            assert db_manager.reserve_daily_slot("2025-01-15", 20) == expected
        assert db_manager.get_daily_connection_count("2025-01-15") == 20

    def test_reserve_slot_refused_when_full(self, db_manager):
        """Once at the limit, reservation is refused and the count is stable."""
        for _ in range(20):
            db_manager.reserve_daily_slot("2025-01-15", 20)
        # Day is full: the next reservation must be refused, not over-count.
        assert db_manager.reserve_daily_slot("2025-01-15", 20) is None
        assert db_manager.get_daily_connection_count("2025-01-15") == 20

    def test_reserve_final_slot_is_not_confused_with_full(self, db_manager):
        """Claiming the last slot (count==limit) returns the count, not None."""
        for _ in range(19):
            db_manager.reserve_daily_slot("2025-01-15", 20)
        # 19/20 -> claiming the 20th must succeed and return 20 (not None).
        assert db_manager.reserve_daily_slot("2025-01-15", 20) == 20
        # 20/20 -> now refused.
        assert db_manager.reserve_daily_slot("2025-01-15", 20) is None

    def test_reserve_slot_zero_limit_refused(self, db_manager):
        """A non-positive limit never grants a slot."""
        assert db_manager.reserve_daily_slot("2025-01-15", 0) is None
        assert db_manager.get_daily_connection_count("2025-01-15") == 0

    def test_release_slot_decrements(self, db_manager):
        """Releasing gives a reserved slot back."""
        db_manager.reserve_daily_slot("2025-01-15", 20)
        db_manager.reserve_daily_slot("2025-01-15", 20)
        db_manager.release_daily_slot("2025-01-15")
        assert db_manager.get_daily_connection_count("2025-01-15") == 1

    def test_release_slot_never_below_zero(self, db_manager):
        """Releasing an empty day never produces a negative count."""
        db_manager.release_daily_slot("2025-01-15")
        assert db_manager.get_daily_connection_count("2025-01-15") == 0

    def test_reserve_then_release_frees_capacity(self, db_manager):
        """A released slot can be reserved again (full -> release -> claim)."""
        for _ in range(20):
            db_manager.reserve_daily_slot("2025-01-15", 20)
        assert db_manager.reserve_daily_slot("2025-01-15", 20) is None
        db_manager.release_daily_slot("2025-01-15")  # back to 19/20
        assert db_manager.reserve_daily_slot("2025-01-15", 20) == 20

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
# Weekly Connection Count Tests (proactive weekly-invitation budget)
# ============================================================================

@pytest.mark.unit
class TestWeeklyConnectionCount:
    """Test the trailing-7-day connection sum used for the weekly budget."""

    def test_absent_week_is_zero(self, db_manager):
        """No recorded days sums to zero."""
        assert db_manager.get_weekly_connection_count(reference_date=date(2025, 1, 15)) == 0

    def test_sums_trailing_seven_days(self, db_manager):
        """The reference day and the previous 6 days are all included."""
        ref = date(2025, 1, 15)
        for n in range(7):  # today .. 6 days ago, 1 each
            db_manager.increment_daily_connection_count(
                (ref - timedelta(days=n)).isoformat()
            )
        assert db_manager.get_weekly_connection_count(reference_date=ref) == 7

    def test_excludes_days_older_than_seven(self, db_manager):
        """A day 7+ days before the reference date is outside the window."""
        ref = date(2025, 1, 15)
        # Within window: today and 6 days ago.
        db_manager.increment_daily_connection_count(ref.isoformat())
        db_manager.increment_daily_connection_count(
            (ref - timedelta(days=6)).isoformat()
        )
        # Just outside the window (7 days before): must be excluded.
        for _ in range(5):
            db_manager.increment_daily_connection_count(
                (ref - timedelta(days=7)).isoformat()
            )
        assert db_manager.get_weekly_connection_count(reference_date=ref) == 2

    def test_sums_varying_daily_counts(self, db_manager):
        """Per-day counts (not just 1 each) are summed correctly."""
        ref = date(2025, 1, 15)
        for _ in range(3):
            db_manager.increment_daily_connection_count(ref.isoformat())
        for _ in range(4):
            db_manager.increment_daily_connection_count(
                (ref - timedelta(days=2)).isoformat()
            )
        assert db_manager.get_weekly_connection_count(reference_date=ref) == 7

    def test_default_reference_date_is_today(self, db_manager):
        """Called with no argument, it buckets by the local ``date.today()``."""
        today = date.today()
        db_manager.increment_daily_connection_count(today.isoformat())
        db_manager.increment_daily_connection_count(
            (today - timedelta(days=3)).isoformat()
        )
        assert db_manager.get_weekly_connection_count() == 2


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

    def test_update_campaign_stats_counts_possibly_sent(self, db_manager):
        """possibly_sent counts as sent AND pending (issue #31).

        An assumed-sent invite consumed a daily slot, so it must not under-report
        the campaign totals or skew the acceptance rate.
        """
        campaign = db_manager.create_campaign({"name": "Possibly Sent"})
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Confirmed",
            "profile_url": "https://linkedin.com/in/confirmed",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Ambiguous",
            "profile_url": "https://linkedin.com/in/ambiguous",
            "status": "possibly_sent",
        })

        db_manager.update_campaign_stats(campaign.id)

        updated = db_manager.get_campaign(campaign.id)
        assert updated.total_sent == 2       # sent + possibly_sent
        assert updated.total_pending == 2    # both await acceptance
        assert updated.total_accepted == 0

    def test_update_campaign_stats_excludes_reserved(self, db_manager):
        """#39: a ``reserved`` pre-send marker does NOT count as a sent invite.

        It is only a skip-key (no invite is known out), so it must be excluded
        from both sent and pending totals — otherwise a reservation that never
        sent would inflate the campaign stats.
        """
        campaign = db_manager.create_campaign({"name": "Reserved"})
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Confirmed",
            "profile_url": "https://linkedin.com/in/confirmed",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Reserved",
            "profile_url": "https://linkedin.com/in/reserved",
            "status": "reserved",
        })

        db_manager.update_campaign_stats(campaign.id)

        updated = db_manager.get_campaign(campaign.id)
        assert updated.total_sent == 1       # only the confirmed send
        assert updated.total_pending == 1    # reserved excluded

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

    def test_get_dashboard_stats_counts_possibly_sent(self, db_manager):
        """Dashboard totals include possibly_sent as sent+pending (issue #31)."""
        campaign = db_manager.create_campaign({"name": "Campaign"})
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Confirmed",
            "profile_url": "https://linkedin.com/in/confirmed",
            "status": "sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Ambiguous",
            "profile_url": "https://linkedin.com/in/ambiguous",
            "status": "possibly_sent",
        })
        db_manager.create_contact({
            "campaign_id": campaign.id,
            "name": "Accepted",
            "profile_url": "https://linkedin.com/in/accepted",
            "status": "accepted",
        })

        stats = db_manager.get_dashboard_stats()

        assert stats["total_sent"] == 3       # sent + possibly_sent + accepted
        assert stats["total_pending"] == 2    # sent + possibly_sent
        assert stats["total_accepted"] == 1

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
