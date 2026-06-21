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
from linkedin_cli import LinkedInCLI
from exceptions import (
    CaptchaDetectedException,
    RateLimitExceededException,
    NotAuthenticatedException,
    SelectorNotFoundException,
    UnexpectedLandingException,
    LinkedInAutomationError,
)


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
