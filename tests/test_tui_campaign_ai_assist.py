"""Pilot-driven tests for the "describe your campaign in plain language"

AI Assist panel, embedded in Create Campaign only (Edit is untouched).

No real LLM/Ollama/network: every test overrides the panel's seams
(``perform_extraction``, ``check_model_available``, ``perform_pull``) exactly
like ``perform_location_search`` is overridden in test_tui_location_search.py.
"""

import threading

import pytest
from textual.widgets import Button, Input, ListView, Select, Static, TextArea

from database.operations import DatabaseManager
from llm_assist import ExtractedCampaign, ExtractionResult
from llm_assist.errors import LLMUnavailableError
from llm_assist.matching import MatchResult
from tui.app import CreateCampaignScreen, HomeScreen, LinkedInTUI
from tui.screens.campaign_ai_assist import CampaignAIAssistPanel

# ── helpers ──────────────────────────────────────────────────────────────


def _extracted(**overrides) -> ExtractedCampaign:
    base = dict(
        name=None,
        description=None,
        keywords=None,
        location_text=None,
        industry_text=None,
        network_text=None,
        daily_limit=None,
        message_template=None,
    )
    base.update(overrides)
    return ExtractedCampaign(**base)


def _result(data=None, flagged=(), location=None, industry=None, network=None) -> ExtractionResult:
    return ExtractionResult(
        data=data or _extracted(),
        flagged_fields=frozenset(flagged),
        location_match=location or MatchResult(None, None, False),
        industry_match=industry or MatchResult(None, None, False),
        network_match=network or MatchResult(None, None, False),
        repaired=False,
    )


async def goto_create(pilot) -> CreateCampaignScreen:
    """Open Create Campaign via arrows + Enter on the home nav list."""
    assert isinstance(pilot.app.screen, HomeScreen)
    nav = pilot.app.screen.query_one("#home-nav", ListView)
    while nav.index != 2:  # "New Campaign" is the third home item
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()
    screen = pilot.app.screen
    assert isinstance(screen, CreateCampaignScreen)
    return screen


async def submit_form(pilot, screen) -> None:
    """Tab to the form's submit button and press Enter (the only submit path)."""
    button = screen.query_one("#form-submit", Button)
    while pilot.app.focused is not button:
        await pilot.press("tab")
    await pilot.press("enter")


async def press_button(pilot, panel, selector: str) -> None:
    panel.query_one(selector, Button).focus()
    await pilot.press("enter")
    await pilot.pause()


async def expand_panel(pilot, screen) -> CampaignAIAssistPanel:
    panel = screen.query_one(CampaignAIAssistPanel)
    await press_button(pilot, panel, "#ai-assist-toggle")
    return panel


async def wait_until(pilot, predicate, tries: int = 150, step: float = 0.02) -> bool:
    for _ in range(tries):
        if predicate():
            return True
        await pilot.pause(step)
    return predicate()


def status_text(panel) -> str:
    return str(panel.query_one("#ai-assist-status", Static).render())


# ── collapse / expand ────────────────────────────────────────────────────


@pytest.mark.unit
async def test_panel_starts_collapsed_and_field_name_keeps_focus(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = screen.query_one(CampaignAIAssistPanel)
        assert panel.query_one("#ai-assist-body").display is False
        assert app.focused is screen.query_one("#field-name", Input)


@pytest.mark.unit
async def test_toggle_expands_and_focuses_the_model_select(db_manager: DatabaseManager):
    """The model select is the first interactive control in local mode (the
    default in tests, no LLM_API_KEY set); the toggle focuses it, not the
    TextArea further down (regression: Tab order previously skipped it)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)
        assert panel.query_one("#ai-assist-body").display is True
        assert app.focused is panel.query_one("#ai-assist-model-select", Select)


@pytest.mark.unit
async def test_toggle_focuses_the_textarea_in_hosted_mode(
    db_manager: DatabaseManager, monkeypatch
):
    """In hosted mode the model select is hidden, so it must not receive
    focus; the TextArea (the first visible control) does instead."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)
        assert panel.query_one("#ai-assist-model-select", Select).display is False
        assert app.focused is panel.query_one("#ai-assist-input", TextArea)


# ── happy path / flagging ────────────────────────────────────────────────


@pytest.mark.unit
async def test_happy_path_prefills_unflagged_fields_and_focuses_name(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        result = _result(
            data=_extracted(
                name="SF Engineers", keywords="python", daily_limit=15,
                message_template="Hi {name}!",
            ),
            location=MatchResult("San Francisco Bay Area", "San Francisco", False),
            industry=MatchResult("Computer Software", "software", False),
        )
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "Software engineers in SF, 15/day"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert screen.query_one("#field-name", Input).value == "SF Engineers"
        assert screen.query_one("#field-location", Select).value == "San Francisco Bay Area"
        assert screen.query_one("#field-industry", Select).value == "Computer Software"
        assert screen.query_one("#field-daily", Input).value == "15"
        assert screen.query_one("#field-message", Input).value == "Hi {name}!"
        # 6 of 8: description and network were never mentioned (regression —
        # the old "8 - len(flagged)" formula miscounted unmentioned fields as
        # filled since they're never flagged either).
        assert "Filled 6 of 8" in str(screen.query_one("#create-status", Static).render())
        # The panel folds back to its toggle so the filled form is what the
        # user is looking at, with focus handed to the form for review.
        assert panel.query_one("#ai-assist-body").display is False
        assert app.focused is screen.query_one("#field-name", Input)


@pytest.mark.unit
async def test_unmatched_location_leaves_any_and_shows_hint(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        result = _result(flagged={"location"}, location=MatchResult(None, "Atlantis", True))
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "People in Atlantis"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert screen.query_one("#field-location", Select).value == "Any"
        hint = screen.query_one("#hint-location", Static)
        assert hint.display is True
        assert "Atlantis" in str(hint.render())
        assert screen.query_one("#field-location").has_class("field-flagged")
        assert app.focused is screen.query_one("#field-location", Select)


@pytest.mark.unit
async def test_matched_but_needs_review_location_applies_value_and_flags(
    db_manager: DatabaseManager,
):
    """A containment/ratio location match (``MatchResult.needs_review=True``)
    is still APPLIED — unlike a fully unmatched mention — but the field must
    also be flagged and hinted for review (regression: _apply_match treated
    any ``matched_display_name`` as confident and skipped flagging/hinting).
    Also exercises the Select-echo race: setting .value programmatically
    queues a deferred Select.Changed that on_select_changed must not let
    strip the flag we just set (mirrors the Input snapshot guard)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        result = _result(
            data=_extracted(name="India Reach"),
            location=MatchResult("Bangalore, India", "India", True),
        )
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "connect with people in India"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        # give the value-set-triggered Select.Changed message time to be
        # processed — this is exactly the race the snapshot guard protects.
        await pilot.pause()
        await pilot.pause()

        location = screen.query_one("#field-location", Select)
        hint = screen.query_one("#hint-location", Static)
        assert location.value == "Bangalore, India"
        assert location.has_class("field-flagged")
        assert hint.display is True
        assert "India" in str(hint.render())
        assert "review" in str(hint.render()).lower()
        assert app.focused is location

        # "Filled" still counts the matched-but-flagged field, and the
        # summary stays coherent with the amber flag shown on the field.
        status = str(screen.query_one("#create-status", Static).render())
        assert "Filled 2 of 8" in status
        assert "review the highlighted ones" in status


@pytest.mark.unit
async def test_message_template_repair_flag_survives_the_async_echo_then_saves(
    db_manager: DatabaseManager,
):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        result = _result(
            data=_extracted(name="Repair Test", message_template="Hi {name}, connect?"),
            flagged={"message_template"},
        )
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "friendly outreach"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        # give the value-set-triggered Input.Changed message time to be
        # processed — this is exactly the race the snapshot guard protects.
        await pilot.pause()
        await pilot.pause()

        assert screen.query_one("#field-message").has_class("field-flagged")

        await submit_form(pilot, screen)
        assert await wait_until(
            pilot,
            lambda: "created" in str(screen.query_one("#create-status", Static).render()),
        )

    campaign = db_manager.get_campaigns(active_only=False)[0]
    assert campaign.message_template == "Hi {name}, connect?"


@pytest.mark.unit
async def test_ai_panel_locks_after_successful_create(db_manager: DatabaseManager):
    """A late extraction after a successful create must not write into the
    locked form, and the panel's own controls go disabled too (regression:
    ``_done()`` previously locked only the form, leaving the panel live)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        screen.query_one("#field-name", Input).value = "Locked Before AI"
        await submit_form(pilot, screen)
        assert await wait_until(
            pilot,
            lambda: "created" in str(screen.query_one("#create-status", Static).render()),
        )

        assert panel._locked is True
        assert panel.query_one("#ai-assist-toggle", Button).disabled is True
        assert panel.query_one("#ai-assist-run", Button).disabled is True
        assert panel.query_one("#ai-assist-input", TextArea).disabled is True

        # A straggling Extracted message (e.g. a run started before submit)
        # must be a no-op on the now-locked form.
        result = _result(data=_extracted(name="Should Not Apply"))
        panel.post_message(CampaignAIAssistPanel.Extracted(panel, result))
        await pilot.pause()
        assert screen.query_one("#field-name", Input).value == "Locked Before AI"


@pytest.mark.unit
async def test_daily_limit_flag_persists_then_clears_on_genuine_edit(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        result = _result(data=_extracted(name="Big limit", daily_limit=100), flagged={"daily_limit"})
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "connect with everyone"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()
        await pilot.pause()

        daily = screen.query_one("#field-daily", Input)
        assert daily.value == "100"
        assert daily.has_class("field-flagged")

        daily.value = "30"
        await pilot.pause()
        assert daily.has_class("field-flagged") is False


@pytest.mark.unit
async def test_rerun_preserves_hand_edited_fields_the_llm_did_not_mention(
    db_manager: DatabaseManager,
):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        screen.query_one("#field-keywords", Input).value = "hand typed keywords"

        result = _result(data=_extracted(name="From AI"))  # keywords stays None
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: result

        panel.query_one("#ai-assist-input", TextArea).text = "engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert screen.query_one("#field-name", Input).value == "From AI"
        assert screen.query_one("#field-keywords", Input).value == "hand typed keywords"


@pytest.mark.unit
async def test_rerun_clears_stale_flag_and_hint_when_no_longer_mentioned(
    db_manager: DatabaseManager,
):
    """A field flagged/hinted by one run must not keep its amber flag or
    "You said: …" hint after a re-run whose new description drops the mention
    (regression: fill_form_from_extraction only ever added flags/hints)."""
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        first = _result(
            data=_extracted(daily_limit=99),
            flagged={"daily_limit"},
            location=MatchResult(None, "Atlantis", True),
        )
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: first

        panel.query_one("#ai-assist-input", TextArea).text = (
            "connect with a lot of people in Atlantis"
        )
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()
        await pilot.pause()

        daily = screen.query_one("#field-daily", Input)
        location = screen.query_one("#field-location", Select)
        hint = screen.query_one("#hint-location", Static)
        assert daily.has_class("field-flagged")
        assert location.has_class("field-flagged")
        assert hint.display is True
        assert "Atlantis" in str(hint.render())

        # Re-run: this time neither daily_limit nor the location is mentioned.
        # The successful first run folded the panel away, so re-expand it the
        # way a user would before running again.
        second = _result(data=_extracted(name="Second Pass"))
        panel.perform_extraction = lambda *a, **k: second

        await press_button(pilot, panel, "#ai-assist-toggle")
        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()
        await pilot.pause()

        assert daily.has_class("field-flagged") is False
        assert location.has_class("field-flagged") is False
        assert hint.display is False


# ── input validation / errors ───────────────────────────────────────────


@pytest.mark.unit
async def test_too_short_input_is_rejected_without_calling_extraction(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        calls = []
        panel.perform_extraction = lambda *a, **k: calls.append(1)

        panel.query_one("#ai-assist-input", TextArea).text = "hi"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()

        assert panel._busy is False
        assert "more detail" in status_text(panel)
        assert calls == []


@pytest.mark.unit
async def test_extraction_error_leaves_the_manual_form_usable(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        def boom(*a, **k):
            raise LLMUnavailableError("Connection refused")

        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = boom

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert "Can't reach" in status_text(panel)
        # Nothing was applied, so the panel stays open with its error visible
        # (it only folds away after a successful fill).
        assert panel.query_one("#ai-assist-body").display is True
        assert screen.query_one("#field-name", Input).value == ""

        screen.query_one("#field-name", Input).value = "Manual fallback"
        await submit_form(pilot, screen)
        assert await wait_until(
            pilot,
            lambda: "created" in str(screen.query_one("#create-status", Static).render()),
        )


# ── missing model / pull ─────────────────────────────────────────────────


@pytest.mark.unit
async def test_missing_model_offers_both_pull_and_manual_command(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        calls = []
        panel.check_model_available = lambda *a, **k: False
        panel.perform_extraction = lambda *a, **k: calls.append(1)

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert calls == []
        assert panel.query_one("#ai-assist-missing-model").display is True
        assert "isn't downloaded" in str(panel.query_one("#missing-model-status", Static).render())

        await press_button(pilot, panel, "#missing-model-manual-btn")
        command = panel.query_one("#missing-model-command", Static)
        assert command.display is True
        assert "ollama pull" in str(command.render())


@pytest.mark.unit
async def test_pull_success_auto_resumes_extraction(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        panel.check_model_available = lambda *a, **k: False

        def fake_pull(llm_settings, model, on_progress, should_stop):
            on_progress({"status": "pulling manifest"})
            on_progress({"status": "downloading", "completed": 50, "total": 100})

        panel.perform_pull = fake_pull
        panel.perform_extraction = lambda *a, **k: _result(data=_extracted(name="After Pull"))

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        await press_button(pilot, panel, "#missing-model-pull-btn")
        assert panel.query_one("#ai-assist-pull-confirm").armed is True
        panel.check_model_available = lambda *a, **k: True  # available once pulled
        await pilot.press("enter")  # confirm
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert panel.query_one("#ai-assist-missing-model").display is False
        assert screen.query_one("#field-name", Input).value == "After Pull"


@pytest.mark.unit
async def test_pull_cancel_installs_nothing_and_keeps_offering_both_options(
    db_manager: DatabaseManager,
):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        started = threading.Event()

        def fake_pull(llm_settings, model, on_progress, should_stop):
            on_progress({"status": "pulling manifest"})
            started.set()
            while not should_stop():
                pass  # cooperative-cancel poll, mirroring the real client

        calls = []
        panel.check_model_available = lambda *a, **k: False
        panel.perform_pull = fake_pull
        panel.perform_extraction = lambda *a, **k: calls.append(1)

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        await press_button(pilot, panel, "#missing-model-pull-btn")
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: started.is_set())
        assert panel._busy is True
        assert panel.query_one("#ai-assist-stop", Button).display is True

        panel.request_stop()
        assert await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()

        assert "cancelled" in status_text(panel).lower()
        assert panel.query_one("#ai-assist-missing-model").display is True
        assert calls == []


# ── double-submit / concurrency ─────────────────────────────────────────


@pytest.mark.unit
async def test_double_submit_run_is_a_noop_while_busy(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        started = threading.Event()
        release = threading.Event()
        calls = []

        def slow_extraction(*a, **k):
            calls.append(1)
            started.set()
            release.wait(timeout=5)
            return _result(data=_extracted(name="Slow"))

        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = slow_extraction

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        assert await wait_until(pilot, lambda: started.is_set())

        panel.action_run()  # a second Run while busy must not start another worker
        await pilot.pause()
        assert len(calls) == 1

        release.set()
        assert await wait_until(pilot, lambda: not panel._busy)


# ── hosted mode / privacy consent ───────────────────────────────────────


@pytest.mark.unit
async def test_hosted_consent_shown_once_persists_and_decline_skips_extraction(
    db_manager: DatabaseManager, monkeypatch
):
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)
        assert panel._llm_settings["mode"] == "hosted"

        calls = []
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: calls.append(1) or _result(
            data=_extracted(name="Hosted")
        )

        panel.query_one("#ai-assist-input", TextArea).text = "recruiters in NYC"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()

        notice = panel.query_one("#ai-assist-privacy-notice")
        confirm_bar = panel.query_one("#ai-assist-privacy-confirm")
        assert notice.display is True

        # decline first — no HTTP call, no persisted opt-out
        confirm_bar.query_one(".confirm-no", Button).focus()
        await pilot.press("enter")
        await pilot.pause()
        assert calls == []
        assert db_manager.get_setting("llm_hosted_consent_ack", False) is False
        assert notice.display is False

        # try again, this time confirm
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()
        assert notice.display is True
        confirm_bar.query_one(".confirm-yes", Button).focus()
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()
        assert calls == [1]
        assert db_manager.get_setting("llm_hosted_consent_ack", False) is True

        # a fresh run no longer shows the notice
        panel.query_one("#ai-assist-input", TextArea).text = "recruiters in SF"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        assert await wait_until(pilot, lambda: not panel._busy)
        assert notice.display is False
        assert calls == [1, 1]


@pytest.mark.unit
async def test_hosted_mode_missing_api_key_is_blocked_before_any_call(
    db_manager: DatabaseManager, monkeypatch
):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("LLM_MODE", "hosted")
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        calls = []
        panel.perform_extraction = lambda *a, **k: calls.append(1)

        panel.query_one("#ai-assist-input", TextArea).text = "recruiters in NYC"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()

        assert "API key" in status_text(panel)
        assert calls == []


@pytest.mark.unit
async def test_hosted_consent_gate_fails_closed_when_db_manager_unavailable(
    db_manager: DatabaseManager, monkeypatch
):
    """Issue #63: a degraded app (DatabaseManager init failed, e.g. read-only
    home) must still require the privacy confirmation before a hosted-mode
    send — not skip the gate because consent can't be persisted."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-4o-mini")
    app = LinkedInTUI(db_manager=db_manager)
    app.db_manager = None
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)
        assert panel._db_manager is None

        calls = []
        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = lambda *a, **k: calls.append(1) or _result(
            data=_extracted(name="Hosted")
        )

        panel.query_one("#ai-assist-input", TextArea).text = "recruiters in NYC"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()

        # Gate shown, no call made yet.
        notice = panel.query_one("#ai-assist-privacy-notice")
        assert notice.display is True
        assert calls == []

        # Confirming runs the extraction (consent can't be persisted, so it
        # can't be skipped on a later run either — see below).
        confirm_bar = panel.query_one("#ai-assist-privacy-confirm")
        confirm_bar.query_one(".confirm-yes", Button).focus()
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: not panel._busy)
        await pilot.pause()
        assert calls == [1]

        # A fresh run still shows the notice: nothing was persisted.
        panel.query_one("#ai-assist-input", TextArea).text = "recruiters in SF"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        await pilot.pause()
        assert notice.display is True
        assert calls == [1]


# ── escape / leave mid-run ──────────────────────────────────────────────


@pytest.mark.unit
async def test_esc_mid_run_warns_once_then_leaves(db_manager: DatabaseManager):
    app = LinkedInTUI(db_manager=db_manager)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen = await goto_create(pilot)
        panel = await expand_panel(pilot, screen)

        started = threading.Event()
        release = threading.Event()

        def slow_extraction(*a, **k):
            started.set()
            release.wait(timeout=5)
            return _result(data=_extracted(name="Slow"))

        panel.check_model_available = lambda *a, **k: True
        panel.perform_extraction = slow_extraction

        panel.query_one("#ai-assist-input", TextArea).text = "software engineers"
        await pilot.pause()
        await press_button(pilot, panel, "#ai-assist-run")
        assert await wait_until(pilot, lambda: started.is_set())

        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CreateCampaignScreen)
        assert "in progress" in status_text(panel).lower()

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CreateCampaignScreen)

        release.set()  # let the worker finish harmlessly after the screen left
        await pilot.pause()
