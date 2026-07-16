"""
Tests for connection-status checking (src/automation/checker.py).
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from automation.checker import (
    _clean_profile_url,
    _get_connection_limit,
    monitor_pending_connections,
    smart_connection_checker,
)


@pytest.fixture(autouse=True)
def _stub_navigate_guarded(monkeypatch):
    """checker.py now drives the connections-page goto through
    navigate_guarded (same guarded navigation the search flows use). Its full
    retry/settle/surf/guard machinery is exercised in test_navigation.py; here
    it is a thin pass-through (still calling page.goto, so existing goto-based
    assertions keep working) unless a test overrides it to raise.
    """
    async def _navigate(page, url, **kwargs):
        await page.goto(url)
        return page

    monkeypatch.setattr(
        "automation.checker.navigate_guarded", AsyncMock(side_effect=_navigate)
    )


def _automation(authenticated=True, pending=None):
    automation = MagicMock()
    automation.is_authenticated = authenticated
    automation._nav_kwargs = MagicMock(return_value={})
    automation._recover = AsyncMock()
    automation._mark_session_compromised = MagicMock()

    page = AsyncMock()
    page.url = "https://www.linkedin.com/mynetwork/invite-connect/connections/"
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    page.query_selector_all = AsyncMock(return_value=[])
    page.content = AsyncMock(return_value="")
    page.keyboard = AsyncMock()
    automation.page = page
    automation.context = AsyncMock()

    db = MagicMock()
    db.get_contacts_by_status.return_value = pending or []
    db.update_contact = MagicMock()
    db.update_campaign_stats = MagicMock()
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


def _new_tab():
    """A stubbed enrichment tab (``automation.context.new_page()``'s return).

    ``query_selector``/``content`` are wired clean (no match / empty page) so
    ``detect_captcha`` — now called on this tab too, see
    ``_update_accepted_connection`` — reads it as challenge-free by default.
    A bare ``AsyncMock()`` would fail that check: its unstubbed
    ``query_selector(...)`` and the result's ``is_visible()`` both resolve to
    a truthy ``MagicMock``, which ``detect_captcha`` reads as a CAPTCHA match.
    """
    page = AsyncMock()
    page.query_selector = AsyncMock(return_value=None)
    page.content = AsyncMock(return_value="")
    return page


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
class TestChallengeSafety:
    """A challenge/checkpoint on the connections page must raise loudly, not
    read as a clean 'no connections found' — and must not let the scroll
    loop hammer keypresses against it."""

    @pytest.mark.asyncio
    async def test_post_navigation_captcha_raises_instead_of_clean_empty(
        self, monkeypatch
    ):
        from exceptions import CaptchaDetectedException

        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        monkeypatch.setattr(
            "automation.checker.detect_captcha", AsyncMock(return_value=True)
        )

        with pytest.raises(CaptchaDetectedException):
            await smart_connection_checker(automation, 1)

        # Marked compromised so close_browser doesn't persist a challenged
        # session (issue #2's guard, reused here).
        automation._mark_session_compromised.assert_called_once()
        # Never reached the scroll/keypress loop.
        automation.page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_mid_scroll_captcha_raises_before_hammering_keypresses(
        self, monkeypatch
    ):
        """A challenge that appears AFTER the initial landing (e.g. between
        scroll rounds) is caught before that round's keypress-mashing."""
        from exceptions import CaptchaDetectedException

        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        # First call is the post-navigation check (clean); the round-top
        # check on the first scroll round then finds the challenge.
        monkeypatch.setattr(
            "automation.checker.detect_captcha",
            AsyncMock(side_effect=[False, True]),
        )

        with pytest.raises(CaptchaDetectedException):
            await smart_connection_checker(automation, 1)

        automation.page.keyboard.press.assert_not_called()

    @pytest.mark.asyncio
    async def test_navigation_challenge_raises_and_marks_compromised(
        self, monkeypatch
    ):
        """A URL-level challenge bounce from the guarded navigation is
        re-raised (not swallowed into a clean-empty result) too."""
        from exceptions import NotAuthenticatedException

        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent",
            profile_url="https://www.linkedin.com/in/jane/",
        )
        automation = _automation(pending=[contact])

        async def _raise_wall(page, url, **kwargs):
            raise NotAuthenticatedException("session expired")

        monkeypatch.setattr(
            "automation.checker.navigate_guarded", AsyncMock(side_effect=_raise_wall)
        )

        with pytest.raises(NotAuthenticatedException):
            await smart_connection_checker(automation, 1)

        automation._mark_session_compromised.assert_called_once()

    @pytest.mark.asyncio
    async def test_enrichment_tab_challenge_raises_and_marks_compromised(
        self, monkeypatch
    ):
        """A checkpoint hit while enriching an accepted contact's info opens a
        NEW tab (_update_accepted_connection) and previously raised no typed
        exception at all: the raw goto never checked the landing, and a broad
        except swallowed anything that did go wrong, so the session stayed
        marked authenticated and close_browser could persist a compromised
        session.json (issue #58). It must now raise and mark the session
        compromised exactly like a challenge on the main connections page."""
        from exceptions import CaptchaDetectedException

        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent", profile_url=url,
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url)]
        )
        new_page = AsyncMock()
        automation.context.new_page = AsyncMock(return_value=new_page)

        async def _navigate(page, target_url, **kwargs):
            # Only the enrichment tab (not the connections-page navigation)
            # hits the challenge, mirroring the reported scenario where
            # automation.page never left the connections page.
            if page is new_page:
                raise CaptchaDetectedException("checkpoint on enrichment tab")
            await page.goto(target_url)
            return page

        monkeypatch.setattr(
            "automation.checker.navigate_guarded", AsyncMock(side_effect=_navigate)
        )

        with pytest.raises(CaptchaDetectedException):
            await smart_connection_checker(automation, 1)

        automation._mark_session_compromised.assert_called_once()
        new_page.close.assert_awaited_once()
        automation.db_manager.update_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_tab_in_page_captcha_raises_and_marks_compromised(
        self, monkeypatch
    ):
        """A CAPTCHA can render on the enrichment tab without a URL bounce
        (navigate_guarded's landing check only catches URL-level challenges)
        — detect_captcha must be checked on the tab itself too, or a
        checkpoint widget would leave get_contact_info to silently return an
        empty dict with no exception at all, and the session would stay
        marked authenticated (issue #58)."""
        from exceptions import CaptchaDetectedException

        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent", profile_url=url,
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url)]
        )
        new_page = _new_tab()
        automation.context.new_page = AsyncMock(return_value=new_page)

        async def _detect_captcha(page):
            # Only the enrichment tab shows the in-page widget; the main
            # connections page (automation.page) stays clean throughout.
            return page is new_page

        monkeypatch.setattr("automation.checker.detect_captcha", _detect_captcha)

        with pytest.raises(CaptchaDetectedException):
            await smart_connection_checker(automation, 1)

        automation._mark_session_compromised.assert_called_once()
        new_page.close.assert_awaited_once()
        automation.db_manager.update_contact.assert_not_called()

    @pytest.mark.asyncio
    async def test_enrichment_tab_navigates_guarded_without_recover(self):
        """The enrichment visit drives the NEW tab itself through
        navigate_guarded (not automation.page — it never left the connections
        list) and disables crash recovery: a wedge on this side tab must not
        trigger a refresh of the main connections-page context."""
        from automation import checker as checker_module

        url = "https://www.linkedin.com/in/jane/"
        contact = SimpleNamespace(
            id=1, campaign_id=1, name="Jane", status="sent", profile_url=url,
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        automation.page.query_selector = AsyncMock(return_value=None)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url)]
        )
        new_page = _new_tab()
        automation.context.new_page = AsyncMock(return_value=new_page)

        await smart_connection_checker(automation, 1)

        # The enrichment call is the second navigate_guarded call (the first
        # is the connections-page navigation on automation.page).
        enrichment_call = checker_module.navigate_guarded.call_args_list[-1]
        assert enrichment_call.args[0] is new_page
        assert enrichment_call.kwargs["recover"] is None


@pytest.mark.unit
class TestCooperativeStop:
    """Issue #43: the smart checker polls a stop flag between profiles and
    returns partial stats carrying ``stopped: True`` — never a torn per-profile
    update."""

    @pytest.mark.asyncio
    async def test_smart_checker_stop_during_scroll_is_responsive(self):
        """A stop landing mid-scroll ends the walk within one scroll step —
        the loading phase must not run out its full tens-of-seconds round."""
        import threading

        pending = [SimpleNamespace(id=1, name="Jane", profile_url="https://x/in/jane/")]
        automation = _automation(pending=pending)
        _set_limit(automation, None)
        stop = threading.Event()

        async def _press(key):
            stop.set()  # the user presses Stop during the scroll phase

        automation.page.keyboard.press = AsyncMock(side_effect=_press)

        stats = await smart_connection_checker(automation, 1, stop_event=stop)

        assert stats.get("stopped") is True
        assert stats["checked"] == 0
        # The very next poll observed the flag: one keypress, no card harvest.
        assert automation.page.keyboard.press.await_count == 1
        automation.page.query_selector_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_smart_checker_preset_stop_returns_partial_stats(self):
        import threading

        pending = [SimpleNamespace(id=1, name="Jane", profile_url="https://x/in/jane/")]
        automation = _automation(pending=pending)
        _set_limit(automation, None)
        stop = threading.Event()
        stop.set()

        stats = await smart_connection_checker(automation, 1, stop_event=stop)

        assert stats.get("stopped") is True
        assert stats["checked"] == 0
        # The walk ended before any scrolling round drove the keyboard.
        automation.page.keyboard.press.assert_not_called()


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
        contact = SimpleNamespace(
            id=7, campaign_id=1, name="Jane", status="sent", profile_url=url
        )
        automation = _automation(pending=[contact])
        _set_limit(automation, None)
        # A "Recently added" element to focus, then one matching connection card
        # whose href carries tracking params (must be normalized to match).
        # Only the "Recently added" selector resolves to an element; every
        # other query_selector call (including the CAPTCHA-detection probes)
        # must see None.
        recent = AsyncMock()

        async def _query_selector(selector):
            if selector == '[data-view-name="connections-profile"]':
                return recent
            return None

        automation.page.query_selector = AsyncMock(side_effect=_query_selector)
        automation.page.query_selector_all = AsyncMock(
            return_value=[_connection_el(url + "?trk=foo")]
        )
        automation.context.new_page = AsyncMock(return_value=_new_tab())
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
        # Stale Campaign.total_accepted fix: the affected campaign's stats are
        # refreshed after the reconciliation pass.
        automation.db_manager.update_campaign_stats.assert_called_once_with(1)

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
            id=9, campaign_id=1, name="Jane", status="possibly_sent", profile_url=url,
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
        contact = SimpleNamespace(
            id=5, campaign_id=1, name="Jane", status="sent", profile_url=url
        )
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
