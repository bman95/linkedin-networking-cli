import asyncio
import random
import subprocess
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone, date
from pathlib import Path
from typing import List, Optional, Dict, Any, Callable
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
    Playwright,
    TimeoutError,
)
from dataclasses import dataclass

import sys
from pathlib import Path
import psutil

sys.path.append(str(Path(__file__).parent.parent))

from database.models import Campaign, Contact
from database.operations import DatabaseManager
from config.settings import AppSettings
from automation.linkedin_mappings import format_ids_for_url
from automation.interactions import (
    random_wait,
    _is_true_limit,
    human_type,
    move_to_element,
    move_to_and_click,
    scroll_down,
    dwell,
    RateLimiter,
)
from automation.diagnostics import (
    capture_anomaly_context,
    capture_error_context,
    snapshot_page,
    reset_diagnostics_run,
)
from automation.navigation import (
    navigate_guarded,
    confirm_logged_in_dom,
    verify_listing_rendered,
    landed_on_challenge,
    landed_on_checkpoint,
    run_bounded,
    _is_crash_error,
)
from automation import selectors as sel
from utils.logging import get_logger
from exceptions import (
    NotAuthenticatedException,
    LoginFailedException,
    SelectorNotFoundException,
    CaptchaDetectedException,
    UnexpectedLandingException,
)


logger = get_logger(__name__)


# Passive automation hardening: drop the two most obvious "this is a bot"
# tells that page JS can read for free. Scope is deliberately narrow — no
# canvas/WebGL/audio fingerprint spoofing (synthetic noise creates
# detectable inconsistencies on real Chrome).
#
# 1. Disables the AutomationControlled blink feature, which otherwise sets
#    navigator.webdriver = true and advertises automation to detectors.
AUTOMATION_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
# 2. Belt-and-braces: mask navigator.webdriver before any page script runs,
#    in case the flag is still readable on a given Chrome build.
WEBDRIVER_MASK_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
)


def force_close_chrome() -> None:
    """Close Chrome processes forcefully before launching Playwright."""
    try:
        # Windows: Kill chrome.exe processes
        subprocess.run(
            ["taskkill", "/f", "/im", "chrome.exe"], capture_output=True, check=False
        )

        # Also kill any remaining Chrome processes
        for proc in psutil.process_iter(["pid", "name"]):
            if proc.info.get("name", "").lower().startswith("chrome"):
                try:
                    proc.kill()
                    logger.debug("Killed Chrome process %d", proc.pid)
                except psutil.NoSuchProcess:
                    pass
    except Exception as exc:
        logger.warning("Error killing Chrome processes: %s", exc)

    # Wait for processes to close
    time.sleep(2)


@dataclass
class LinkedInProfile:
    """Data class for LinkedIn profile information"""

    name: str
    profile_url: str
    headline: Optional[str] = None
    location: Optional[str] = None
    company: Optional[str] = None
    mutual_connections: int = 0


class LinkedInAutomation:
    """LinkedIn automation engine for networking campaigns"""

    BASE_URL = "https://www.linkedin.com"
    SEARCH_URL = f"{BASE_URL}/search/results/people/"

    def __init__(self, db_manager: DatabaseManager, settings: AppSettings):
        self.db_manager = db_manager
        self.settings = settings
        self.playwright: Optional[Playwright] = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.is_authenticated = False
        # Sliding-window per-minute action cap, built lazily from settings so
        # an env override applied after construction still takes effect.
        self._rate_limiter: Optional[RateLimiter] = None

    def _get_rate_limiter(self) -> RateLimiter:
        """Return the action rate limiter, building it from settings once."""
        if self._rate_limiter is None:
            cap = self.settings.get_automation_settings()["max_actions_per_minute"]
            self._rate_limiter = RateLimiter(max_per_minute=cap)
        return self._rate_limiter

    async def _throttle_action(self) -> None:
        """Enforce the sliding-window per-minute action cap before an action."""
        await self._get_rate_limiter().acquire()

    async def _dwell(self) -> None:
        """Probabilistic reading/dwell pause between major actions."""
        auto = self.settings.get_automation_settings()
        await dwell(
            self.page,
            min_s=auto["action_delay_min"],
            max_s=auto["action_delay_max"],
        )

    async def _refresh_context(self) -> Page:
        """Close and reopen the browser context, keeping the persistent profile.

        Recovery primitive for the resilient-navigation layer (issue #17): when
        a renderer crashes or a per-item watchdog fires, the wedged context is
        torn down and a fresh one launched so one crash does not cascade across
        the rest of the worklist. Login state survives because the persistent
        Chrome profile on disk (and the ``session.json`` ``close_browser``
        writes for the transient path) carries the cookies — ``start_browser``
        re-reads them, so the refreshed context resumes the same session.

        The teardown is bounded by an ``asyncio`` watchdog: on a frozen,
        memory-thrashing renderer an individual ``close`` can hang, which would
        otherwise wedge the very refresh meant to recover from it. On timeout (or
        any close error) the partial handles are dropped and ``start_browser``
        relaunches from a clean slate.

        Returns the fresh ``Page``.
        """
        logger.warning("Refreshing browser context to recover from a wedged renderer")
        nav = self.settings.get_navigation_settings()
        close_budget = nav["hard_timeout_margin_s"]
        try:
            await asyncio.wait_for(self.close_browser(), timeout=close_budget)
        except Exception as exc:
            logger.warning("Error closing context during refresh: %s", exc)
        finally:
            # Drop any partial handles so start_browser launches from scratch
            # rather than reusing a half-dead context.
            self.context = None
            self.browser = None
            self.page = None
            self.playwright = None

        await self.start_browser()
        return self.page

    async def _recover(self) -> Page:
        """The ``recover`` callback handed to the navigation helpers.

        Thin wrapper around :meth:`_refresh_context` so the navigation layer
        stays page-agnostic (it receives a zero-arg async callable returning the
        fresh page) without importing the automation engine.
        """
        return await self._refresh_context()

    def _nav_kwargs(self) -> Dict[str, Any]:
        """Resilient-navigation kwargs shared by every ``navigate_guarded`` call.

        Bundles the env-tuned retry/watchdog tunables plus the crash-recovery
        callback so each call site stays a single readable statement and the two
        navigations cannot drift apart in how they retry/recover.
        """
        nav = self.settings.get_navigation_settings()
        return {
            "timeout": nav["goto_timeout_ms"],
            "max_retries": nav["max_retries"],
            "retry_backoff_base_s": nav["retry_backoff_base_s"],
            "hard_timeout_margin_s": nav["hard_timeout_margin_s"],
            "recover": self._recover,
        }

    async def __aenter__(self):
        """Async context manager entry"""
        await self.start_browser()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Async context manager exit"""
        await self.close_browser()

    async def start_browser(self):
        """Initialize Playwright browser with enhanced session management.

        Session persistence relies on two complementary mechanisms, chosen by
        how the browser is launched:

        - **Persistent context** (``user_data_dir``): used when a real Chrome
          install is configured (custom executable or the ``chrome`` channel)
          and the persistent launch succeeds. ``launch_persistent_context``
          reuses the on-disk Chrome profile under
          ``~/.linkedin-networking-cli/browser_data/``, so cookies and login
          state live inside that profile. This is the default/primary path.
        - **storage_state JSON** (``session.json``): used on the transient
          (non-persistent) launch path — i.e. when no real Chrome is configured,
          a non-``chrome`` channel is used, or the persistent launch above
          fails. Only on this path is ``session.json`` *loaded* into the new
          context so auth survives across runs without a persistent profile.

        Read is exclusive — exactly one mechanism is *loaded* per run: the
        persistent profile when present, otherwise ``session.json``. Writing is
        not exclusive: ``close_browser`` and ``login`` always write
        ``session.json`` for whatever context is active (persistent included),
        so a later transient run can resume the session a persistent run
        established.
        """
        # Force close any existing Chrome processes
        force_close_chrome()

        self.playwright = await async_playwright().start()
        browser_settings = self.settings.get_browser_settings()

        launch_kwargs: Dict[str, Any] = {
            "headless": browser_settings["headless"],
            "timeout": 60_000,  # Increased timeout
            "args": list(AUTOMATION_LAUNCH_ARGS),
        }

        browser_executable = browser_settings.get("executable_path")
        browser_channel = browser_settings.get("channel")
        user_data_dir = browser_settings.get("user_data_dir")

        # Context options shared by every launch path (persistent and
        # transient) so the resulting page exposes one coherent fingerprint:
        # viewport, locale and timezone always agree, regardless of which path
        # wins. timezone_id is included only when the host zone could be
        # resolved — otherwise it is left to the browser's own host default
        # rather than forcing an incoherent UTC. user_agent is likewise included
        # only when explicitly overridden; the default leaves real Chrome's own
        # (already-consistent) UA untouched.
        context_options: Dict[str, Any] = {
            "viewport": browser_settings["viewport"],
            "locale": browser_settings["locale"],
        }
        if browser_settings.get("timezone_id"):
            context_options["timezone_id"] = browser_settings["timezone_id"]
        if browser_settings.get("user_agent"):
            context_options["user_agent"] = browser_settings["user_agent"]

        if user_data_dir:
            user_data_path = Path(user_data_dir)
            user_data_path.mkdir(parents=True, exist_ok=True)

            # Check if profile directory exists
            if user_data_path.exists():
                logger.info(f"Profile directory found: {user_data_path}")
            else:
                logger.warning("Profile directory not found, using a temporary one.")
                user_data_dir = None

        if browser_executable:
            launch_kwargs["executable_path"] = browser_executable
            logger.info("Launching Chrome using executable at %s", browser_executable)
        elif browser_channel:
            launch_kwargs["channel"] = browser_channel
            logger.info("Launching Chrome via Playwright channel '%s'", browser_channel)
        else:
            logger.info("Launching default Playwright Chromium browser")

        use_persistent = bool(
            browser_executable
            or (browser_channel and browser_channel.lower() == "chrome")
        )

        if use_persistent and user_data_dir:
            persistent_kwargs = launch_kwargs.copy()
            persistent_kwargs.update(context_options)
            logger.info("Using persistent context with user data dir %s", user_data_dir)
            try:
                logger.info("Launching persistent Chrome…")
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    **persistent_kwargs,
                )
                self.browser = self.context.browser
                # Register the webdriver mask before touching any page: a
                # persistent context opens with a page already loaded, and an
                # init script only applies to documents created or navigated
                # after registration.
                await self.context.add_init_script(WEBDRIVER_MASK_SCRIPT)
                # Persistent context already opens a page; reuse it instead
                # of creating a second tab.
                if self.context.pages:
                    self.page = self.context.pages[0]
                    logger.info("Using existing page from persistent context")
                    # The reused page's current document loaded before the
                    # init script was registered, so reload it so the mask
                    # applies before this page navigates to LinkedIn.
                    try:
                        await self.page.reload(wait_until="domcontentloaded")
                    except Exception as reload_error:
                        logger.debug(
                            "Could not reload reused persistent page: %s", reload_error
                        )
            except Exception as persistent_error:
                logger.exception(
                    "Failed persistent context, falling back to transient browser…"
                )
                # Close the half-built context (if any) so the partial Chrome
                # instance is not leaked, then discard it so the transient
                # fallback below engages and registers the mask on a fresh
                # context.
                if self.context:
                    try:
                        await self.context.close()
                    except Exception as close_error:
                        logger.debug(
                            "Could not close partial persistent context: %s",
                            close_error,
                        )
                self.context = None
                self.browser = None
                self.page = None
                use_persistent = False

        if not self.context:
            try:
                self.browser = await self.playwright.chromium.launch(**launch_kwargs)
            except Exception as launch_error:
                if browser_channel and "channel" in launch_kwargs:
                    logger.warning(
                        "Falling back to bundled Chromium after Chrome launch failed (%s)",
                        launch_error,
                    )
                    self.browser = await self.playwright.chromium.launch(
                        headless=browser_settings["headless"],
                        args=list(AUTOMATION_LAUNCH_ARGS),
                    )
                else:
                    raise

            # Try to load existing session
            session_path = self.settings.session_path
            if session_path.exists():
                try:
                    self.context = await self.browser.new_context(
                        storage_state=str(session_path),
                        **context_options,
                    )
                    logger.info("Loaded existing LinkedIn session")
                except Exception as session_error:
                    logger.warning("Failed to load session state: %s", session_error)
                    self.context = await self.browser.new_context(**context_options)
                    logger.info("Starting fresh LinkedIn session")
            else:
                self.context = await self.browser.new_context(**context_options)
                logger.info("Starting fresh LinkedIn session")

            # Mask navigator.webdriver before the page is created, so it runs
            # before any page script on every navigation. This non-persistent
            # context has no page yet (one is created below).
            await self.context.add_init_script(WEBDRIVER_MASK_SCRIPT)

        if self.page is None:
            self.page = await self.context.new_page()
            logger.info("Created new page for browser context")

    async def close_browser(self):
        """Close browser and cleanup.

        Every step is independently best-effort. ``_refresh_context`` calls this
        specifically against *crashed/half-closed* objects, where the snapshot,
        ``context.close``, ``browser.close`` or ``playwright.stop`` can each
        throw. An unguarded throw on any of them would skip the *later* steps and
        orphan the still-running Chrome/Playwright node driver, so a repeated
        crash-recovery refresh would leak a process per crash. Guarding each step
        keeps the teardown total — every handle gets a close attempt, and
        ``stop`` (which frees the driver subprocess) always runs.
        """
        if self.context:
            # Save session state (best-effort — a dead context can't snapshot).
            try:
                await self.context.storage_state(
                    path=str(self.settings.session_path)
                )
            except Exception as exc:
                logger.debug("Could not save storage_state on close: %s", exc)
            try:
                await self.context.close()
            except Exception as exc:
                logger.debug("Could not close context: %s", exc)
        if self.browser:
            try:
                await self.browser.close()
            except Exception as exc:
                logger.debug("Could not close browser: %s", exc)
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as exc:
                logger.debug("Could not stop playwright: %s", exc)

    async def login(self, progress_callback: Optional[Callable] = None) -> bool:
        """Login to LinkedIn with enhanced session detection"""
        try:
            if progress_callback:
                progress_callback("Checking LinkedIn session...")

            # Check if already logged in by attempting to access feed.
            # A bounce to a /login wall here is the *expected* "need to
            # authenticate" path, so this probe uses a bare goto (not
            # navigate_guarded, which would raise on the login bounce).
            await self.page.goto(f"{self.BASE_URL}/feed", timeout=30_000, wait_until="domcontentloaded")
            # Give a moment for redirect to happen if not logged in
            await self.page.wait_for_timeout(2000)

            current_url = self.page.url

            wall = landed_on_challenge(current_url)
            checkpoint_in_progress = landed_on_checkpoint(current_url)
            # A /checkpoint landing is LinkedIn's routine login verification/2FA
            # step, which the existing _wait_for_login_redirect flow already
            # EXPECTS during a successful login — so it must NOT be aborted here
            # as a CAPTCHA. Defer it to the login-redirect logic below (without
            # re-routing to /login, which would discard the verification step).
            # A non-checkpoint challenge (/authwall) is a genuine block, so it
            # still surfaces as a typed exception with evidence.
            if wall == "challenge" and not checkpoint_in_progress:
                challenge_exc = CaptchaDetectedException(
                    "Stored session challenged on feed probe "
                    f"({current_url!r}); manual verification required"
                )
                await capture_error_context(
                    self.page,
                    "login_feed_probe_challenge",
                    exc=challenge_exc,
                    context={"landed_url": current_url},
                )
                raise challenge_exc

            # DOM-backed "already logged in?" check: not on a login wall AND the
            # logged-in nav landmark renders. The URL alone is not enough — a soft
            # block served from a non-login URL would pass a URL-only check but
            # renders no nav landmark. A /checkpoint in progress is skipped here:
            # the landmark cannot render on a verification page, so it is handed
            # straight to _wait_for_login_redirect below.
            if wall is None:
                # Give the logged-in landmark a generous budget: a valid but slow
                # feed can take longer than a couple of seconds to paint the nav,
                # and a too-tight wait would misread it as logged out and re-drive
                # a healthy session through /login (which can corrupt it). On a
                # timeout, fall back to a direct count before concluding "not
                # logged in" — the landmark may have rendered just past the wait.
                landmark_present = False
                try:
                    await self.page.wait_for_selector(
                        sel.GLOBAL_NAV_ME.css, timeout=20_000
                    )
                    landmark_present = True
                except TimeoutError:
                    try:
                        landmark_present = await sel.GLOBAL_NAV_ME.count(self.page) > 0
                    except Exception as count_error:
                        logger.debug(
                            "Logged-in landmark fallback count failed: %s",
                            count_error,
                        )
                if landmark_present:
                    self.is_authenticated = True
                    if progress_callback:
                        progress_callback("Session already active on LinkedIn!")
                    return True

            # We were redirected to login (or the feed never confirmed a session),
            # proceed with authentication.
            if progress_callback:
                if checkpoint_in_progress:
                    progress_callback(
                        "Login verification in progress, awaiting confirmation..."
                    )
                else:
                    progress_callback("Not logged in, proceeding with login...")

            # A /checkpoint verification step is already mid-login: do NOT route
            # back to /login (that discards it) or re-enter credentials. Hand it
            # straight to the redirect-confirmation logic, which waits for the
            # URL to leave the login/challenge flow and a logged-in landmark.
            if checkpoint_in_progress:
                # A checkpoint needs a human to complete the verification (2FA
                # code / approval) in a visible browser. Headless has no window
                # to do that in, so fail fast with an actionable error instead of
                # blocking a CI/background run for the full 10-minute wait.
                if self.settings.get_browser_settings().get("headless"):
                    raise LoginFailedException(
                        "Login verification (/checkpoint) requires manual "
                        "completion, but the browser is headless. Run with "
                        "HEADLESS=0 to complete the verification."
                    )
                await self._wait_for_login_redirect(timeout_ms=600_000)
                self.is_authenticated = True
                if progress_callback:
                    progress_callback("Login completed successfully!")
                try:
                    await self.context.storage_state(
                        path=str(self.settings.session_path)
                    )
                    logger.info("Session state saved successfully")
                except Exception as save_error:
                    logger.warning("Failed to save session state: %s", save_error)
                return True

            # Ensure we're on the login page
            if "/login" not in current_url:
                await self.page.goto(f"{self.BASE_URL}/login", timeout=30000)

            # Check for CAPTCHA on login page
            from .interactions import detect_captcha
            if await detect_captcha(self.page):
                raise CaptchaDetectedException("CAPTCHA challenge detected on login page - manual verification required")

            # Handle login with or without stored credentials
            email = self.settings.linkedin_email
            password = self.settings.linkedin_password

            if email and password:
                if progress_callback:
                    progress_callback("Entering credentials...")

                # Type credentials character-by-character (with a short focus
                # pause and randomized per-key delay) instead of an instant
                # fill, which reads as scripted. Field targets come from the
                # central selector registry so they survive SDUI churn.
                auto = self.settings.get_automation_settings()
                typing_min = auto["typing_delay_min"]
                typing_max = auto["typing_delay_max"]
                await human_type(
                    self.page.locator(sel.LOGIN_USERNAME.css),
                    email,
                    delay_min=typing_min,
                    delay_max=typing_max,
                )
                await human_type(
                    self.page.locator(sel.LOGIN_PASSWORD.css),
                    password,
                    delay_min=typing_min,
                    delay_max=typing_max,
                )

                # Submit login with a natural mouse move to the button first.
                submit_button = self.page.locator(sel.LOGIN_SUBMIT.css).first
                await move_to_and_click(self.page, submit_button)

                # Wait a moment for the page to respond
                await self.page.wait_for_timeout(2000)

                # Check for CAPTCHA after login submission
                if await detect_captcha(self.page):
                    raise CaptchaDetectedException("CAPTCHA challenge detected after login submission")

                # Wait for login success (2FA may add a checkpoint step)
                if progress_callback:
                    progress_callback("Waiting for login confirmation...")

                await self._wait_for_login_redirect(timeout_ms=60_000)
            else:
                # Manual login needs a visible browser window.
                if self.settings.get_browser_settings().get("headless"):
                    raise LoginFailedException(
                        "No credentials configured and the browser is headless, so "
                        "manual login is impossible. Set LINKEDIN_EMAIL and "
                        "LINKEDIN_PASSWORD, or run with HEADLESS=0."
                    )

                if progress_callback:
                    progress_callback(
                        "No credentials configured. Complete the login manually in the Chrome window."
                    )

                try:
                    await self._wait_for_login_redirect(timeout_ms=600_000)
                except (
                    CaptchaDetectedException,
                    NotAuthenticatedException,
                    UnexpectedLandingException,
                ):
                    # A challenge/login wall (or a soft block with no nav
                    # landmark) is NOT an ordinary timeout: let the typed signal
                    # propagate so the caller can stop to protect the account
                    # rather than reading it as "manual login timed out".
                    raise
                except Exception as wait_error:
                    logger.error(f"Manual login timed out: {wait_error}")
                    if progress_callback:
                        progress_callback("Manual login timed out before confirmation.")
                    raise LoginFailedException(f"Manual login timed out: {wait_error}")

            self.is_authenticated = True
            if progress_callback:
                progress_callback("Login completed successfully!")

            # Save session state
            try:
                await self.context.storage_state(path=str(self.settings.session_path))
                logger.info("Session state saved successfully")
            except Exception as save_error:
                logger.warning("Failed to save session state: %s", save_error)

            return True

        except LoginFailedException:
            raise  # Re-raise login failed exceptions
        except (
            CaptchaDetectedException,
            NotAuthenticatedException,
            UnexpectedLandingException,
        ):
            # Surface a challenge/auth wall (or a soft block that left the login
            # URL but never rendered the logged-in nav landmark) as its typed
            # self, not a generic LoginFailedException, so callers can stop to
            # protect the account rather than retrying credentials into a wall.
            # This outer handler also covers the credentials path, whose
            # _wait_for_login_redirect call has no inner guard.
            raise
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Login failed: {str(e)}")
            raise LoginFailedException(f"Login failed: {str(e)}")

    async def _wait_for_login_redirect(self, timeout_ms: int) -> None:
        """Confirm login by URL leaving the login flow *and* a DOM landmark.

        Delegates to ``confirm_logged_in_dom``: the URL leaving the
        login/challenge flow is the cheap, redesign-robust first signal, but it
        is no longer sufficient on its own — a soft block served from a
        non-login URL would pass a URL-only check. A logged-in nav landmark
        (``GLOBAL_NAV_ME``) must also render, so the confirmation is DOM-backed.
        Raises a typed exception (with an evidence bundle) on a challenge/login
        bounce or a missing landmark.
        """
        await confirm_logged_in_dom(self.page, timeout=timeout_ms)

    async def search_profiles(
        self,
        campaign: Campaign,
        limit: int = 100,
        progress_callback: Optional[Callable] = None,
    ) -> List[LinkedInProfile]:
        """Search for LinkedIn profiles based on campaign criteria"""

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        profiles = []
        # New run boundary: clear per-run diagnostics state (anomaly rate-limit
        # counter + page-snapshot ring) so a long-lived CLI process neither
        # leaks the counter nor mixes ring-buffer evidence across campaigns.
        reset_diagnostics_run()

        try:
            # Build search URL
            search_params = self._build_search_params(campaign)
            search_url = f"{self.SEARCH_URL}?{search_params}"

            if progress_callback:
                progress_callback("Starting profile search...")

            # Guarded navigation: resilient goto (transient-error retry +
            # renderer-crash watchdog + one crash-recovery refresh) -> settle ->
            # surf benign interstitials -> landing guard. A bounce to a
            # challenge/login wall or a wrong path raises a typed exception with
            # an evidence bundle; ``strict_path`` asserts we are still on the
            # people-search results path even when LinkedIn rewrites the rest of
            # the URL. navigate_guarded returns the page it finished on (a fresh
            # one if a crash was recovered), so rebind self.page. The humanized
            # scroll below still runs after this returns.
            self.page = await navigate_guarded(
                self.page,
                search_url,
                strict_path="/search/results/people",
                context={"campaign": campaign.name},
                **self._nav_kwargs(),
            )
            # Disambiguate "empty" from "not rendered yet": race the readiness
            # selector against the explicit no-results marker and, if neither
            # renders, reload ONCE before trusting "no results" (re-checking for
            # a challenge that replaced the listing). Only a still-missing listing
            # after the reload fails loud through the registry (evidence bundle +
            # raise). A genuine empty/no-results page returns False — return an
            # empty list cleanly rather than entering the harvest loop (which
            # would time out on the result-cards wait and misreport a failure).
            rendered = await verify_listing_rendered(
                self.page,
                sel.SEARCH_RESULTS_READY,
                empty_selector=sel.SEARCH_NO_RESULTS.css,
                ready_timeout_ms=15000,
                context={"campaign": campaign.name},
            )
            if not rendered:
                logger.info("Search returned no results for campaign %r", campaign.name)
                if progress_callback:
                    progress_callback("Search complete! Found 0 profiles (no results)")
                return profiles

            page_count = 0
            max_pages = 10  # Limit to prevent infinite loops

            while len(profiles) < limit and page_count < max_pages:
                page_count += 1

                if progress_callback:
                    progress_callback(
                        f"Scanning page {page_count}... Found {len(profiles)} profiles"
                    )

                # Wait for profiles to load (legacy attribute or SDUI links)
                try:
                    await self.page.wait_for_selector(
                        sel.SEARCH_RESULT_CARDS.css,
                        timeout=10000,
                    )
                except TimeoutError:
                    raise SelectorNotFoundException(
                        "Profile elements not found on search results page",
                        selector=sel.SEARCH_RESULT_CARDS.css,
                        timeout=10000
                    )

                # Record the landed search page into the rolling ring buffer so
                # a later failure can be traced back through how we got here.
                await snapshot_page(self.page, page_count - 1)

                profiles_before_page = len(profiles)

                # Scroll the results like a human before harvesting them, so the
                # page isn't read instantly the way a scraper would.
                await scroll_down(self.page)

                # Legacy UI: structured result elements with a stable attribute
                # (the result-cards selector's stable anchor).
                profile_elements = await self.page.query_selector_all(
                    sel.SEARCH_RESULT_CARDS.anchor
                )

                if profile_elements:
                    for element in profile_elements:
                        try:
                            profile = await self._extract_profile_info(element)
                            if profile and len(profiles) < limit:
                                profiles.append(profile)
                        except Exception as e:
                            logger.warning(f"Failed to extract profile info: {e}")
                            continue
                else:
                    # SDUI layout (2026): extract result cards in one JS pass
                    seen_urls = {p.profile_url for p in profiles}
                    for profile in await self._extract_profiles_new_ui():
                        if len(profiles) >= limit:
                            break
                        if profile.profile_url not in seen_urls:
                            profiles.append(profile)
                            seen_urls.add(profile.profile_url)

                # The readiness selector matched but neither extraction strategy
                # yielded a profile: a "weird but survivable" SDUI-drift state.
                # Capture an anomaly bundle (rate-limited, best-effort) so a
                # silent extraction regression leaves evidence.
                if len(profiles) == profiles_before_page:
                    await capture_anomaly_context(
                        self.page,
                        "search_page_no_profiles_extracted",
                        context={"campaign": campaign.name, "page": page_count},
                    )

                # Check for next page. Absence is the normal end-of-results
                # signal, so this is a non-required locate (no fail-loud); the
                # registry handles the EN/ES aria-label → SDUI text fallback
                # ordering and warns if it drifts off the primary.
                next_button = await sel.PAGINATION_NEXT.locate(self.page)
                if next_button and not await next_button.is_disabled():
                    await next_button.scroll_into_view_if_needed()
                    # Natural mouse move to the pagination control before clicking.
                    await self._throttle_action()
                    await move_to_and_click(self.page, next_button, click_timeout=10_000)
                    await self.page.wait_for_timeout(3000)  # Wait for page load
                else:
                    break

            if progress_callback:
                progress_callback(f"Search complete! Found {len(profiles)} profiles")

            return profiles

        except (
            CaptchaDetectedException,
            NotAuthenticatedException,
            UnexpectedLandingException,
        ) as challenge:
            # The navigation guard bounced the search to a challenge/login wall,
            # or it landed on the wrong path / LinkedIn reset a requested param
            # (UnexpectedLandingException). Evidence is already captured. This
            # must NOT be swallowed into an empty result list: a walled or
            # wrong-landed session read as "no profiles" would both misreport to
            # the user and let the caller drive send_connection_requests straight
            # through the wall. Re-raise so the run stops loudly, mirroring the
            # per-profile guard's break.
            logger.warning("Search hit a challenge/wrong landing; aborting: %s", challenge)
            if progress_callback:
                progress_callback(
                    "⚠️ Challenge or wrong landing detected during search — "
                    "stopping to protect the account"
                )
            raise
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Search failed: {str(e)}")
            return profiles

    async def send_connection_requests(
        self,
        campaign: Campaign,
        profiles: List[LinkedInProfile],
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, int]:
        """Send connection requests to profiles"""

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        from .interactions import detect_captcha

        automation_settings = self.settings.get_automation_settings()
        daily_limit = automation_settings["daily_connection_limit"]
        sent_count = 0
        failed_count = 0
        existing_count = 0

        # Persisted, restart-safe daily cap. The count is keyed by the local
        # day, so quitting and reopening the CLI cannot blow past the limit,
        # and the counter self-clears when a new local day begins.
        today = date.today().isoformat()
        already_sent_today = self.db_manager.get_daily_connection_count(today)

        # Optional inter-session cooldown: if a previous run sent a request
        # within the configured window, warn the user before continuing.
        cooldown_seconds = automation_settings.get("connection_cooldown", 0)
        if cooldown_seconds > 0:
            last_action_at = self.db_manager.get_last_connection_at()
            if last_action_at is not None:
                if last_action_at.tzinfo is None:
                    last_action_at = last_action_at.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - last_action_at).total_seconds()
                if elapsed < cooldown_seconds:
                    remaining = int(cooldown_seconds - elapsed)
                    logger.warning(
                        "Inter-session cooldown active: last request %ds ago, "
                        "cooldown is %ds (%ds remaining)",
                        int(elapsed),
                        cooldown_seconds,
                        remaining,
                    )
                    if progress_callback:
                        progress_callback(
                            f"⚠️ Cooldown active — last connection {int(elapsed)}s ago; "
                            f"wait {remaining}s before the next run"
                        )

        # Stop immediately if the persisted daily cap was already reached on a
        # prior run today.
        if already_sent_today >= daily_limit:
            logger.info(
                "Daily connection limit already reached (%d/%d) before this run",
                already_sent_today,
                daily_limit,
            )
            if progress_callback:
                progress_callback(
                    f"Daily connection limit already reached "
                    f"({already_sent_today}/{daily_limit} used today)"
                )
            self.db_manager.update_campaign_stats(campaign.id)
            return {
                "sent": 0,
                "failed": 0,
                "existing": 0,
                "total_processed": 0,
            }

        # Backoff state: repeated failures may signal a restricted account.
        consecutive_failures = 0
        backoff_base_seconds = 5
        backoff_cap_seconds = 300

        for i, profile in enumerate(profiles):
            # Tracks whether THIS iteration has claimed a daily slot, so any
            # path that doesn't end in a confirmed send can give it back.
            slot_reserved = False
            today = date.today().isoformat()
            try:
                # Recompute the local-day key each iteration so a run that
                # crosses midnight starts a fresh bucket. The actual slot is
                # claimed atomically just before sending (reserve-before-send),
                # so this is only a cheap early stop, not the enforcement point.
                if self.db_manager.get_daily_connection_count(today) >= daily_limit:
                    if progress_callback:
                        progress_callback(
                            f"Daily connection limit reached "
                            f"({daily_limit}/{daily_limit} used today)"
                        )
                    break

                if progress_callback:
                    progress_callback(
                        f"Processing {profile.name} ({i + 1}/{len(profiles)})"
                    )

                # Check if contact already exists
                with self.db_manager.get_session() as session:
                    from sqlmodel import select

                    existing_contact = session.exec(
                        select(Contact).where(
                            Contact.profile_url == profile.profile_url
                        )
                    ).first()

                if existing_contact:
                    existing_count += 1
                    continue

                # Navigate to the profile and read it — all under ONE per-item
                # interaction watchdog (run_bounded). The whole read sequence
                # (guarded navigation, the DOM-level CAPTCHA check, the humanized
                # scroll/dwell, and the connect-control lookup) consists of
                # untimeouted page calls — a crashed renderer defeats even
                # locator.count(), so any of them can hang forever. Bounding the
                # whole unit means a wedge at any point caps the item, refreshes
                # the browser, and re-raises TimeoutError so this profile is
                # skipped (caught below) without wedging the rest of the worklist.
                #
                # navigate_guarded itself is guarded + resilient (transient-error
                # retry + renderer-crash watchdog + one crash-recovery refresh,
                # then goto -> settle -> surf -> landing guard). check_path is OFF
                # because LinkedIn canonicalizes vanity profile URLs (a normal
                # redirect). A challenge/login bounce raises a typed exception
                # (caught below). On a recovered crash navigate_guarded returns a
                # fresh page, and the connect-control lookup uses self.page, so
                # the helper rebinds self.page before reading it.
                async def _navigate_and_read():
                    self.page = await navigate_guarded(
                        self.page,
                        profile.profile_url,
                        check_path=False,
                        context={"profile_url": profile.profile_url},
                        **self._nav_kwargs(),
                    )
                    # DOM-level captcha check (an in-page widget on a non-wall
                    # URL); the URL-level bounce is already caught by the guard.
                    captcha = await detect_captcha(self.page)
                    if captcha:
                        return True, None, None
                    # Read the profile like a human (scroll + dwell) before
                    # acting (#15 humanization preserved). _find_connect_control
                    # scrolls back to the top to bring the top-card action in view.
                    await scroll_down(self.page)
                    await self._dwell()
                    button, kind = await self._find_connect_control(profile)
                    return False, button, kind

                await self._throttle_action()
                captcha_detected, connect_button, control_kind = await run_bounded(
                    _navigate_and_read(),
                    timeout_s=self.settings.get_navigation_settings()[
                        "interaction_watchdog_s"
                    ],
                    recover=self._recover,
                    label=f"profile:{profile.name}",
                )

                # Stop early if LinkedIn challenges us — pushing through a
                # CAPTCHA is the fastest way to get an account restricted.
                if captcha_detected:
                    logger.warning("CAPTCHA detected during connection run; stopping")
                    if progress_callback:
                        progress_callback(
                            "⚠️ CAPTCHA detected — stopping automation to protect the account"
                        )
                    break

                if control_kind == "pending":
                    logger.info(f"Pending invitation already exists for {profile.name}")
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "pending",
                        "notes": "Already sent (found Pending button)",
                    }
                    self.db_manager.create_contact(contact_data)
                    existing_count += 1
                    if progress_callback:
                        progress_callback(f"⚠️ Already pending for {profile.name}")
                    continue

                if control_kind != "connect" or not connect_button:
                    logger.info(
                        f"No 'Connect' button for {profile.name} - already connected, "
                        "follow-only, or restricted profile"
                    )
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "No connect button available - likely already connected",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"⚠️ No Connect button for {profile.name}")
                    continue

                # Reserve a daily slot atomically BEFORE sending. This closes
                # the check-then-send window: a concurrent run cannot also pass
                # the cap while we are mid-send, because only one process can
                # claim the slot that brings the count to the limit. If the
                # reservation is refused, the day is full and we stop.
                reserved_count = self.db_manager.reserve_daily_slot(today, daily_limit)
                if reserved_count is None:
                    if progress_callback:
                        progress_callback(
                            f"Daily connection limit reached "
                            f"({daily_limit}/{daily_limit} used today)"
                        )
                    break
                slot_reserved = True

                # Click Connect and wait for the invitation modal — under the
                # SAME per-item watchdog as the read unit. ``move_to_and_click``,
                # ``query_selector`` and the modal-polling ``locator.count()``
                # loop are all untimeouted page operations a wedged renderer can
                # hang forever (a crashed renderer defeats even ``count()``); the
                # ``click`` has its own 5s timeout but the surrounding reads do
                # not. Bounding the click+poll here means a wedge after the page
                # loaded (e.g. right after clicking Connect, or while waiting for
                # the modal) caps the item, refreshes, and the outer
                # ``except asyncio.TimeoutError`` skips this profile and releases
                # its reserved slot — instead of wedging the rest of the run.
                async def _click_connect_and_await_modal():
                    # The top-card control is already in view at scroll-top, so a
                    # natural mouse move reaches it and the SDUI opens the
                    # invitation modal (a JS click is a last resort for the rare
                    # case the control is still occluded).
                    logger.info("Clicking 'Connect' button")
                    await self._throttle_action()
                    await move_to_and_click(self.page, connect_button)
                    await random_wait(self.page, min_ms=2500, max_ms=4000)

                    email_present = (
                        await self.page.query_selector('label[for="email"]')
                        is not None
                    )
                    if email_present:
                        return "email_required", False, False

                    note_loc = self.page.locator(sel.INVITE_ADD_NOTE.css).first
                    send_no_note_loc = self.page.locator(
                        sel.INVITE_SEND_NO_NOTE.css
                    ).first
                    send_exact_loc = self.page.locator(sel.INVITE_SEND.css).first
                    blocked_inner = False
                    modal_ready_inner = False
                    for _ in range(8):
                        # LinkedIn blocks re-inviting for 3 weeks after a
                        # withdrawal; clicking Connect then shows an error toast
                        # and no modal.
                        if await self._invitation_blocked_toast():
                            blocked_inner = True
                            break
                        if (
                            await note_loc.count()
                            or await send_no_note_loc.count()
                            or await send_exact_loc.count()
                        ):
                            modal_ready_inner = True
                            break
                        await self.page.wait_for_timeout(1000)
                    return "modal_check", blocked_inner, modal_ready_inner

                modal_outcome, blocked, modal_ready = await run_bounded(
                    _click_connect_and_await_modal(),
                    timeout_s=self.settings.get_navigation_settings()[
                        "interaction_watchdog_s"
                    ],
                    recover=self._recover,
                    label=f"invite:{profile.name}",
                )

                # Check if email is required to connect (dismiss and skip). The
                # ``email`` field is the modal's fingerprint; the dismiss control
                # comes from the selector registry (EMAIL_REQUIRED_DISMISS) so the
                # ES/EN aria-label variants live in one maintained place. This is
                # the *in-flow* handler that runs right after the Connect click
                # (when the modal actually appears and we must record the skip);
                # surf_benign_interstitials' email-modal dismiss is a cross-
                # navigation backstop for a stray leftover. They intentionally
                # share the selector — don't dedupe one away.
                email_label = (
                    await self.page.query_selector('label[for="email"]')
                    if modal_outcome == "email_required"
                    else None
                )
                if email_label:
                    logger.info(
                        f"Email request modal detected for {profile.name}. Dismissing..."
                    )
                    dismiss_btn = await sel.EMAIL_REQUIRED_DISMISS.locate(self.page)
                    if dismiss_btn:
                        await dismiss_btn.click()

                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Email required for connection",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"❌ Email required for {profile.name}")
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    continue

                # ``blocked`` / ``modal_ready`` were computed inside the bounded
                # click+poll unit above. The invitation modal is not a standard
                # <dialog>, so its buttons were located by text and polled until
                # they appeared (":text-is" matches the exact label so "Enviar"
                # never collides with "Enviar sin nota" / "Enviar mensaje").
                if blocked:
                    logger.info(
                        f"Invitation to {profile.name} blocked (recently withdrawn / cooldown)"
                    )
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Invitation blocked (recently withdrawn / 3-week cooldown)",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"⚠️ Invitation blocked for {profile.name} (cooldown)")
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    continue

                if not modal_ready:
                    logger.warning(f"Invitation modal did not appear for {profile.name}")
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Invitation modal did not appear after clicking Connect",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"❌ Invitation modal not found for {profile.name}")
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    continue

                # Send without a personalized note. LinkedIn gates custom notes
                # behind Premium (and a small free quota); attempting "Add a note"
                # leads to an upsell with no note field, so "Send without a note"
                # is the reliable path that always delivers the invitation.
                if campaign.message_template and campaign.message_template.strip():
                    logger.info(
                        "Campaign has a message template, but custom notes require "
                        "LinkedIn Premium; sending without a note"
                    )

                send_no_note = self.page.locator(sel.INVITE_SEND_NO_NOTE.css).first
                send_exact = self.page.locator(sel.INVITE_SEND.css).first
                send_target = send_no_note if await send_no_note.count() else send_exact

                logger.info("Clicking 'Send without a note' button")
                # Natural mouse move to the send button before clicking. The
                # click keeps its own failure handling (records the contact and
                # skips), so don't route it through move_to_and_click's JS
                # fallback — only humanize the approach.
                await self._throttle_action()
                await move_to_element(self.page, send_target)
                try:
                    await send_target.click(timeout=5000)
                except Exception as send_error:
                    logger.warning(f"Send click failed for {profile.name}: {send_error}")
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Send button not clickable after clicking Connect",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"❌ Send button not clickable for {profile.name}")
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    continue
                await random_wait(self.page, min_ms=2000, max_ms=3000)

                # Check for the weekly invitation limit, distinguishing the real
                # limit from the "near limit" warning.
                if await self._handle_invitation_limit_modal(profile):
                    if progress_callback:
                        progress_callback("❌ LinkedIn weekly invitation limit reached!")
                    break

                # Success - connection sent
                contact_data = {
                    "campaign_id": campaign.id,
                    "name": profile.name,
                    "profile_url": profile.profile_url,
                    "headline": profile.headline,
                    "location": profile.location,
                    "company": profile.company,
                    "status": "sent",
                    "connection_sent_at": datetime.now(timezone.utc),
                }
                self.db_manager.create_contact(contact_data)
                sent_count += 1
                consecutive_failures = 0  # successful action resets backoff
                # The slot was reserved (and persisted) before the send, so the
                # request is now confirmed and the reservation is consumed —
                # don't release it. reserved_count is the cumulative day total.
                slot_reserved = False
                total_today = reserved_count
                # Stamp the cooldown timestamp now (only on a real send, not on
                # reservation), so a failed send never triggers a false cooldown.
                self.db_manager.mark_connection_sent(today)
                logger.info(f"Successfully sent connection request to {profile.name}")

                if progress_callback:
                    progress_callback(f"✅ Sent connection request to {profile.name}")
                    progress_callback(
                        f"📊 {total_today}/{daily_limit} used today"
                    )

                # Random delay between connections
                delay = random.randint(
                    automation_settings["connection_delay_min"],
                    automation_settings["connection_delay_max"],
                )
                await self.page.wait_for_timeout(delay * 1000)

                # Check the persisted daily limit (cumulative across restarts).
                if total_today >= daily_limit:
                    if progress_callback:
                        progress_callback(
                            f"Daily connection limit reached "
                            f"({total_today}/{daily_limit} used today)"
                        )
                    break

            except (CaptchaDetectedException, NotAuthenticatedException) as challenge:
                # The guarded navigation bounced this profile to a
                # challenge/login wall (evidence already captured). Pushing
                # through is the fastest way to get the account restricted, so
                # stop the whole run, mirroring the in-page CAPTCHA break above.
                logger.warning(
                    "Navigation guard hit a challenge/login wall for %s; stopping: %s",
                    profile.name,
                    challenge,
                )
                if progress_callback:
                    progress_callback(
                        "⚠️ Challenge/login wall detected — stopping automation "
                        "to protect the account"
                    )
                break
            except asyncio.TimeoutError:
                # The interaction watchdog fired: a wedged renderer exceeded the
                # per-item budget. run_bounded already refreshed the browser
                # (self.page rebound via the recover callback), so skip just this
                # profile and keep processing the rest of the worklist.
                logger.warning(
                    "Per-profile interaction watchdog fired for %s; "
                    "skipping after browser refresh",
                    profile.name,
                )
                failed_count += 1
                if progress_callback:
                    progress_callback(
                        f"⚠️ Timed out on {profile.name} — refreshed browser and skipped"
                    )
                continue
            except Exception as e:
                logger.error(f"Failed to process {profile.name}: {str(e)}")
                failed_count += 1
                consecutive_failures += 1

                # A renderer that crashes by *raising* (rather than hanging)
                # escapes the run_bounded watchdog and surfaces here. Without a
                # refresh self.page stays dead and every later profile fails on
                # it until the backoff. Detect the crash shape and refresh once
                # so the rest of the worklist runs on a live page. Best-effort:
                # a failed refresh must not mask the original failure handling.
                if _is_crash_error(e):
                    logger.warning(
                        "Crash-shaped failure for %s — refreshing browser before "
                        "continuing",
                        profile.name,
                    )
                    try:
                        await self._refresh_context()
                    except Exception as refresh_exc:
                        logger.error(
                            "Browser refresh after crash-shaped failure failed: %s",
                            refresh_exc,
                        )

                # Exponential backoff after repeated failures: a burst of
                # errors often means LinkedIn has started throttling or
                # restricting the account, so we slow down instead of hammering.
                if consecutive_failures >= 3:
                    wait_seconds = min(
                        backoff_base_seconds * (2 ** (consecutive_failures - 3)),
                        backoff_cap_seconds,
                    )
                    logger.warning(
                        "%d consecutive failures; backing off %ds (possible restriction)",
                        consecutive_failures,
                        wait_seconds,
                    )
                    if progress_callback:
                        progress_callback(
                            f"⚠️ {consecutive_failures} consecutive failures — "
                            f"backing off {wait_seconds}s"
                        )
                    # A wall-clock pause, not a page operation — use asyncio so
                    # it never depends on a live page. A crash-shaped failure
                    # above may have left self.page None (a failed refresh), and
                    # the old page-based sleep would then throw AttributeError
                    # out of this handler and abort the whole run.
                    await asyncio.sleep(wait_seconds)
                continue
            finally:
                # Give back a reserved slot that wasn't consumed by a confirmed
                # send (email-required, blocked, modal-not-found, failed send,
                # or any exception). Success clears the flag, so this is a
                # no-op there. Runs on every continue/break/exception exit.
                if slot_reserved:
                    self.db_manager.release_daily_slot(today)

        # Update campaign statistics
        self.db_manager.update_campaign_stats(campaign.id)

        return {
            "sent": sent_count,
            "failed": failed_count,
            "existing": existing_count,
            "total_processed": sent_count + failed_count + existing_count,
        }

    def _build_search_params(self, campaign: Campaign) -> str:
        """Build LinkedIn search parameters from campaign criteria"""
        params = []

        # Keywords - URL encode for safety
        if campaign.keywords:
            keywords_encoded = urllib.parse.quote(campaign.keywords)
            params.append(f"keywords={keywords_encoded}")

        # Location - use new geo_urn field, fallback to legacy location field
        geo_urn = campaign.geo_urn if hasattr(campaign, 'geo_urn') and campaign.geo_urn else None
        if not geo_urn and campaign.location:
            # Legacy support: if old location field exists but no geo_urn
            # This shouldn't happen in new campaigns, but keeps backward compatibility
            geo_urn = campaign.location

        if geo_urn:
            # Correct format: geoUrn=["105646813"]
            params.append(f'geoUrn=["{geo_urn}"]')

        # Industry - use new industry_ids field (comma-separated), fallback to legacy industry field
        industry_ids = campaign.industry_ids if hasattr(campaign, 'industry_ids') and campaign.industry_ids else None
        if not industry_ids and campaign.industry:
            # Legacy support
            industry_ids = campaign.industry

        if industry_ids:
            # Convert comma-separated IDs to LinkedIn format: industry=["4","6"]
            formatted = format_ids_for_url(industry_ids)
            if formatted:
                params.append(f"industry={formatted}")

        # Network - use new network field with default
        network = campaign.network if hasattr(campaign, 'network') and campaign.network else '["F","S"]'
        if network:
            params.append(f"network={network}")

        # Origin - use FACETED_SEARCH as per LinkedIn's current format
        params.append("origin=FACETED_SEARCH")

        return "&".join(params)

    async def search_location(self, query: str) -> List[Dict[str, str]]:
        """
        Search for LinkedIn location geoUrn codes.

        LinkedIn removed the public Voyager typeahead REST endpoint, so this
        drives the people-search "Locations" filter UI and captures the
        geoUrn each suggestion resolves to from the results page URL.

        Args:
            query: Location search query (e.g., "San Francisco", "Madrid")

        Returns:
            List of dicts with keys: 'name' (display name) and 'geoUrn' (code)

        Raises:
            NotAuthenticatedException: If not authenticated
        """
        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        if not query or not query.strip():
            return []

        logger.info(f"Searching location: {query}")
        try:
            results = await self._search_location_via_filter_ui(query.strip())
            logger.info(f"Found {len(results)} locations for '{query}'")
            return results
        except Exception as e:
            logger.error(f"Error searching location: {e}")
            return []

    async def _search_location_via_filter_ui(
        self, query: str, max_options: int = 5
    ) -> List[Dict[str, str]]:
        """Resolve location names to geoUrn codes by driving the search filter UI.

        Each suggestion is clicked and applied so its geoUrn appears in the
        results URL, then the page is reset for the next suggestion.
        """
        base_url = f"{self.SEARCH_URL}?origin=FACETED_SEARCH"
        results: List[Dict[str, str]] = []
        total_options: Optional[int] = None
        index = 0

        while total_options is None or index < total_options:
            await self.page.goto(base_url, timeout=30000)
            await self.page.wait_for_timeout(3000)

            # Open the Locations filter pill (ES/EN)
            pill = self.page.locator("text=/^Ubicaciones$|^Locations$/").first
            await pill.click(timeout=10000)
            await self.page.wait_for_timeout(1500)

            # The typeahead input renders inside the dropdown
            box = self.page.locator("input:visible").last
            await box.click(timeout=5000)
            await box.type(query, delay=120)

            options = self.page.locator("[role='option']")
            await options.first.wait_for(state="visible", timeout=10000)
            await self.page.wait_for_timeout(1500)  # let the list settle

            if total_options is None:
                total_options = min(await options.count(), max_options)
                logger.info(
                    f"Found {total_options} location suggestions for '{query}'"
                )

            option = options.nth(index)
            name = (await option.inner_text()).strip().splitlines()[0]
            await option.click(timeout=5000)
            await self.page.wait_for_timeout(1500)  # let the checkbox register

            # Apply the filter so the geoUrn shows up in the URL. The control
            # is an <a> ("Mostrar resultados" / "Show results") in the SDUI
            # filter dropdown, with a button fallback for older variants.
            apply_control = self.page.locator(
                "a:has-text('Mostrar resultados'), a:has-text('Show results'), "
                "button:has-text('Mostrar resultados'), button:has-text('Show results')"
            ).first
            try:
                await apply_control.click(timeout=5000)
            except Exception as apply_error:
                logger.debug(f"Apply button click failed: {apply_error}")

            try:
                await self.page.wait_for_url(
                    lambda url: "geourn" in str(url).lower(), timeout=15000
                )
            except Exception:
                logger.warning(
                    f"No geoUrn in URL after selecting '{name}'; skipping suggestion"
                )
                index += 1
                continue
            geo_param = urllib.parse.parse_qs(
                urllib.parse.urlparse(self.page.url).query
            ).get("geoUrn", [""])[0]
            geo_urn = "".join(ch for ch in geo_param if ch.isdigit())

            if name and geo_urn:
                results.append({"name": name, "geoUrn": geo_urn})
            index += 1

        return results

    async def _extract_profiles_new_ui(self) -> List[LinkedInProfile]:
        """Extract search results from LinkedIn's SDUI search layout (2026).

        The new layout uses obfuscated class names, so result cards are
        located via the stable ``SearchResults_FirstResult_people``
        componentkey and parsed from their visible text in one JS pass.
        """
        raw = await self.page.evaluate(
            """
            () => {
                const first = document.querySelector(
                    '[componentkey="SearchResults_FirstResult_people"]'
                );
                let cards = [];
                if (first && first.parentElement) {
                    cards = [...first.parentElement.children];
                } else {
                    cards = [...document.querySelectorAll('main [componentkey]')];
                }
                const results = [];
                const seen = new Set();
                for (const card of cards) {
                    const link = card.querySelector("a[href*='/in/']");
                    if (!link) continue;
                    const href = link.href.split('?')[0];
                    if (seen.has(href)) continue;
                    const lines = (card.innerText || '')
                        .split('\\n').map(s => s.trim()).filter(Boolean);
                    if (!lines.length) continue;
                    seen.add(href);
                    results.push({href, lines: lines.slice(0, 8)});
                }
                return results;
            }
            """
        )
        if not isinstance(raw, list):
            return []

        action_words = {
            "conectar", "connect", "seguir", "follow",
            "mensaje", "message", "pendiente", "pending",
        }
        profiles = []
        for item in raw:
            lines = item.get("lines") or []
            if not lines:
                continue
            # First line is "Name • 2º" (degree marker after the bullet)
            name = lines[0].split("•")[0].strip()
            if not name:
                continue
            rest = [l for l in lines[1:] if l.lower() not in action_words]
            profiles.append(
                LinkedInProfile(
                    name=name,
                    profile_url=item["href"],
                    headline=rest[0] if rest else None,
                    location=rest[1] if len(rest) > 1 else None,
                )
            )
        return profiles

    @staticmethod
    def _normalize(text: Optional[str]) -> str:
        """Casefold, strip accents, and collapse whitespace for comparison."""
        decomposed = unicodedata.normalize("NFKD", text or "")
        no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
        return " ".join(no_marks.casefold().split())

    async def _find_connect_control(self, profile: "LinkedInProfile"):
        """Find the Connect/Pending control for THIS profile (SDUI layout, 2026).

        Both the top-card action button and the scroll-activated sticky header
        carry the person's name in their ``aria-label`` (e.g. "Invita a
        {Name} a conectar"), which disambiguates the real action from the
        "People also viewed" sidebar that is full of other Connect buttons.

        Returns ``(handle, kind)`` where kind is 'connect', 'pending' or 'none'.
        """
        name_norm = self._normalize(profile.name)
        if not name_norm:
            return None, "none"

        # The profile's own primary action is an <a>, while the "People also
        # viewed" sidebar uses <button>; query both (plus role=button). When
        # the same control exists in both the top card and the scroll-only
        # sticky header, prefer the lower one (the top card), which is never
        # overlapped by the floating "Probar Premium" promo.
        async def match(keywords) -> Optional[Any]:
            controls = await self.page.query_selector_all(
                sel.CONNECT_CONTROL.css
            )
            best = None
            best_y = -1.0
            for ctrl in controls:
                aria = self._normalize(await ctrl.get_attribute("aria-label"))
                if name_norm in aria and any(k in aria for k in keywords):
                    try:
                        if not await ctrl.is_visible():
                            continue
                        box = await ctrl.bounding_box()
                        y = box["y"] if box else 0.0
                        if y > best_y:
                            best, best_y = ctrl, y
                    except Exception:
                        continue
            return best

        # Stay at the top of the page so the top-card action (visible in a
        # 1080px viewport) is used, with no sticky header / promo overlapping.
        try:
            await self.page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

        # SDUI action controls render shortly after load, so poll a few times.
        for _ in range(5):
            connect = await match(("conectar", "connect"))
            if connect:
                return connect, "connect"
            pending = await match(("pendiente", "pending"))
            if pending:
                return pending, "pending"
            await self.page.wait_for_timeout(1000)

        return None, "none"

    async def _invitation_blocked_toast(self) -> bool:
        """Detect the error toast shown when an invitation can't be sent.

        Covers LinkedIn's 3-week post-withdrawal cooldown and similar "not
        sent" errors, in Spanish and English.
        """
        try:
            text = await self.page.evaluate(
                """
                () => {
                  const sel = "[role='alert'], [class*='toast' i], [class*='snackbar' i]";
                  for (const e of document.querySelectorAll(sel)) {
                    const t = (e.innerText || '').trim();
                    if (t) return t;
                  }
                  return '';
                }
                """
            )
        except Exception:
            return False

        t = self._normalize(text)
        markers = (
            "no se ha enviado la invitacion",
            "3 semanas despues de retirarla",
            "couldn't send",
            "could not send",
            "weeks after you withdraw",
        )
        return any(m in t for m in markers)

    async def _handle_invitation_limit_modal(self, profile: "LinkedInProfile") -> bool:
        """Detect and dismiss the weekly invitation-limit modal.

        Returns True only when the real weekly limit was hit (caller should
        stop). A "near limit" warning is dismissed and returns False.
        """
        # Resolve via the combined CSS (DOM-order first match), not .locate's
        # candidate-order: the returned handle is the search root for
        # _is_true_limit() and the close-button queries below, so we must get
        # the *outer* modal wrapper. A nested layout (the data-test id on an
        # inner node, the artdeco class on the outer wrapper) would otherwise
        # have .locate prefer the inner node and scope those sub-queries to the
        # wrong subtree, misclassifying a real weekly limit as a normal send.
        modal = await self.page.query_selector(sel.LIMIT_MODAL.css)
        if not modal:
            return False

        is_true = await _is_true_limit(modal)
        log_msg = (
            f"Weekly invitation limit reached; not sent to {profile.name}"
            if is_true
            else f"'Near limit' warning for {profile.name}; continuing"
        )
        logger.warning(log_msg) if is_true else logger.info(log_msg)

        for close_sel in (
            "button.ip-fuse-limit-alert__primary-action",
            "button[aria-label='Descartar']",
            "button[aria-label='Dismiss']",
        ):
            try:
                close_btn = await modal.query_selector(close_sel)
                if close_btn and await close_btn.is_visible():
                    await close_btn.click()
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    break
            except Exception:
                continue

        return is_true

    async def _extract_profile_info(self, element) -> Optional[LinkedInProfile]:
        """Extract profile information from search result element"""
        try:
            # Get profile link - LinkedIn profile links contain "/in/"
            link_element = await element.query_selector("a[href*='/in/']")
            if not link_element:
                # Try alternative selector
                link_element = await element.query_selector("a.app-aware-link")

            if not link_element:
                return None

            profile_url = await link_element.get_attribute("href")
            if not profile_url:
                return None

            # Clean up URL (remove query parameters)
            if "?" in profile_url:
                profile_url = profile_url.split("?")[0]

            if not profile_url.startswith("http"):
                profile_url = self.BASE_URL + profile_url

            # Extract name - try multiple strategies
            name = None

            # Strategy 1: Get from link text
            name_text = await link_element.inner_text()
            if name_text and name_text.strip():
                name = name_text.strip()

            # Strategy 2: Try aria-label attribute
            if not name:
                aria_label = await link_element.get_attribute("aria-label")
                if aria_label:
                    name = aria_label.strip()

            # Strategy 3: Look for span with name
            if not name:
                name_span = await element.query_selector("span[aria-hidden='true']")
                if name_span:
                    name_text = await name_span.inner_text()
                    if name_text and name_text.strip():
                        name = name_text.strip()

            if not name:
                logger.warning("Could not extract name from profile")
                return None

            # Extract headline - look for any div that might contain headline info
            headline = None
            try:
                # Try to find elements that might contain headline
                text_elements = await element.query_selector_all("div")
                for text_elem in text_elements:
                    text = await text_elem.inner_text()
                    # Headline is usually 1-3 lines of text describing role
                    if text and len(text) > 10 and len(text) < 200 and text != name:
                        # Check if it looks like a headline (contains job-related keywords)
                        if any(keyword in text.lower() for keyword in ["engineer", "manager", "developer", "designer", "director", "founder", "consultant", "analyst", "specialist", "lead", "senior", "junior", "intern", "at ", "•"]):
                            headline = text.strip()
                            break
            except Exception as e:
                logger.debug(f"Could not extract headline: {e}")

            # Extract location - usually appears after headline
            location = None
            try:
                text_elements = await element.query_selector_all("div")
                for text_elem in text_elements:
                    text = await text_elem.inner_text()
                    # Location is usually short and might contain city/country names
                    if text and len(text) > 2 and len(text) < 100:
                        # Check if it looks like a location
                        if any(keyword in text for keyword in [", ", " Area", "United States", "Canada", "UK", "London", "New York", "San Francisco", "Remote"]):
                            location = text.strip()
                            break
            except Exception as e:
                logger.debug(f"Could not extract location: {e}")

            return LinkedInProfile(
                name=name.strip(),
                profile_url=profile_url,
                headline=headline.strip() if headline else None,
                location=location.strip() if location else None,
            )

        except Exception as e:
            logger.warning(f"Failed to extract profile info: {e}")
            return None

    async def check_connection_status(
        self, contacts: List[Contact], progress_callback: Optional[Callable] = None
    ) -> int:
        """Check status of pending connection requests using enhanced checker"""
        from .checker import check_specific_contacts

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        # Filter to only sent contacts and get their IDs
        sent_contacts = [contact for contact in contacts if contact.status == "sent"]
        contact_ids = [contact.id for contact in sent_contacts]

        if not contact_ids:
            if progress_callback:
                progress_callback("No pending connections to check")
            return 0

        # Use the enhanced checker
        stats = await check_specific_contacts(self, contact_ids, progress_callback)
        return stats["newly_accepted"]

    async def smart_connection_checker(
        self, campaign_id: int, progress_callback: Optional[Callable] = None
    ) -> Dict[str, int]:
        """Smart checker that monitors LinkedIn connections page for newly accepted connections"""
        from .checker import smart_connection_checker

        return await smart_connection_checker(self, campaign_id, progress_callback)

    async def extract_detailed_profile(
        self, profile_url: str, progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Extract comprehensive profile data using enhanced scraping"""
        from .scraping import collect_public_information, get_contact_info, get_open_to_work_status

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        try:
            if progress_callback:
                progress_callback(f"Extracting detailed profile data...")

            await self.page.goto(profile_url, timeout=30000)
            await self.page.wait_for_timeout(2000)

            # Collect comprehensive profile information
            profession, location, experience, education = await collect_public_information(self.page)

            # Get contact information
            contact_info = await get_contact_info(self.page)

            # Check open to work status
            open_to_work = await get_open_to_work_status(self.page)

            profile_data = {
                "profile_url": profile_url,
                "profession": profession,
                "location": location,
                "experience": experience,
                "education": education,
                "contact_info": contact_info,
                "open_to_work": open_to_work,
                "extracted_at": datetime.now(timezone.utc),
            }

            if progress_callback:
                progress_callback(f"✅ Extracted profile data successfully")

            return profile_data

        except Exception as e:
            logger.error(f"Failed to extract profile data: {str(e)}")
            if progress_callback:
                progress_callback(f"❌ Failed to extract profile data: {str(e)}")
            return {}
