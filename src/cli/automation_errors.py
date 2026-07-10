"""Typed automation error → user-facing message mapping.

One home for the six exception headlines and the evidence-reference wording so
the Textual TUI (``src/tui/screens/automation_errors.py`` re-exports from here)
and the non-interactive ``linkedin-run`` entry point (``cli.runner``) cannot
drift. The strings returned are plain text: each caller applies its own
presentation on top (the TUI writes them into a ``markup=False`` log as-is;
``linkedin-run`` prints them straight to stderr).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from exceptions import (
    BrowserProfileBusyError,
    CaptchaDetectedException,
    LinkedInAutomationError,
    NotAuthenticatedException,
    RateLimitExceededException,
    SelectorNotFoundException,
    UnexpectedLandingException,
)


def _default_artifacts_dir() -> Path:
    """Deterministic artifacts location: env override first, then home default."""
    override = os.getenv("LINKEDIN_CLI_ARTIFACTS_DIR")
    return (
        Path(override)
        if override
        else Path.home() / ".linkedin-networking-cli" / "artifacts"
    )


def evidence_reference(
    exc: BaseException | None,
    artifacts_dir: Callable[[], Path] | None = None,
) -> str:
    """Describe where the saved diagnostics evidence lives, for the user.

    The lower automation layers capture an evidence bundle (screenshot + DOM
    snapshot) before raising and attach it to the exception as ``exc.evidence``
    (the dict from ``capture_error_context``). When those concrete artifact
    paths are available we point straight at them; otherwise we fall back to
    the artifacts directory so the message still tells the user where to look.

    ``artifacts_dir`` optionally resolves that directory (the CLI passes the
    diagnostics layer's ``_artifacts_dir``); if it is omitted or raises, the
    deterministic env-override/home default is used instead.
    """
    evidence = getattr(exc, "evidence", None) if exc is not None else None
    if isinstance(evidence, dict):
        paths = [p for p in (evidence.get("screenshot"), evidence.get("dom")) if p]
        if len(paths) == 1:
            return f"Evidence saved to {paths[0]}"
        if paths:
            joined = "\n  - ".join(paths)
            return f"Evidence saved to:\n  - {joined}"
    # No concrete bundle on the exception: point at the artifacts directory.
    resolved: Path
    if artifacts_dir is not None:
        try:
            resolved = artifacts_dir()
        except Exception:
            resolved = _default_artifacts_dir()
    else:
        resolved = _default_artifacts_dir()
    return f"Evidence (screenshot/DOM) saved under {resolved}"


def describe_automation_error(
    exc: BaseException,
    action_label: str,
    artifacts_dir: Callable[[], Path] | None = None,
) -> tuple[str, str]:
    """Return ``(headline, evidence_reference)`` for an automation failure.

    Distinct, actionable headline per typed automation exception, with a
    generic fallback for anything else. Plain text — no markup escaping.
    """
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
    elif isinstance(exc, BrowserProfileBusyError):
        headline = f"Browser profile in use by another run — stopped. {exc}"
    elif isinstance(exc, LinkedInAutomationError):
        headline = f"Automation stopped during {action_label}: {exc}"
    else:
        headline = f"Unexpected error during {action_label} — stopped: {exc}"
    return headline, evidence_reference(exc, artifacts_dir)
