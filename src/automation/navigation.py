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

Around that gate sits a **resilience layer** (issue #17) that surfs the problems
it can safely surf and never wedges on a crashed renderer:

- **goto retry**: ``page.goto`` is retried on transient ``net::ERR_*`` failures
  with a small backoff and a bounded retry count, so a flaky resolver/connection
  on a cold start does not fail a whole run.
- **renderer-crash watchdog**: the ``goto`` is wrapped in an *outer*
  ``asyncio.wait_for``. A renderer that crashes mid-navigation detaches the CDP
  session ``goto``'s own timer is bound to, so the call would deadlock forever;
  the watchdog converts that into a crash-shaped error.
- **crash detect + recover**: a ``"crashed"`` / ``"target closed"`` /
  ``"unresponsive"`` error triggers the caller's ``recover`` callback (close +
  reopen the context, *keeping the persistent profile/cookies*) and one retry,
  so a single crash does not cascade across the worklist.
- **surf benign interstitials**: cookie banners and the email-required modal are
  auto-dismissed (via the selector registry). CAPTCHAs and security checkpoints
  are **not** surfed — those hard-stop via the landing guard above.
- **bounded per-item interaction**: ``run_bounded`` wraps each per-profile /
  per-card unit of work in an ``asyncio.wait_for`` so a crashed renderer (which
  defeats even ``locator.count()``'s missing timeout) cannot hang the run; on
  fire the browser is refreshed and the item skipped.

Three companions live here too:

- ``confirm_logged_in_dom`` — DOM-backed login confirmation (a logged-in nav
  landmark *in addition to* the URL), so a soft block on a non-login URL is not
  read as "logged in".
- ``verify_listing_rendered`` — empty-vs-not-rendered disambiguation for listing
  pages: race a ready-selector against an empty-selector and, on render timeout,
  reload once before trusting "no results" (re-checking for a challenge that
  replaced the listing).
- ``run_bounded`` — the per-item interaction watchdog described above.

Modeled on the LinkedIn Worker project's ``goto_with_retry`` / ``run_bounded`` /
``_handle_possible_crash`` and ``_guard_landing`` / ``_diff_redirect``
(``agent/src/workflows/base.py``) and ``detection.py``.

All functions are async and operate on an async Playwright ``Page``.
"""

import asyncio
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional, Tuple

sys.path.append(str(Path(__file__).parent.parent))

from playwright.async_api import Error as PlaywrightError
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

# Requested query params that are navigation *hints*, not load-bearing filters.
# We send these (e.g. ``origin=FACETED_SEARCH``), but LinkedIn routinely drops
# or rewrites them while still landing on the correct results page, so a change
# to one of these is normal URL normalization — not a wrong landing. Excluding
# them from the requested-param diff keeps the guard low-noise (the same intent
# as ignoring the params LinkedIn *adds*): only the params that actually steer
# the result set (keywords, geoUrn, industry, network, start, page) are checked.
_NON_SEMANTIC_REQUEST_PARAMS = frozenset({"origin", "sid", "trk", "trackingid"})

# --- Resilience-layer defaults (issue #17) ------------------------------------
# Defaults mirror ``AppSettings.get_navigation_settings``; the callers in
# ``linkedin.py`` pass the env-tuned values, and these keep the helpers usable
# (and testable) standalone with the same numbers.
#
# ``_DEFAULT_GOTO_TIMEOUT_MS`` — per-``page.goto`` navigation timeout.
# ``_DEFAULT_MAX_RETRIES``     — extra ``goto`` attempts on a transient net error.
# ``_DEFAULT_BACKOFF_BASE_S``  — base seconds for the inter-retry backoff.
# ``_DEFAULT_HARD_MARGIN_S``   — slack on top of the goto timeout for the outer
#                                ``asyncio`` crash watchdog.
# ``_DEFAULT_INTERACTION_WATCHDOG_S`` — hard cap for one ``run_bounded`` unit.
_DEFAULT_GOTO_TIMEOUT_MS = 30_000
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_BACKOFF_BASE_S = 3
_DEFAULT_HARD_MARGIN_S = 15
_DEFAULT_INTERACTION_WATCHDOG_S = 240

# A "recover" callback closes + reopens the browser context (keeping the
# persistent profile/cookies) and returns the fresh ``Page``. It is supplied by
# the automation engine; the navigation helpers stay page-agnostic by taking it
# as a parameter rather than reaching into ``LinkedInAutomation``.
RecoverCallback = Callable[[], Awaitable["object"]]


def _is_crash_error(exc: BaseException) -> bool:
    """True when ``exc`` looks like a renderer/browser crash or a wedged renderer.

    A crashed renderer surfaces as ``"Page crashed"`` from Playwright, a context
    that died takes the page with it (``"Target page, context or browser has
    been closed"`` / ``"target closed"`` across Playwright versions), and a
    navigation that hung against a wedged renderer is re-raised by
    ``_goto_with_retry`` as ``"... renderer unresponsive"``. These are the cases
    a context refresh can recover; a plain ``net::ERR_*`` (handled by the retry
    loop) or a typed landing exception is *not* a crash.
    """
    msg = str(exc).lower()
    return (
        "crashed" in msg
        or "target closed" in msg
        or "has been closed" in msg
        or "unresponsive" in msg
    )


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


def landed_on_checkpoint(landed_url: str) -> bool:
    """Return True if the landed path is a ``/checkpoint/`` step.

    A ``/checkpoint`` is the verification/2FA interstitial LinkedIn routes a
    *successful* login through, so it is a legitimate "login in progress" step —
    distinct from ``/authwall`` (a genuine block). The general
    ``landed_on_challenge`` collapses both into ``"challenge"`` because the
    navigation landing guard treats either as a stop; the login probe needs the
    finer distinction so it can hand a checkpoint off to the login-redirect logic
    instead of aborting it as a CAPTCHA.
    """
    return "checkpoint" in _path_segments(_split(landed_url)[0])


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
    on the params we didn't set. Non-semantic *requested* hints
    (``_NON_SEMANTIC_REQUEST_PARAMS``, e.g. ``origin``) are ignored too: LinkedIn
    normalizes them away on a perfectly valid landing, so checking them would
    abort good searches. Returns None when the landing matches.
    """
    req_path, req_query = _split(requested_url)
    land_path, land_query = _split(landed_url)

    if req_path != land_path:
        return ("path_changed", land_path or "/")

    # Only the *load-bearing* params WE requested are checked: a param LinkedIn
    # appended (in land_query but not req_query) is ignored as tracking/session
    # noise, and a requested non-semantic hint (origin/sid/trk...) is ignored as
    # normal normalization rather than a reset of a filter we depend on.
    for key, req_value in req_query.items():
        if key.lower() in _NON_SEMANTIC_REQUEST_PARAMS:
            continue
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
        exc.evidence = await capture_error_context(
            page, step, exc=exc, context=bundle_context
        )
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
        exc.evidence = await capture_error_context(
            page, "navigation_wrong_landing", exc=exc, context=bundle_context
        )
    except Exception as capture_exc:  # pragma: no cover - defensive backstop
        logger.error("Evidence capture failed for wrong-landing: %s", capture_exc)
    raise exc


async def _goto_with_retry(
    page,
    url: str,
    *,
    timeout: int,
    wait_until: Optional[str],
    max_retries: int,
    retry_backoff_base_s: int,
    hard_timeout_margin_s: int,
) -> None:
    """``page.goto`` that retries transient net errors and can't hang forever.

    Three distinct failure modes, kept distinct on purpose:

    - **Transient network error** (``net::ERR_NAME_NOT_RESOLVED`` /
      ``ERR_CONNECTION_CLOSED`` …): a flaky resolver/connection on a cold start
      loses the first ``goto`` while a manual reload succeeds. Retried up to
      ``max_retries`` times with a ``base * (attempt + 1)`` second backoff. Any
      other Playwright error (or the last attempt) propagates unchanged.
    - **Wedged renderer**: a renderer that crashes *mid-navigation* detaches the
      CDP session ``page.goto``'s own timer is bound to, so the driver timeout
      never fires and the call deadlocks forever. The ``goto`` is wrapped in an
      outer ``asyncio.wait_for`` (goto timeout + ``hard_timeout_margin_s``);
      *only* that outer fire is re-raised as a crash-shaped ``PlaywrightError``,
      so the caller's heavyweight crash recovery (context refresh) engages for a
      genuine wedge and nothing else.
    - **Slow-but-alive page** (the driver's own ``PlaywrightTimeoutError`` from
      ``goto``): the page is reachable, just slow — *not* a crash. It propagates
      unchanged so the caller treats it as an ordinary navigation timeout rather
      than tearing down and relaunching the whole browser. Folding it into the
      crash path (as a naive single ``except`` would) would force a full Chrome
      relaunch on every slow load, which is both wasteful and wrong.
    """
    hard = timeout / 1000 + hard_timeout_margin_s
    goto_kwargs = {"timeout": timeout}
    if wait_until is not None:
        goto_kwargs["wait_until"] = wait_until

    # Clamp to >= 0: a misconfigured negative retry count must still perform the
    # navigation once. Without this, range(max_retries + 1) would be range(0) for
    # max_retries == -1 and skip page.goto() entirely (returning as if navigated).
    max_retries = max(max_retries, 0)

    for attempt in range(max_retries + 1):
        try:
            await asyncio.wait_for(page.goto(url, **goto_kwargs), timeout=hard)
            return
        except asyncio.TimeoutError as exc:
            # ONLY the outer watchdog firing means the renderer is wedged: the
            # driver timeout never resolved, so the goto deadlocked. Surface it
            # as a crash so the caller refreshes the browser instead of retrying
            # on a dead page. A driver-side PlaywrightTimeoutError (slow page) is
            # deliberately NOT caught here — it falls through to propagate plain.
            raise PlaywrightError(
                f"Navigation to {url!r} hung past {hard:.0f}s — "
                "renderer unresponsive"
            ) from exc
        except PlaywrightTimeoutError:
            # The page is reachable but slow — a normal navigation timeout, not a
            # crash. Propagate unchanged; do not trigger a context refresh.
            raise
        except PlaywrightError as exc:
            if "net::ERR_" not in str(exc) or attempt == max_retries:
                raise
            delay = retry_backoff_base_s * (attempt + 1)
            logger.warning(
                "Transient navigation error (%s) — retry %d/%d in %ds",
                str(exc).splitlines()[0],
                attempt + 1,
                max_retries,
                delay,
            )
            await asyncio.sleep(delay)


# Fingerprint that *gates* surfing the generic Dismiss/Descartar button: a bare
# "Dismiss" matches many LinkedIn dialogs, so the email-required dismiss is only
# surfed when the email field that uniquely identifies that modal is present.
# Without this gate, surf would click away an unrelated dialog before the
# overlay sweep (``sweep_unexpected_overlay``) could capture it as an anomaly.
_EMAIL_MODAL_FINGERPRINT = "label[for='email']"


async def _interstitial_present(page, fingerprint: Optional[str]) -> bool:
    """Whether a gating fingerprint is on the page (non-raising). None => always."""
    if fingerprint is None:
        return True
    try:
        return await page.locator(fingerprint).count() > 0
    except Exception as exc:
        logger.debug("Interstitial fingerprint probe %r failed: %s", fingerprint, exc)
        return False


async def surf_benign_interstitials(page, *, context: Optional[dict] = None) -> bool:
    """Auto-dismiss the *safe-to-surf* interstitials (cookie banner, email modal).

    Surfing means clicking away only the overlays that are benign decoys — a
    cookie-consent banner and the "email required to connect" modal. They merely
    eat the next click; dismissing them changes nothing about the session. A
    CAPTCHA or a security checkpoint is **never** surfed here: those are handled
    by the landing guard, which hard-stops the run with a typed exception. This
    is therefore deliberately a *closed allow-list*, not a generic "close any
    modal" sweep.

    Each entry may carry a *fingerprint* that must be present before its dismiss
    button is clicked. The email-required modal's dismiss button is a bare
    ``Dismiss``/``Descartar`` that matches many unrelated dialogs, so it is gated
    on the ``label[for='email']`` field that uniquely identifies that modal —
    otherwise surf could click away (and hide) an unexpected overlay before the
    landing guard's overlay sweep records it. The cookie banner's accept control
    is specific enough to need no gate.

    Best-effort and non-fatal: a click failure is logged and skipped so surfing
    can never derail the navigation it is meant to smooth. Returns True if any
    interstitial was dismissed.

    Args:
        page: An async Playwright ``Page``.
        context: Optional dict (unused today; accepted for call-site symmetry
            with the other guard helpers).

    Returns:
        True when at least one benign interstitial was dismissed, else False.
    """
    dismissed = False
    # (dismiss-control selector, gating fingerprint or None for "always safe").
    surfable = (
        (sel.COOKIE_BANNER_DISMISS, None),
        (sel.EMAIL_REQUIRED_DISMISS, _EMAIL_MODAL_FINGERPRINT),
    )
    for selector, fingerprint in surfable:
        if not await _interstitial_present(page, fingerprint):
            continue
        try:
            handle = await selector.locate(page)
        except Exception as exc:
            logger.debug("Interstitial probe %r failed: %s", selector.name, exc)
            continue
        if handle is None:
            continue
        try:
            await handle.click()
            dismissed = True
            logger.info("Surfed benign interstitial: %s", selector.name)
        except Exception as exc:
            logger.debug("Interstitial dismiss %r failed: %s", selector.name, exc)
    return dismissed


async def navigate_guarded(
    page,
    url: str,
    *,
    strict_path: Optional[str] = None,
    check_path: bool = True,
    timeout: int = _DEFAULT_GOTO_TIMEOUT_MS,
    settle_timeout_ms: int = 2_000,
    wait_until: Optional[str] = None,
    context: Optional[dict] = None,
    recover: Optional[RecoverCallback] = None,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    retry_backoff_base_s: int = _DEFAULT_BACKOFF_BASE_S,
    hard_timeout_margin_s: int = _DEFAULT_HARD_MARGIN_S,
    surf: bool = True,
):
    """Navigate to ``url`` resiliently and verify the browser landed there.

    Replaces a bare ``page.goto`` + fixed ``wait_for_timeout`` with: resilient
    goto (transient-error retry + renderer-crash watchdog + one crash-recovery
    retry) → settle → surf benign interstitials → landing guard. On a wrong
    landing it captures a diagnostics evidence bundle and raises a typed
    exception.

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
        recover: Optional async callback that refreshes the browser context
            (close + reopen, *keeping the persistent profile/cookies*) and
            returns the fresh ``Page``. When supplied, a renderer-crash-shaped
            failure during the goto triggers exactly one recover + retry so a
            single crash does not cascade across the worklist. When ``None`` (or
            recovery itself fails) the crash error propagates.
        max_retries: Extra ``goto`` attempts on a transient ``net::ERR_*`` error.
        retry_backoff_base_s: Base seconds for the inter-retry backoff.
        hard_timeout_margin_s: Slack (s) added to the goto timeout for the outer
            renderer-crash watchdog.
        surf: When True (default), auto-dismiss benign interstitials (cookie
            banner, email-required modal) after the settle and before the guard.

    Returns:
        The ``Page`` the navigation completed on — the same ``page`` normally,
        or the fresh page from ``recover`` when a crash was recovered. Callers
        holding a page reference must rebind it to the return value.

    Raises:
        CaptchaDetectedException: landed on a ``/checkpoint/`` / ``/authwall``.
        NotAuthenticatedException: landed on a ``/login`` / ``/uas/`` wall.
        UnexpectedLandingException: path changed or a requested param was reset.
    """
    retry_kwargs = dict(
        timeout=timeout,
        wait_until=wait_until,
        max_retries=max_retries,
        retry_backoff_base_s=retry_backoff_base_s,
        hard_timeout_margin_s=hard_timeout_margin_s,
    )

    # Outer watchdog for the post-goto steps. The goto has its own inner
    # asyncio.wait_for; settle/surf/guard each issue *untimeouted* page reads
    # (surf's locator.count()/query_selector, the guard's URL/overlay reads) that
    # a wedged renderer can hang forever. Bounding them here means a crash that
    # *hangs* (not just one that raises) during the post-goto phase is converted
    # to a crash-shaped error and recovered, instead of deadlocking a caller
    # (e.g. search_profiles) that is not itself wrapped in run_bounded.
    post_goto_hard_s = settle_timeout_ms / 1000 + hard_timeout_margin_s

    async def _navigate_once(target_page):
        """goto -> settle -> surf -> guard on ``target_page``.

        The *whole* sequence is one recoverable unit: a renderer can crash not
        only mid-``goto`` but also during the settle/surf/guard that immediately
        follow (those touch the page too). Keeping them inside the recovery
        boundary means such a crash refreshes + retries rather than surfacing as
        an ordinary exception the caller misreads (e.g. ``search_profiles``
        returning an empty result set). The landing guard's *typed* exceptions
        (challenge/login/wrong-landing) are not crash-shaped, so ``_is_crash_error``
        lets them propagate unchanged — recovery never swallows a real wall.
        """
        await _goto_with_retry(target_page, url, **retry_kwargs)

        async def _post_goto():
            await _settle(target_page, settle_timeout_ms)
            if surf:
                await surf_benign_interstitials(target_page, context=context)
            await _guard_landing(
                target_page,
                url,
                strict_path=strict_path,
                check_path=check_path,
                context=context,
            )

        try:
            await asyncio.wait_for(_post_goto(), timeout=post_goto_hard_s)
        except asyncio.TimeoutError as exc:
            # A wedged renderer hung the post-goto reads. Surface it crash-shaped
            # so the outer recovery refreshes the context and retries.
            raise PlaywrightError(
                f"Post-navigation page work for {url!r} hung past "
                f"{post_goto_hard_s:.0f}s — renderer unresponsive"
            ) from exc

    try:
        await _navigate_once(page)
    except Exception as exc:
        # Only a crash-shaped failure with a recovery callback is recoverable;
        # transient net-error retries are already exhausted inside
        # _goto_with_retry, and a non-crash error (a typed landing exception, or
        # any failure when no callback was supplied) propagates so the caller's
        # typed handling still runs.
        if recover is None or not _is_crash_error(exc):
            raise
        logger.warning(
            "Renderer crash during navigation to %r (%s) — refreshing context "
            "and retrying once",
            url,
            exc,
        )
        try:
            fresh = await recover()
        except Exception as recover_exc:
            # The refresh itself could not relaunch. Preserve the ORIGINAL crash
            # as the cause (it is the real reason the navigation failed); a
            # recover error chained over it would make the caller misclassify a
            # page crash as a browser-startup/teardown failure.
            logger.error(
                "Context refresh after crash failed (%s); re-raising original "
                "crash for %r",
                recover_exc,
                url,
            )
            raise exc
        if fresh is not None:
            page = fresh
        # One full retry on the fresh page. A second crash propagates: a context
        # that crashes again right after a refresh is not something one more
        # refresh will fix, and the caller treats it as a hard nav failure.
        await _navigate_once(page)

    return page


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


async def run_bounded(
    awaitable: Awaitable,
    *,
    timeout_s: float = _DEFAULT_INTERACTION_WATCHDOG_S,
    recover: Optional[RecoverCallback] = None,
    label: str = "unit",
):
    """Run one unit of page interaction under a hard watchdog. Returns its result.

    A crashed renderer defeats Playwright's per-operation timeouts — even
    ``locator.count()`` deadlocks, because it carries no timeout — so any
    unbounded sequence of page calls for one item (a profile visit, a result
    card) can wedge the run indefinitely. Wrap that whole unit in a single
    coroutine and pass it here: on timeout the wedged browser is refreshed (via
    ``recover``, keeping the persistent profile) and ``asyncio.TimeoutError`` is
    re-raised so the caller can skip *this* item and keep the rest of the
    worklist alive.

    A unit that ran long but did not time out is surfaced with a WARNING (a
    genuinely slow page and early memory pressure look alike, so the duration is
    made visible) — but is not interfered with.

    Args:
        awaitable: A single coroutine doing all of one item's page interaction.
        timeout_s: Hard cap (seconds) for the unit.
        recover: Optional async callback that refreshes the browser context
            (returning the fresh page) when the unit times out. The refresh
            result is *not* returned here — the caller skips the timed-out item
            and rebinds its page on the next navigation's ``recover`` — so a
            wedged unit cannot also poison the next one.
        label: Short name used in the watchdog log lines.

    Returns:
        The awaited result of ``awaitable`` on success.

    Raises:
        asyncio.TimeoutError: the unit exceeded ``timeout_s`` (after refresh).
    """
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout_s)
    except asyncio.TimeoutError:
        logger.warning(
            "%s wedged the renderer (>%.0fs) — refreshing browser",
            label,
            timeout_s,
        )
        if recover is not None:
            try:
                await recover()
            except Exception as exc:
                logger.error("Browser refresh after wedge failed: %s", exc)
        raise
    elapsed = time.monotonic() - start
    if elapsed > timeout_s / 2:
        logger.warning(
            "Slow %s took %.0fs (budget %.0fs) — watch for memory pressure",
            label,
            elapsed,
            timeout_s,
        )
    return result
