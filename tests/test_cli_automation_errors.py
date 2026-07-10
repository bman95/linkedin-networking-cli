"""Tests for the shared typed-exception error mapping (``cli.automation_errors``).

This pure mapping (``describe_automation_error`` / ``evidence_reference``) is
shared by the TUI (``src/tui/screens/automation_errors.py`` re-exports it) and
the non-interactive ``linkedin-run`` entry point (``cli.runner``). It used to
be exercised indirectly through the classic InquirerPy CLI's
``_report_automation_failure`` / ``_format_evidence_reference`` wrappers
(``tests/test_cli_run_loops.py``, issue #18), which were deleted along with
the rest of the interactive menu in the issue #47 cutover — these tests drive
the shared functions directly instead, so the branch coverage on the
underlying mapping isn't lost along with the CLI-specific presentation layer
(Rich escaping, console printing) that legitimately went away with it.
"""

import pytest

from cli.automation_errors import describe_automation_error, evidence_reference
from exceptions import (
    BrowserProfileBusyError,
    CaptchaDetectedException,
    LinkedInAutomationError,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
    UnexpectedLandingException,
)


@pytest.mark.unit
class TestEvidenceReference:
    def test_uses_concrete_artifact_paths_from_exception(self):
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {
            "screenshot": "/art/error_x.png",
            "dom": "/art/error_x.html",
        }
        ref = evidence_reference(exc)
        assert "/art/error_x.png" in ref
        assert "/art/error_x.html" in ref

    def test_single_path_when_only_one_artifact(self):
        exc = SelectorNotFoundException("missing")
        exc.evidence = {"screenshot": "/art/only.png", "dom": None}
        ref = evidence_reference(exc)
        assert ref == "Evidence saved to /art/only.png"

    def test_falls_back_to_artifacts_dir_when_no_bundle(self):
        ref = evidence_reference(ValueError("boom"), artifacts_dir=lambda: "/tmp/dir-a")
        assert "/tmp/dir-a" in ref

    def test_falls_back_when_bundle_has_no_paths(self):
        exc = CaptchaDetectedException("blocked")
        exc.evidence = {"screenshot": None, "dom": None}
        ref = evidence_reference(exc, artifacts_dir=lambda: "/tmp/dir-b")
        assert "/tmp/dir-b" in ref

    def test_none_exception_references_dir(self):
        ref = evidence_reference(None, artifacts_dir=lambda: "/tmp/dir-c")
        assert "/tmp/dir-c" in ref

    def test_default_home_dir_when_no_resolver_given(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-env-default")
        ref = evidence_reference(ValueError("boom"))
        assert "/tmp/issue18-env-default" in ref

    def test_does_not_raise_when_artifacts_dir_throws(self, monkeypatch):
        # Defensive: a throwing artifacts_dir resolver must not break the
        # caller — falls back to the deterministic env/home default instead.
        monkeypatch.delenv("LINKEDIN_CLI_ARTIFACTS_DIR", raising=False)

        def _throws():
            raise OSError("disk unavailable")

        ref = evidence_reference(RuntimeError("boom"), artifacts_dir=_throws)
        assert ".linkedin-networking-cli" in ref

    def test_dir_fallback_honors_env_override_on_double_fault(self, monkeypatch):
        # If artifacts_dir() itself throws but the env override is set, the
        # message must still point at the configured dir, not the home
        # default.
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-override")

        def _throws():
            raise OSError("disk unavailable")

        ref = evidence_reference(RuntimeError("boom"), artifacts_dir=_throws)
        assert "/tmp/issue18-override" in ref


@pytest.mark.unit
class TestDescribeAutomationError:
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
        headline, evidence = describe_automation_error(exc, "campaign execution")
        assert marker in headline
        assert "Evidence" in evidence

    def test_each_typed_message_is_unique(self):
        headlines = {
            describe_automation_error(exc, "campaign execution")[0]
            for exc in (
                CaptchaDetectedException("x"),
                RateLimitExceededException("x"),
                NotAuthenticatedException("x"),
                UnexpectedLandingException("x"),
                SelectorNotFoundException("x"),
            )
        }
        assert len(headlines) == 5

    def test_browser_profile_busy_uses_dedicated_branch(self):
        exc = BrowserProfileBusyError(
            "The browser profile at '/x/browser_data' is already in use by "
            "process 4242; wait or stop it first."
        )
        headline, evidence = describe_automation_error(exc, "campaign execution")
        assert "profile" in headline.lower()
        assert "4242" in headline
        assert "Evidence" in evidence

    def test_unrecognized_automation_error_uses_base_branch(self):
        # A LinkedInAutomationError subtype not enumerated above still gets a
        # clean "automation stopped" message (not the raw generic fallback).
        exc = LinkedInAutomationError("weird automation state")
        headline, _ = describe_automation_error(exc, "campaign execution")
        assert "Automation stopped during campaign execution" in headline
        assert "weird automation state" in headline

    def test_generic_fallback_references_evidence(self, monkeypatch):
        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/issue18-generic")
        headline, evidence = describe_automation_error(
            RuntimeError("unforeseen"), "campaign execution"
        )
        assert "Unexpected error" in headline
        assert "/tmp/issue18-generic" in evidence
