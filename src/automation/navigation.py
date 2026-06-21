"""Guarded navigation: verify the browser landed where we intended.

Today a navigation is a bare ``page.goto`` followed by a fixed
``wait_for_timeout`` sleep, and login state is inferred from the URL alone. That
is fragile: LinkedIn can bounce a ``goto`` to a challenge, a login wall, or a
soft block served from a non-login URL; it can cap a query param we explicitly
asked for (``?start=1000`` → ``?start=0``); and it can throw up a blocking modal
that eats the next click. None of these are visible to "did goto resolve?".

``navigate_guarded`` is the "am I where I expected?" gate. After every
navigation it:

- **Diffs the path** requested vs landed, flagging a path change or the reset of
  a param we *explicitly requested*, while ignoring params LinkedIn adds
  (tracking/session) so the check stays low-noise.
- **Detects a challenge/login bounce by URL, over DOM.** Any ``/checkpoint/``,
  ``/authwall``, ``/login``, ``/uas/`` in the landed path is a challenge/login
  *regardless of DOM* — the challenge DOM mutates faster than any selector list.
- **Sweeps for an unexpected blocking overlay** the workflow did not open, and
  captures an anomaly bundle (non-fatal: the modal is evidence, not a stop).

On a fatal mismatch it captures a diagnostics evidence bundle and raises a typed
exception (``CaptchaDetectedException`` / ``NotAuthenticatedException`` for a
challenge/login bounce, ``UnexpectedLandingException`` otherwise).

Two companions live here too:

- ``confirm_logged_in_dom`` — DOM-backed login confirmation (a logged-in nav
  landmark *in addition to* the URL), so a soft block on a non-login URL is not
  read as "logged in".
- ``verify_listing_rendered`` — empty-vs-not-rendered disambiguation for listing
  pages: race a ready-selector against an empty-selector and, on render timeout,
  reload once before trusting "no results" (re-checking for a challenge that
  replaced the listing).

Modeled on the LinkedIn Worker project's ``_guard_landing`` / ``_diff_redirect``
(``agent/src/workflows/base.py``) and ``detection.py``.

All functions are async and operate on an async Playwright ``Page``.
"""

import sys
import time
import urllib.parse
from pathlib import Path
from typing import Dict, Optional, Tuple

sys.path.append(str(Path(__file__).parent.parent))

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from automation import selectors as sel
from automation.diagnostics import capture_anomaly_context, capture_error_context
from utils.logging import get_logger
from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    UnexpectedLandingException,
)

logger = get_logger(__name__)

# Path *segments* that mark a challenge or login wall. Matched against the
# landed path's segments (not the full URL, and not a bare substring) so a
# profile that merely *links* to ``/login`` and a legitimate path that merely
# *contains* the text (e.g. ``/company/loginworks``) cannot trip the check.
# URL-over-DOM by design: the challenge DOM churns far faster than any selector
# list, so the stable signal is "the browser is sitting on a challenge/login
# path".
#
# ``login`` and ``uas`` are login walls -> NotAuthenticatedException.
# ``checkpoint`` and ``authwall`` are interstitial challenges -> CAPTCHA.
_LOGIN_PATH_SEGMENTS = ("login", "uas")
_CHALLENGE_PATH_SEGMENTS = ("checkpoint", "authwall")


def _path_segments(path: str) -> set:
    """Return the set of non-empty path segments (already lower-cased)."""
    return {seg for seg in path.split("/") if seg}


def _split(url: str) -> Tuple[str, Dict[str, str]]:
    """Return ``(path, query_dict)`` for ``url``.

    The path is lower-cased and trailing-slash-stripped so ``/feed`` and
    ``/feed/`` compare equal. Query values are taken first-wins (LinkedIn does
    not repeat the params we care about).
    """
    parsed = urllib.parse.urlsplit(url or "")
    path = (parsed.path or "").rstrip("/").lower()
    query = {
        key: values[0]
        for key, values in urllib.parse.parse_qs(
            parsed.query, keep_blank_values=True
        ).items()
    }
    return path, query


def landed_on_challenge(landed_url: str) -> Optional[str]:
    """Return ``"login"`` / ``"challenge"`` if the landed path is a wall, else None.

    URL-over-DOM: a positive result is trusted regardless of what the DOM shows.
    """
    path, _ = _split(landed_url)
    segments = _path_segments(path)
    if segments & set(_CHALLENGE_PATH_SEGMENTS):
        return "challenge"
    if segments & set(_LOGIN_PATH_SEGMENTS):
        return "login"
    return None


def diff_redirect(requested_url: str, landed_url: str) -> Optional[Tuple[str, str]]:
    """Compare requested vs landed URL; return ``(reason, detail)`` or None.

    Flags two low-noise mismatches:

    - ``("path_changed", landed_path)`` — the landed path differs from the
      requested one (after slash/case normalization).
    - ``("param_reset", "key=req->landed")`` — a query param *we explicitly
      requested* came back changed or dropped (e.g. ``start=1000`` capped to
      ``start=0``).

    Params LinkedIn *adds* (present in the landed URL but not requested —
    tracking/session noise) are deliberately ignored, so this never false-flags
    on the params we didn't set. Returns None when the landing matches.
    """
    req_path, req_query = _split(requested_url)
    land_path, land_query = _split(landed_url)

    if req_path != land_path:
        return ("path_changed", land_path or "/")

    # Only the params WE requested are checked; a param LinkedIn appended (in
    # land_query but not req_query) is ignored as tracking/session noise.
    for key, req_value in req_query.items():
        land_value = land_query.get(key)
        if land_value != req_value:
            return ("param_reset", f"{key}={req_value}->{land_value}")

    return None


async def _settle(page, settle_timeout_ms: int) -> None:
    """Let the page settle after a navigation. Best-effort, never raises.

    Prefers ``wait_for_load_state('domcontentloaded')`` (cheap, deterministic):
    when it resolves, the page has settled and we return immediately — no fixed
    sleep is added to the hot path (``navigate_guarded`` wraps every search and
    profile navigation, so an unconditional sleep would tax every visit). The
    fixed ``wait_for_timeout`` is a true *fallback*, used only when the
    load-state wait could not confirm the settle (timeout, or a mocked page) so
    a slow page still gets a settle beat.
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=settle_timeout_ms)
        return
    except Exception as exc:
        logger.debug("settle load-state wait did not confirm; using fallback: %s", exc)
    try:
        await page.wait_for_timeout(settle_timeout_ms)
    except Exception as exc:
        logger.debug("settle timeout wait skipped: %s", exc)


async def sweep_unexpected_overlay(page, *, context: Optional[dict] = None) -> bool:
    """Sweep for a visible blocking overlay the workflow did not open.

    Non-fatal: a stray interstitial/upsell/survey modal is an anomaly, not a
    stop — it is captured as an evidence bundle (rate-limited, best-effort) so a
    silent "the next click landed on a modal" regression leaves a trail. Returns
    True when an overlay was found (and captured), else False. Never raises:
    the count itself is guarded so a sweep can't derail the navigation.
    """
    try:
        count = await sel.BLOCKING_OVERLAY.count(page)
    except Exception as exc:
        logger.debug("Overlay sweep count failed: %s", exc)
        return False

    if count <= 0:
        return False

    logger.warning(
        "Unexpected blocking overlay detected after navigation (%d match(es))",
        count,
    )
    bundle_context = {"overlay_matches": count}
    if context:
        bundle_context.update(context)
    await capture_anomaly_context(
        page, "unexpected_overlay", context=bundle_context
    )
    return True


async def _raise_challenge(page, requested_url, landed_url, kind, context):
    """Capture an evidence bundle then raise the right typed exception.

    The capture is best-effort and never masks the raise, mirroring the
    selector registry's ``fail_loud`` contract.
    """
    if kind == "login":
        exc = NotAuthenticatedException(
            f"Navigation to {requested_url!r} bounced to a login wall "
            f"({landed_url!r}); session is no longer authenticated"
        )
        step = "navigation_login_wall"
    else:
        exc = CaptchaDetectedException(
            f"Navigation to {requested_url!r} bounced to a challenge "
            f"({landed_url!r}); manual verification required"
        )
        step = "navigation_challenge"

    bundle_context = {
        "requested_url": requested_url,
        "landed_url": landed_url,
        "reason": kind,
    }
    if context:
        bundle_context.update(context)
    try:
        await capture_error_context(page, step, exc=exc, context=bundle_context)
    except Exception as capture_exc:  # pragma: no cover - defensive backstop
        logger.error("Evidence capture failed for %s: %s", step, capture_exc)
    raise exc


async def _guard_landing(page, requested_url, *, strict_path, check_path, context):
    """Run the landing guard on the *current* page. Raises on a fatal mismatch.

    Order matters: the URL-over-DOM challenge/login check runs first (a bounce
    to a wall is the most consequential outcome and is trusted over any DOM
    state), then the path/param diff, then the non-fatal overlay sweep.
    """
    try:
        landed_url = str(page.url)
    except Exception:
        landed_url = ""

    # 1. Challenge / login bounce — URL over DOM, trusted unconditionally. This
    # always runs, even when ``check_path`` is off.
    kind = landed_on_challenge(landed_url)
    if kind:
        await _raise_challenge(page, requested_url, landed_url, kind, context)

    # 2. Path / requested-param diff. ``strict_path`` lets the caller assert the
    # landed path must still contain a known segment (e.g. ``/search/``) even
    # when LinkedIn rewrites the rest; an explicit strict_path miss is fatal.
    # ``check_path`` gates the *full* requested-vs-landed diff — off for the
    # per-profile nav, where vanity-URL canonicalization (``/in/john-123abc`` ->
    # ``/in/john-smith``) is a normal redirect, not a wrong landing.
    if strict_path is not None:
        land_path, _ = _split(landed_url)
        if strict_path.rstrip("/").lower() not in land_path:
            exc = UnexpectedLandingException(
                f"Navigation to {requested_url!r} did not land on the expected "
                f"path {strict_path!r} (landed {landed_url!r})",
                requested_url=requested_url,
                landed_url=landed_url,
                reason="strict_path_miss",
            )
            await _raise_mismatch(page, exc, context)

    if check_path:
        drift = diff_redirect(requested_url, landed_url)
        if drift is not None:
            reason, detail = drift
            exc = UnexpectedLandingException(
                f"Navigation to {requested_url!r} landed unexpectedly "
                f"({reason}: {detail}; landed {landed_url!r})",
                requested_url=requested_url,
                landed_url=landed_url,
                reason=reason,
            )
            await _raise_mismatch(page, exc, context)

    # 3. Non-fatal: unexpected blocking overlay before the workflow interacts.
    await sweep_unexpected_overlay(page, context=context)


async def _raise_mismatch(page, exc, context):
    """Capture an evidence bundle for an UnexpectedLandingException, then raise."""
    bundle_context = {
        "requested_url": exc.requested_url,
        "landed_url": exc.landed_url,
        "reason": exc.reason,
    }
    if context:
        bundle_context.update(context)
    try:
        await capture_error_context(
            page, "navigation_wrong_landing", exc=exc, context=bundle_context
        )
    except Exception as capture_exc:  # pragma: no cover - defensive backstop
        logger.error("Evidence capture failed for wrong-landing: %s", capture_exc)
    raise exc


async def navigate_guarded(
    page,
    url: str,
    *,
    strict_path: Optional[str] = None,
    check_path: bool = True,
    timeout: int = 30_000,
    settle_timeout_ms: int = 2_000,
    wait_until: Optional[str] = None,
    context: Optional[dict] = None,
) -> None:
    """Navigate to ``url`` and verify the browser actually landed there.

    Replaces a bare ``page.goto`` + fixed ``wait_for_timeout`` with: goto →
    settle → landing guard. On a wrong landing it captures a diagnostics
    evidence bundle and raises a typed exception.

    Args:
        page: An async Playwright ``Page``.
        url: The URL to navigate to.
        strict_path: Optional path segment that MUST still be present in the
            landed path (e.g. ``"/search/"``). A miss is a fatal
            ``UnexpectedLandingException``. ``None`` (default) skips the strict
            assertion and only runs the full requested-vs-landed diff (when
            ``check_path`` is on).
        check_path: When True (default), run the full requested-vs-landed path
            and requested-param diff. Turn off for navigations where a path
            redirect is *normal* — e.g. the per-profile nav, where LinkedIn
            canonicalizes vanity URLs. The challenge/login detection and overlay
            sweep always run regardless.
        timeout: ``page.goto`` navigation timeout (ms).
        settle_timeout_ms: Post-navigation settle budget (ms).
        wait_until: Optional ``page.goto`` ``wait_until`` value
            (e.g. ``"domcontentloaded"``); passed through when set.
        context: Optional dict merged into any evidence bundle this raises
            (e.g. ``{"campaign": name}`` / ``{"profile_url": url}``).

    Raises:
        CaptchaDetectedException: landed on a ``/checkpoint/`` / ``/authwall``.
        NotAuthenticatedException: landed on a ``/login`` / ``/uas/`` wall.
        UnexpectedLandingException: path changed or a requested param was reset.
    """
    goto_kwargs = {"timeout": timeout}
    if wait_until is not None:
        goto_kwargs["wait_until"] = wait_until
    await page.goto(url, **goto_kwargs)
    await _settle(page, settle_timeout_ms)
    await _guard_landing(
        page, url, strict_path=strict_path, check_path=check_path, context=context
    )


async def confirm_logged_in_dom(
    page,
    *,
    timeout: int = 60_000,
    context: Optional[dict] = None,
) -> None:
    """Confirm login by URL *and* a logged-in DOM landmark.

    Replaces the URL-only redirect check. First waits for the URL to leave the
    login/challenge flow (the cheap, redesign-robust signal), then waits for a
    logged-in nav landmark (``GLOBAL_NAV_ME``) so a soft block served from a
    non-login URL is not misread as "logged in". A landed challenge/login path
    raises the matching typed exception; a missing landmark within the budget
    fails loud with an evidence bundle.

    Args:
        page: An async Playwright ``Page``.
        timeout: Total budget (ms) shared across the URL wait and the
            landmark wait.
        context: Optional dict merged into any evidence bundle.

    Raises:
        CaptchaDetectedException / NotAuthenticatedException: a challenge/login
            path is still showing when the budget elapses or is detected.
        UnexpectedLandingException: the URL left the login flow but no logged-in
            landmark rendered (a soft block / unknown interstitial).
    """

    def _left_login(url) -> bool:
        return landed_on_challenge(str(url)) is None

    # One shared deadline across both waits: the URL wait and the landmark wait
    # together may not exceed ``timeout``, so a stuck login cannot silently hang
    # for 2x the budget (the manual-login path passes a 10-minute timeout).
    deadline = time.monotonic() + timeout / 1000.0

    try:
        await page.wait_for_url(_left_login, timeout=timeout)
    except PlaywrightTimeoutError:
        # Still on a wall when the budget elapsed: classify by the landed path
        # and raise the matching typed exception with evidence.
        try:
            landed_url = str(page.url)
        except Exception:
            landed_url = ""
        kind = landed_on_challenge(landed_url) or "login"
        await _raise_challenge(page, "login-confirmation", landed_url, kind, context)

    # URL left the login flow — now require the DOM landmark so a soft block on
    # a non-login URL cannot pass as authenticated. The landmark wait gets only
    # the time remaining in the shared budget (floored so a near-elapsed budget
    # still gives the landmark a real, if small, chance to render).
    remaining_ms = max(int((deadline - time.monotonic()) * 1000), 1_000)
    try:
        await page.wait_for_selector(sel.GLOBAL_NAV_ME.css, timeout=remaining_ms)
    except PlaywrightTimeoutError as exc:
        try:
            landed_url = str(page.url)
        except Exception:
            landed_url = ""
        # A challenge may have replaced the page after the URL settled; re-check.
        kind = landed_on_challenge(landed_url)
        if kind:
            await _raise_challenge(page, "login-confirmation", landed_url, kind, context)
        not_landed = UnexpectedLandingException(
            "Login URL settled but no logged-in nav landmark "
            f"({sel.GLOBAL_NAV_ME.css}) rendered; treating as not authenticated "
            f"(landed {landed_url!r})",
            requested_url="login-confirmation",
            landed_url=landed_url,
            reason="login_landmark_missing",
        )
        await _raise_mismatch(page, not_landed, context)
        raise not_landed from exc  # pragma: no cover - _raise_mismatch always raises


async def verify_listing_rendered(
    page,
    ready_selector: "sel.Selector",
    *,
    empty_selector: Optional[str] = None,
    ready_timeout_ms: int = 10_000,
    context: Optional[dict] = None,
) -> bool:
    """Disambiguate "empty listing" from "listing not rendered yet".

    Races the ready-selector against an optional empty-state selector. If the
    ready-selector appears, the listing rendered (return True). If neither
    appears within ``ready_timeout_ms``, the listing is *not rendered* (not
    necessarily empty): reload once and re-check before trusting "no results",
    and re-check for a challenge that replaced the listing.

    Args:
        page: An async Playwright ``Page``.
        ready_selector: The registry ``Selector`` whose presence means the
            listing rendered.
        empty_selector: Optional CSS for an explicit empty-state marker; when it
            wins the race, the listing rendered-but-empty (return False, no
            reload).
        ready_timeout_ms: Per-attempt wait budget (ms).
        context: Optional dict merged into any evidence bundle.

    Returns:
        True when the ready-selector rendered (the listing has content to read),
        False when the page is a genuine empty/no-results state.

    Raises:
        CaptchaDetectedException / NotAuthenticatedException: a challenge
            replaced the listing.
        SelectorNotFoundException: the listing never rendered even after the
            reload (via the registry's ``fail_loud``).
    """
    race_css = ready_selector.css
    if empty_selector:
        race_css = f"{race_css}, {empty_selector}"

    async def _attempt() -> Optional[str]:
        """Return 'ready', 'empty', or None (nothing appeared in time)."""
        try:
            await page.wait_for_selector(race_css, timeout=ready_timeout_ms)
        except PlaywrightTimeoutError:
            return None
        # Something matched the race. Prefer the ready-selector; only call it
        # empty when ONLY the empty marker is present.
        if await ready_selector.count(page) > 0:
            return "ready"
        if empty_selector and await page.locator(empty_selector).count() > 0:
            return "empty"
        return "ready"

    outcome = await _attempt()
    if outcome == "ready":
        return True
    if outcome == "empty":
        return False

    # Nothing rendered: a challenge may have replaced the listing.
    try:
        landed_url = str(page.url)
    except Exception:
        landed_url = ""
    kind = landed_on_challenge(landed_url)
    if kind:
        await _raise_challenge(
            page, "listing-verification", landed_url, kind, context
        )

    # Reload once before trusting "no results" — the listing may simply not have
    # rendered on the first paint.
    logger.warning(
        "Listing did not render within %dms; reloading once before trusting "
        "'no results'",
        ready_timeout_ms,
    )
    try:
        await page.reload(wait_until="domcontentloaded")
    except Exception as exc:
        logger.debug("Listing reload failed: %s", exc)

    # Re-check for a challenge that the reload may have surfaced.
    try:
        landed_url = str(page.url)
    except Exception:
        landed_url = ""
    kind = landed_on_challenge(landed_url)
    if kind:
        await _raise_challenge(
            page, "listing-verification", landed_url, kind, context
        )

    outcome = await _attempt()
    if outcome == "ready":
        return True
    if outcome == "empty":
        return False

    # Still nothing after a reload: the listing is genuinely missing — fail loud
    # through the registry so a layout change surfaces as a selector-missing
    # failure with an evidence bundle, rather than a silent "no results".
    await ready_selector.fail_loud(
        page, context=context, timeout=ready_timeout_ms
    )
    return False  # pragma: no cover - fail_loud never returns
