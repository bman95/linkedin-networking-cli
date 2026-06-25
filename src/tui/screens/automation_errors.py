"""Typed-error → friendly message mapping for the automation screens (#24).

A faithful port of the classic CLI's ``_report_automation_failure``
(``linkedin_cli.py``): map an automation exception to a distinct, actionable
stop message plus a pointer to the saved evidence (screenshot/DOM), so the TUI
shows the same guidance instead of a traceback. The TUI renders these in a
``markup=False`` log, so no Rich escaping is needed here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple

from exceptions import (
    CaptchaDetectedException,
    LinkedInAutomationError,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
    UnexpectedLandingException,
)


def _evidence_reference(exc: Exception) -> str:
    """Where the saved diagnostics evidence lives, mirroring the classic helper.

    Prefer the concrete artifact paths attached to ``exc.evidence``; otherwise
    fall back to the artifacts directory (honouring the env override).
    """
    evidence = getattr(exc, "evidence", None)
    if isinstance(evidence, dict):
        paths = [p for p in (evidence.get("screenshot"), evidence.get("dom")) if p]
        if len(paths) == 1:
            return f"Evidence saved to {paths[0]}"
        if paths:
            joined = "\n  - ".join(paths)
            return f"Evidence saved to:\n  - {joined}"
    override = os.getenv("LINKEDIN_CLI_ARTIFACTS_DIR")
    artifacts_dir = (
        Path(override) if override else Path.home() / ".linkedin-networking-cli" / "artifacts"
    )
    return f"Evidence (screenshot/DOM) saved under {artifacts_dir}"


def describe_automation_error(exc: Exception, action_label: str) -> Tuple[str, str]:
    """Return ``(headline, evidence_reference)`` for an automation failure."""
    if isinstance(exc, CaptchaDetectedException):
        headline = (
            "LinkedIn is showing a security checkpoint or CAPTCHA — stopped. "
            "Complete the verification in a normal browser, then try again."
        )
    elif isinstance(exc, RateLimitExceededException):
        headline = (
            "LinkedIn rate limit reached — stopped. Wait before sending more "
            "invitations (limits reset over the following hours/days)."
        )
    elif isinstance(exc, NotAuthenticatedException):
        headline = (
            "LinkedIn session is no longer authenticated — stopped. "
            "Log in again to refresh the session, then retry."
        )
    elif isinstance(exc, UnexpectedLandingException):
        headline = (
            "Navigation landed on an unexpected page — stopped. LinkedIn may "
            "have changed its layout or redirected the request."
        )
    elif isinstance(exc, SelectorNotFoundException):
        headline = (
            "A required page element was not found — stopped. LinkedIn's page "
            "structure may have changed, or the page failed to load."
        )
    elif isinstance(exc, LinkedInAutomationError):
        headline = f"Automation stopped during {action_label}: {exc}"
    else:
        headline = f"Unexpected error during {action_label} — stopped: {exc}"
    return headline, _evidence_reference(exc)
