"""Tests for typed-exception surfacing at the CLI run loops.

These cover issue #18: the CLI run loops must catch the custom automation
exceptions *by type*, map each to a distinct user-friendly message that
references the saved diagnostics evidence artifact path(s), and hard-stop
cleanly — no interactive waiting (``inquirer.confirm``) and no traceback shown
to the user — with a generic fallback that still references evidence.
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import linkedin_cli
from exceptions import (
    CaptchaDetectedException,
    LinkedInAutomationError,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
    UnexpectedLandingException,
)
from linkedin_cli import LinkedInCLI


def _cli_with_recording_console():
    """Build a CLI instance without touching the real DB/browser.

    ``__init__`` initializes live components, so we bypass it and wire only the
    attributes the failure-reporting helpers need: a recording ``console``.
    """
    cli = object.__new__(LinkedInCLI)
    cli.console = Console(file=StringIO(), force_terminal=False, width=200)
    return cli


def _rendered(cli):
    return cli.console.file.getvalue()


# ---------------------------------------------------------------------------
# _format_evidence_reference
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFormatEvidenceReference:
    def test_uses_concrete_artifact_paths_from_exception(self):
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {
            "screenshot": "/home/u/.linkedin-networking-cli/artifacts/error_x.png",
            "dom": "/home/u/.linkedin-networking-cli/artifacts/error_x.html",
        }
        ref = LinkedInCLI._format_evidence_reference(exc)
        assert "error_x.png" in ref
        assert "error_x.html" in ref

    def test_single_path_when_only_one_artifact(self):
        exc = SelectorNotFoundException("missing")
        exc.evidence = {"screenshot": "/art/only.png", "dom": None}
        ref = LinkedInCLI._format_evidence_reference(exc)
        assert "/art/only.png" in ref
        assert "Evidence saved to /art/only.png" == ref

    def test_falls_back_to_artifacts_dir_when_no_bundle(self, monkeypatch):
        # No evidence attribute at all -> point at the artifacts directory.
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-artifacts")
        ref = LinkedInCLI._format_evidence_reference(ValueError("boom"))
        assert "/tmp/issue18-artifacts" in ref

    def test_falls_back_when_bundle_has_no_paths(self, monkeypatch):
        # Capture ran on a dead page and produced no files.
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-empty")
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {"screenshot": None, "dom": None}
        ref = LinkedInCLI._format_evidence_reference(exc)
        assert "/tmp/issue18-empty" in ref

    def test_none_exception_references_dir(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-none")
        ref = LinkedInCLI._format_evidence_reference(None)
        assert "/tmp/issue18-none" in ref


# ---------------------------------------------------------------------------
# _report_automation_failure — distinct message per type + evidence reference
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReportAutomationFailure:
    @pytest.mark.parametrize(
        "exc, marker",
        [
            (CaptchaDetectedException("x"), "security checkpoint"),
            (RateLimitExceededException("x"), "rate limit"),
            (NotAuthenticatedException("x"), "no longer authenticated"),
            (UnexpectedLandingException("x"), "unexpected page"),
            (SelectorNotFoundException("x"), "page element was not found"),
        ],
    )
    def test_distinct_message_per_typed_exception(self, exc, marker):
        cli = _cli_with_recording_console()
        exc.evidence = {"screenshot": "/art/e.png", "dom": "/art/e.html"}
        cli._report_automation_failure(exc, "campaign execution")
        out = _rendered(cli)
        assert marker in out
        # Every stop message references the saved evidence artifact path(s).
        assert "/art/e.png" in out

    def test_each_typed_message_is_unique(self):
        markers = set()
        for exc in (
            CaptchaDetectedException("x"),
            RateLimitExceededException("x"),
            NotAuthenticatedException("x"),
            UnexpectedLandingException("x"),
            SelectorNotFoundException("x"),
        ):
            cli = _cli_with_recording_console()
            cli._report_automation_failure(exc, "campaign execution")
            markers.add(_rendered(cli).strip())
        assert len(markers) == 5

    def test_generic_fallback_references_evidence(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-generic")
        cli = _cli_with_recording_console()
        cli._report_automation_failure(RuntimeError("unforeseen"), "campaign execution")
        out = _rendered(cli)
        assert "Unexpected error" in out
        assert "/tmp/issue18-generic" in out

    def test_unrecognized_automation_error_uses_base_branch(self):
        # A LinkedInAutomationError subtype not enumerated above still gets a
        # clean automation-stopped message (not the raw generic one).
        cli = _cli_with_recording_console()
        exc = LinkedInAutomationError("weird automation state")
        exc.evidence = {"screenshot": "/art/x.png", "dom": None}
        cli._report_automation_failure(exc, "campaign execution")
        out = _rendered(cli)
        assert "Automation stopped during campaign execution" in out
        assert "/art/x.png" in out

    def test_does_not_raise_on_capture_or_format_failure(self, monkeypatch):
        # Defensive: a throwing _artifacts_dir must not break the stop path.
        monkeypatch.delenv("LINKEDIN_CLI_ARTIFACTS_DIR", raising=False)
        monkeypatch.setattr(
            linkedin_cli, "_artifacts_dir", lambda: (_ for _ in ()).throw(OSError())
        )
        cli = _cli_with_recording_console()
        # No evidence -> hits the dir fallback, which is now throwing.
        cli._report_automation_failure(RuntimeError("boom"), "campaign execution")
        out = _rendered(cli)
        assert "Unexpected error" in out
        # Falls back to the default home artifacts path string.
        assert ".linkedin-networking-cli" in out

    def test_dir_fallback_honors_env_override_on_double_fault(self, monkeypatch):
        # If _artifacts_dir itself throws but the env override is set, the
        # message must still point at the configured dir, not the home default.
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-override")
        monkeypatch.setattr(
            linkedin_cli, "_artifacts_dir", lambda: (_ for _ in ()).throw(OSError())
        )
        cli = _cli_with_recording_console()
        cli._report_automation_failure(RuntimeError("boom"), "campaign execution")
        out = _rendered(cli)
        assert "/tmp/issue18-override" in out

    def test_no_traceback_dumped_to_user_stdout(self):
        # Acceptance criterion: a stop must not dump a traceback to the user.
        # The logging console handler writes to sys.stdout at WARNING+, so we
        # capture real stdout and assert no traceback text appears there.
        import contextlib

        cli = _cli_with_recording_console()
        captured = StringIO()
        with contextlib.redirect_stdout(captured):
            try:
                raise CaptchaDetectedException("blocked at checkpoint")
            except CaptchaDetectedException as e:
                cli._report_automation_failure(e, "campaign execution")
        assert "Traceback" not in captured.getvalue()
        assert "CaptchaDetectedException" not in captured.getvalue()

    def test_bracketed_detail_survives_rich_markup(self):
        # Rich treats [..] as markup; dynamic exception text / evidence paths
        # can contain brackets (CSS attribute selectors, encoded URLs, paths).
        # They must not be silently dropped from the user-facing message.
        cli = _cli_with_recording_console()
        exc = LinkedInAutomationError("state with selector a[href] and [encoded]")
        exc.evidence = {"screenshot": "/art/error_[odd]_x.png", "dom": None}
        cli._report_automation_failure(exc, "campaign execution")
        out = _rendered(cli)
        assert "a[href]" in out
        assert "[encoded]" in out
        assert "[odd]" in out

    def test_bracketed_detail_survives_in_generic_branch(self):
        cli = _cli_with_recording_console()
        cli._report_automation_failure(RuntimeError("boom [x] a[y]"), "campaign execution")
        out = _rendered(cli)
        assert "[x]" in out
        assert "a[y]" in out


# ---------------------------------------------------------------------------
# Run-loop integration: typed exception => hard stop, no interactive wait
# ---------------------------------------------------------------------------


class _FakeCampaign:
    """A non-dict campaign object so execute_campaign enters the real run path."""

    def __init__(self):
        self.id = 1
        self.name = "Test Campaign"
        self.daily_limit = 20


def _real_settings(validate=True):
    class _S:
        def validate_credentials(self):
            return validate

        def get_automation_settings(self):
            return {"search_limit": 10}

    return _S()


@pytest.mark.unit
class TestExecuteCampaignHardStop:
    """The execute_campaign run loop must hard-stop on a typed exception."""

    def _drive(self, raised_exc):
        cli = _cli_with_recording_console()
        campaign = _FakeCampaign()

        class _DB:
            def get_campaigns(self, active_only=False):
                return [campaign]

        cli.db_manager = _DB()
        cli.settings = _real_settings()

        # inquirer.select -> the campaign; inquirer.confirm -> True (start).
        # If the error path ever calls inquirer.confirm again ("Press Enter"),
        # this counter proves it (hard-stop means it must NOT be called again).
        confirm_calls = {"count": 0}

        class _Prompt:
            def __init__(self, value):
                self._value = value

            def execute(self):
                return self._value

        def fake_select(*a, **k):
            return _Prompt(campaign)

        def fake_confirm(*a, **k):
            confirm_calls["count"] += 1
            return _Prompt(True)

        def fake_run(coro):
            # Close the un-awaited coroutine to avoid a RuntimeWarning, then
            # raise the typed exception as if the automation had failed.
            coro.close()
            raise raised_exc

        with patch.object(linkedin_cli.inquirer, "select", side_effect=fake_select), \
             patch.object(linkedin_cli.inquirer, "confirm", side_effect=fake_confirm), \
             patch.object(linkedin_cli.asyncio, "run", side_effect=fake_run):
            cli.execute_campaign()

        return cli, confirm_calls["count"]

    def test_captcha_hard_stops_with_message_and_no_extra_confirm(self):
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {"screenshot": "/art/c.png", "dom": "/art/c.html"}
        cli, confirm_count = self._drive(exc)
        out = _rendered(cli)
        assert "security checkpoint" in out
        assert "/art/c.png" in out
        # Only the pre-run "Start automation?" confirm fired; the error path
        # added no "Press Enter to continue" wait (hard stop).
        assert confirm_count == 1

    def test_rate_limit_hard_stops(self):
        exc = RateLimitExceededException("weekly cap", limit_type="weekly")
        cli, confirm_count = self._drive(exc)
        out = _rendered(cli)
        assert "rate limit" in out
        assert confirm_count == 1

    def test_generic_error_hard_stops_with_evidence(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-loop")
        cli, confirm_count = self._drive(RuntimeError("unforeseen"))
        out = _rendered(cli)
        assert "Unexpected error" in out
        assert "/tmp/issue18-loop" in out
        assert confirm_count == 1


@pytest.mark.unit
class TestLocationSearchHardStop:
    """_run_location_search must report a typed message and return [] cleanly."""

    def _drive(self, raised_exc):
        cli = _cli_with_recording_console()
        cli.db_manager = object()  # only truthiness matters here
        cli.settings = _real_settings()

        # The loop must add no interactive wait of its own.
        confirm_calls = {"count": 0}

        class _Prompt:
            def execute(self):
                confirm_calls["count"] += 1
                return True

        def fake_confirm(*a, **k):
            return _Prompt()

        def fake_run(coro):
            coro.close()
            raise raised_exc

        with patch.object(linkedin_cli.inquirer, "confirm", side_effect=fake_confirm), \
             patch.object(linkedin_cli.asyncio, "run", side_effect=fake_run):
            result = cli._run_location_search("Madrid")

        return cli, result, confirm_calls["count"]

    def test_typed_message_and_empty_result_no_wait(self):
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {"screenshot": "/art/loc.png", "dom": None}
        cli, result, confirm_count = self._drive(exc)
        out = _rendered(cli)
        assert "security checkpoint" in out
        assert "/art/loc.png" in out
        # Contract preserved: failure yields [] (caller renders "no results").
        assert result == []
        # Hard-stop: no "Press Enter to continue" wait added by this loop.
        assert confirm_count == 0


@pytest.mark.unit
class TestConnectionCheckerSmartOnly:
    """Issue #45: the check flow is zero-config — pick campaign (or all),
    confirm, and the smart checker runs. No "choose checker method" prompt
    exists, and the reported stats are the smart checker's own real counts.
    """

    def _drive(self, selection):
        cli = _cli_with_recording_console()
        campaign = _FakeCampaign()

        from types import SimpleNamespace

        pending = [
            SimpleNamespace(id=1, status="sent"),
            SimpleNamespace(id=2, status="sent"),
            SimpleNamespace(id=3, status="possibly_sent"),
        ]

        class _DB:
            def get_campaigns(self, active_only=False):
                return [campaign]

            def get_contacts_by_status(self, campaign_id, status):
                return [c for c in pending if c.status == status]

        cli.db_manager = _DB()
        cli.settings = _real_settings()

        class _FakeAutomation:
            """Only exposes the smart checker — no direct-checker fallback."""

            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def login(self, progress_callback=None):
                return True

            async def smart_connection_checker(
                self, campaign_id, progress_callback=None, stop_event=None
            ):
                return {"checked": 1, "newly_accepted": 1, "updated": 1}

        class _Prompt:
            def __init__(self, value):
                self._value = value

            def execute(self):
                return self._value

        select_calls = {"count": 0}

        def fake_select(*a, **k):
            select_calls["count"] += 1
            return _Prompt(selection if selection == "all" else campaign)

        with patch.object(
            linkedin_cli.inquirer, "select", side_effect=fake_select
        ), patch.object(
            linkedin_cli.inquirer, "confirm", side_effect=lambda *a, **k: _Prompt(True)
        ), patch.object(linkedin_cli, "LinkedInAutomation", _FakeAutomation):
            cli.connection_checker()

        return _rendered(cli), select_calls["count"]

    @pytest.mark.parametrize("selection", ["single", "all"])
    def test_no_checker_type_prompt_and_smart_stats_reported(self, selection):
        out, select_count = self._drive(selection)
        # Zero-config: only the campaign-selection prompt fires — never a
        # "choose checker method" prompt.
        assert select_count == 1
        assert "Contacts Checked: 1" in out
