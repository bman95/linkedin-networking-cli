"""
Tests for connection-status checking (src/automation/checker.py).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.checker import (
    _clean_profile_url,
    _get_connection_limit,
    check_specific_contacts,
    monitor_pending_connections,
    smart_connection_checker,
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
    # The connected-check waits for the indicator selector; a timeout means
    # the profile is not a 1st-degree connection.
    if is_connected:
        page.wait_for_selector = AsyncMock()
    else:
        page.wait_for_selector = AsyncMock(
            side_effect=PlaywrightTimeoutError("indicator never visible")
        )
    page.keyboard = AsyncMock()
    automation.page = page
    automation.context = AsyncMock()

    db = MagicMock()
    db.get_contacts_by_status.return_value = pending or []
    db.get_contact.return_value = contact
    db.update_contact = MagicMock()
    automation.db_manager = db
    return automation


def _profile_el(href):
    """A connection card's inner profile element exposing an href."""
    profile = AsyncMock()
    profile.get_attribute = AsyncMock(return_value=href)
    return profile


def _connection_el(href):
    """A single connections-list element wrapping a profile with `href`."""
    conn = AsyncMock()
    conn.query_selector = AsyncMock(return_value=_profile_el(href))
    return conn


def _set_limit(automation, limit):
    """Wire `_get_connection_limit`'s session query to return `limit`."""
    session = automation.db_manager.get_session.return_value.__enter__.return_value
    session.exec.return_value.first.return_value = limit


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
    async def test_not_connected_when_indicator_wait_times_out(self):
        """A wait_for_selector timeout means not accepted (no false failure)."""
        contact = SimpleNamespace(
            id=4, name="Zoe", status="sent", profile_url="https://x/in/zoe/"
        )
        automation = _automation(contact=contact, is_connected=False)
        stats = await check_specific_contacts(automation, [4])
        assert stats["checked"] == 1
        assert stats["newly_accepted"] == 0
        assert stats["failed"] == 0
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

    @pytest.mark.asyncio
    async def test_handles_campaign_error(self, monkeypatch):
        """A per-campaign failure is caught; the run continues, then stops."""
        automation = _automation(pending=[])
        monkeypatch.setattr(
            "automation.checker.smart_connection_checker",
            AsyncMock(side_effect=RuntimeError("boom")),
        )
        result = await monitor_pending_connections(
            automation, [1], check_interval_minutes=0, max_iterations=2,
            progress_callback=lambda m: None,
        )
        # The error leaves checked at 0, so monitoring stops after iteration 1.
        assert result["iterations"] == 1

    @pytest.mark.asyncio
    async def test_waits_between_iterations(self, monkeypatch):
        """With acceptances still flowing, it waits between iterations."""
        automation = _automation(pending=[])
        monkeypatch.setattr(
            "automation.checker.smart_connection_checker",
            AsyncMock(return_value={"checked": 1, "newly_accepted": 0, "updated": 0}),
        )
        result = await monitor_pending_connections(
            automation, [1], check_interval_minutes=0, max_iterations=2
        )
        assert result["iterations"] == 2
        # It waited between iteration 1 and 2 (the last iteration doesn't wait).
        automation.page.wait_for_timeout.assert_awaited()


@pytest.mark.unit
class TestCleanProfileUrlFragmentOnly:
    def test_strips_fragment_without_query(self):
        # No "?" in the URL, so the fragment ("#") branch runs.
        assert _clean_profile_url(
            "https://www.linkedin.com/in/jane#about"
        ) == "https://www.linkedin.com/in/jane/"


@pytest.mark.unit
class TestSmartCheckerWalk:
    @pytest.fixture(autouse=True)
    def _fast_random(self, monkeypatch):
        # Keep the humanized scroll/keyboard loops tiny and deterministic.
        monkeypatch.setattr("automation.checker.random.randint", lambda a, b: a)

    @pytest.mark.asyncio
    async def test_walk_with_no_connection_cards_returns_zeros(self):
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(return_value=[])
        result = await smart_connection_checker(
            automation, 1, progress_callback=lambda m: None
        )
        assert result == {"checked": 0, "newly_accepted": 0, "updated": 0}
        automation.page.goto.assert_awaited()

    @pytest.mark.asyncio
    async def test_walk_marks_accepted_and_enriches_contact(self, monkeypatch):
        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(id=7, name="Jane", status="sent", profile_url=url)
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        # A "Recently added" element to focus, then one matching connection card
        # whose href carries tracking params (must be normalized to match).
        recent = AsyncMock()
        automation.page.query_selector = AsyncMock(return_value=recent)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url + "?trk=foo")]
        )
        automation.context.new_page = AsyncMock(return_value=AsyncMock())
        monkeypatch.setattr(
            "automation.checker.get_contact_info",
            AsyncMock(return_value={
                "email": "j@x.com", "phone": "555", "address": "NYC",
            }),
        )
        result = await smart_connection_checker(automation, 1)
        assert result == {"checked": 1, "newly_accepted": 1, "updated": 1}
        recent.focus.assert_awaited()
        contact_id, update = automation.db_manager.update_contact.call_args[0]
        assert contact_id == 7
        assert update["status"] == "accepted"
        assert update["email"] == "j@x.com"
        assert update["phone"] == "555"
        assert "NYC" in update["notes"]

    @pytest.mark.asyncio
    async def test_walk_stops_at_connection_limit(self):
        limit_url = "https://www.linkedin.com/in/bob/"
        limit = SimpleNamespace(name="Bob", profile_url=limit_url)
        pending = SimpleNamespace(
            id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[pending])
        _set_limit(automation, limit)
        automation.page.query_selector = AsyncMock(return_value=None)
        # The limit marker appears in the list; the walk must stop at it.
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(limit_url)]
        )
        result = await smart_connection_checker(
            automation, 1, progress_callback=lambda m: None
        )
        # Bob is the stop marker, not a pending contact -> nothing accepted.
        assert result["checked"] == 0
        assert result["newly_accepted"] == 0

    @pytest.mark.asyncio
    async def test_walk_handles_navigation_error(self):
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        automation.page.goto = AsyncMock(side_effect=RuntimeError("nav boom"))
        result = await smart_connection_checker(
            automation, 1, progress_callback=lambda m: None
        )
        assert result == {"checked": 0, "newly_accepted": 0, "updated": 0}

    @pytest.mark.asyncio
    async def test_update_swallows_enrichment_error(self):
        """A failure while enriching the accepted contact is caught, not raised."""
        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(
            id=9, name="Jane", status="possibly_sent", profile_url=url,
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url)]
        )
        automation.context.new_page = AsyncMock(side_effect=RuntimeError("no page"))
        result = await smart_connection_checker(automation, 1)
        # The card matched (counted) but enrichment failed, so no DB update.
        assert result["checked"] == 1
        automation.db_manager.update_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_update_closes_tab_when_enrichment_fails(self):
        """The enrichment tab is closed even when goto blows up (no tab leak)."""
        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(id=5, name="Jane", status="sent", profile_url=url)
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url)]
        )
        new_page = AsyncMock()
        new_page.goto = AsyncMock(side_effect=RuntimeError("nav boom"))
        automation.context.new_page = AsyncMock(return_value=new_page)
        await smart_connection_checker(automation, 1)
        new_page.close.assert_awaited_once()
        automation.db_manager.update_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_walk_without_limit_terminates_at_end_of_list(self):
        """With no stop marker and the same full page of cards every round,
        the no-new-profiles detection ends the walk after one repeat round
        (it looped forever before the guards existed) — and the result is NOT
        flagged as truncated: the whole list was seen."""
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[
                _connection_el(f"https://www.linkedin.com/in/other{i}/")
                for i in range(10)
            ]
        )
        messages = []
        result = await smart_connection_checker(
            automation, 1, progress_callback=messages.append
        )
        assert result == {"checked": 0, "newly_accepted": 0, "updated": 0}
        assert "truncated" not in result
        assert any("end of connections list" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_walk_pathological_feed_hits_scroll_backstop(self):
        """A page that keeps feeding NEW cards forever trips the scroll-rounds
        backstop, and the result is flagged truncated so callers can tell an
        incomplete check from a complete one."""
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)

        calls = {"n": 0}

        async def _fresh_cards(_selector):
            calls["n"] += 1
            base = calls["n"] * 100
            return [
                _connection_el(f"https://www.linkedin.com/in/other{base + i}/")
                for i in range(10)
            ]

        automation.page.query_selector_all = AsyncMock(side_effect=_fresh_cards)
        messages = []
        result = await smart_connection_checker(
            automation, 1, progress_callback=messages.append
        )
        assert result.get("truncated") is True
        assert any("maximum scroll rounds" in m.lower() for m in messages)


@pytest.mark.unit
class TestCheckSpecificContactsRich:
    @pytest.mark.asyncio
    async def test_collects_contact_info_and_reports(self, monkeypatch):
        contact = SimpleNamespace(
            id=1, name="Jane", status="sent", profile_url="https://x/in/jane/",
        )
        automation = _automation(contact=contact, is_connected=True)
        monkeypatch.setattr(
            "automation.checker.get_contact_info",
            AsyncMock(return_value={"email": "j@x.com", "phone": "555"}),
        )
        messages = []
        stats = await check_specific_contacts(
            automation, [1], progress_callback=messages.append
        )
        assert stats["newly_accepted"] == 1
        update = automation.db_manager.update_contact.call_args[0][1]
        assert update["email"] == "j@x.com"
        assert update["phone"] == "555"
        assert any("accepted" in m.lower() for m in messages)

    @pytest.mark.asyncio
    async def test_navigation_error_counts_as_failed(self):
        contact = SimpleNamespace(
            id=2, name="Bob", status="sent", profile_url="https://x/in/bob/",
        )
        automation = _automation(contact=contact)
        automation.page.goto = AsyncMock(side_effect=RuntimeError("boom"))
        stats = await check_specific_contacts(automation, [2])
        assert stats["failed"] == 1
        assert stats["checked"] == 0
