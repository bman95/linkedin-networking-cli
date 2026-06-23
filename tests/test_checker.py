"""
Tests for connection-status checking (src/automation/checker.py).
"""

import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.checker import (
    _clean_profile_url,
    _get_connection_limit,
    smart_connection_checker,
    check_specific_contacts,
    monitor_pending_connections,
)


def _automation(authenticated=True, pending=None, contact=None, is_connected=True):
    automation = MagicMock()
    automation.is_authenticated = authenticated

    page = AsyncMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.content = AsyncMock(return_value="")
    page.is_visible = AsyncMock(return_value=is_connected)
    page.keyboard = AsyncMock()
    automation.page = page
    automation.context = AsyncMock()

    db = MagicMock()
    db.get_contacts_by_status.return_value = pending or []
    db.get_contact.return_value = contact
    db.update_contact = MagicMock()
    automation.db_manager = db
    return automation


@pytest.mark.unit
class TestCleanProfileUrl:
    def test_strips_query_and_fragment(self):
        assert _clean_profile_url(
            "https://www.linkedin.com/in/jane/?foo=1#bar"
        ) == "https://www.linkedin.com/in/jane/"

    def test_adds_trailing_slash(self):
        assert _clean_profile_url("https://www.linkedin.com/in/jane").endswith("/")

    def test_expands_relative_path(self):
        assert _clean_profile_url("/in/jane/") == "https://www.linkedin.com/in/jane/"

    def test_empty(self):
        assert _clean_profile_url("") == ""


@pytest.mark.unit
class TestConnectionLimit:
    def test_returns_recent_accepted(self):
        db = MagicMock()
        session = MagicMock()
        session.exec.return_value.first.return_value = SimpleNamespace(name="Jane")
        db.get_session.return_value.__enter__.return_value = session
        result = _get_connection_limit(db, 1)
        assert result.name == "Jane"

    def test_handles_error(self):
        db = MagicMock()
        db.get_session.side_effect = RuntimeError("boom")
        assert _get_connection_limit(db, 1) is None


@pytest.mark.unit
class TestSmartChecker:
    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self):
        automation = _automation(authenticated=False)
        with pytest.raises(Exception):
            await smart_connection_checker(automation, 1)

    @pytest.mark.asyncio
    async def test_no_pending_returns_zeros(self):
        automation = _automation(pending=[])
        result = await smart_connection_checker(automation, 1)
        assert result == {"checked": 0, "newly_accepted": 0, "updated": 0}

    @pytest.mark.asyncio
    async def test_sweep_includes_possibly_sent(self):
        """The smart sweep queries both 'sent' and 'possibly_sent' (issue #31)."""
        automation = _automation(pending=[])
        await smart_connection_checker(automation, 1)
        queried = {
            call.args[1]
            for call in automation.db_manager.get_contacts_by_status.call_args_list
        }
        assert "sent" in queried
        assert "possibly_sent" in queried


@pytest.mark.unit
class TestCheckSpecificContacts:
    @pytest.mark.asyncio
    async def test_not_authenticated_raises(self):
        automation = _automation(authenticated=False)
        with pytest.raises(Exception):
            await check_specific_contacts(automation, [1])

    @pytest.mark.asyncio
    async def test_accepted_contact_is_updated(self):
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent", profile_url="https://x/in/jane/"
        )
        automation = _automation(contact=contact, is_connected=True)
        stats = await check_specific_contacts(automation, [1])
        assert stats["checked"] == 1
        assert stats["newly_accepted"] == 1
        automation.db_manager.update_contact.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_non_sent_contact(self):
        contact = SimpleNamespace(
            id=2, name="Bob", status="accepted", profile_url="https://x/in/bob/"
        )
        automation = _automation(contact=contact)
        stats = await check_specific_contacts(automation, [2])
        assert stats["checked"] == 0
        automation.db_manager.update_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_possibly_sent_contact_is_checked(self):
        """possibly_sent (issue #31) is polled for acceptance like sent."""
        contact = SimpleNamespace(
            id=3, name="Ann", status="possibly_sent",
            profile_url="https://x/in/ann/",
        )
        automation = _automation(contact=contact, is_connected=True)
        stats = await check_specific_contacts(automation, [3])
        assert stats["checked"] == 1
        assert stats["newly_accepted"] == 1
        automation.db_manager.update_contact.assert_called_once()


@pytest.mark.unit
class TestMonitor:
    @pytest.mark.asyncio
    async def test_stops_when_no_pending(self):
        automation = _automation(pending=[])
        result = await monitor_pending_connections(
            automation, [1], check_interval_minutes=0, max_iterations=3
        )
        # No pending connections -> stops after the first iteration.
        assert result["iterations"] == 1
        assert result["total_newly_accepted"] == 0
