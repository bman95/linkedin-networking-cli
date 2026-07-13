import asyncio
import builtins
import os
import platform
import random
import re
import time
import unicodedata
import urllib.parse
import uuid
from collections import namedtuple
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import psutil
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    TimeoutError,
    async_playwright,
)

from automation import selectors as sel
from automation.diagnostics import (
    capture_anomaly_context,
    capture_error_context,
    reset_diagnostics_run,
    snapshot_page,
)
from automation.interactions import (
    RateLimiter,
    _is_true_limit,
    dwell,
    human_type,
    move_to_and_click,
    move_to_element,
    random_wait,
    scroll_down,
)
from automation.linkedin_mappings import format_ids_for_url
from automation.navigation import (
    _is_crash_error,
    confirm_logged_in_dom,
    landed_on_challenge,
    landed_on_checkpoint,
    navigate_guarded,
    run_bounded,
    verify_listing_rendered,
)
from cli.helpers import effective_daily_limit
from config.settings import AppSettings
from database.models import Campaign, Contact, ContactStatus
from database.operations import DatabaseManager
from exceptions import (
    BrowserProfileBusyError,
    CaptchaDetectedException,
    LinkedInAutomationError,
    LoginFailedException,
    NotAuthenticatedException,
    SelectorNotFoundException,
    UnexpectedLandingException,
)
from utils.logging import get_logger

logger = get_logger(__name__)


# Outcome of one per-profile connect attempt (see _attempt_connect). ``outcome``
# is the terminal state string the send loop branches on ("sent",
# "possibly_sent", "day_full", "limit_reached", "email_required", "blocked",
# "modal_not_found", "send_failed"); ``total_today`` is set on a confirmed send
# AND on a conservative "possibly_sent" (both consume the reserved slot, so it
# carries the cumulative day count the caller uses to decide whether to stop),
# and defaults to None for every outcome that releases its slot.
#
# "possibly_sent" is the resilient send-tail outcome (issue #31): a watchdog
# timeout or crash that strikes AFTER the irreversible "Send" click was fired
# but before the result could be confirmed. The invitation may well have been
# delivered, so we assume sent on ambiguity — the reserved daily slot is KEPT
# (no cap drift) and the contact is recorded non-retryable (no re-contact),
# rather than released and marked a plain retryable ``failed``.
ConnectResult = namedtuple("ConnectResult", ["outcome", "total_today"])
ConnectResult.__new__.__defaults__ = (None,)


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


# Chrome's single-instance lock artifacts inside a persistent profile. On
# POSIX these are symlinks Chrome leaves behind when it dies without a clean
# shutdown; a stale one makes the next launch_persistent_context abort with
# "Failed to create a ProcessSingleton for your profile directory" — the browser
# opens and immediately closes (exit code 21). Windows guards the profile with a
# kernel object instead and writes no such files, so clearing them is a POSIX-
# only concern and a harmless no-op elsewhere.
_SINGLETON_LOCK_FILES = ("SingletonLock", "SingletonSocket", "SingletonCookie")


def _chrome_procs_using_profile(user_data_dir: str) -> list[psutil.Process]:
    """Return live Chrome/Chromium processes bound to *this* profile.

    Matched by the ``--user-data-dir`` flag on each process' command line so a
    user's everyday Chrome windows — which run on a different profile — are never
    in the set. Cross-platform: relies only on psutil, not on any OS-specific
    kill utility, so the same code path works on Windows and Linux.
    """
    target = os.path.normcase(os.path.abspath(user_data_dir))
    matches: list[psutil.Process] = []
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "chrome" not in name and "chromium" not in name:
                continue
            for arg in proc.info.get("cmdline") or []:
                if arg.startswith("--user-data-dir="):
                    value = arg.split("=", 1)[1]
                    if os.path.normcase(os.path.abspath(value)) == target:
                        matches.append(proc)
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return matches


def _clear_stale_singleton_locks(user_data_dir: Path) -> None:
    """Delete leftover single-instance lock files from a persistent profile.

    Only safe once no live Chrome is using the profile (callers kill those
    first). The locks are symlinks, so ``unlink`` removes the link itself; a
    missing one is fine — on Windows none exist, so every call is a no-op.
    """
    for name in _SINGLETON_LOCK_FILES:
        lock = user_data_dir / name
        try:
            lock.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove stale lock %s: %s", lock, exc)


def force_close_chrome(user_data_dir: str | None = None) -> None:
    """Clear whatever would block a clean persistent-profile launch.

    The OS dictates *how*. On every platform we first kill any leftover Chrome
    still holding this exact profile (scoped via psutil so the user's other
    windows survive); then, on POSIX only, we delete the stale ``Singleton*``
    lock files a hard-killed Chrome leaves behind — the actual trigger of the
    "ProcessSingleton" launch abort that opens and instantly closes the browser.
    On Windows that second step is skipped: the profile lock is a kernel object
    released when the process dies, and no lock files exist to remove.

    With no ``user_data_dir`` (the transient launch path) there is no dedicated
    profile to clean, so this returns immediately rather than touching any
    Chrome the user happens to have open.
    """
    if not user_data_dir:
        return

    try:
        leftovers = _chrome_procs_using_profile(user_data_dir)
    except Exception as exc:
        logger.warning("Error inspecting Chrome processes: %s", exc)
        leftovers = []

    for proc in leftovers:
        try:
            proc.kill()
            logger.debug("Killed leftover Chrome process %d on our profile", proc.pid)
        except psutil.NoSuchProcess:
            pass
        except Exception as exc:
            logger.warning("Error killing Chrome process %d: %s", proc.pid, exc)

    if leftovers:
        # Wait (bounded) for the OS to release the profile's file handles before
        # we clear its locks, instead of a blind fixed sleep.
        psutil.wait_procs(leftovers, timeout=3)

    if platform.system() != "Windows":
        _clear_stale_singleton_locks(Path(user_data_dir))


# Cross-process lock guarding the persistent Chrome profile. Two of our own
# processes (the TUI and a cron-scheduled ``linkedin-run``) can target the
# SAME on-disk profile; without this, ``force_close_chrome`` above would kill
# a live, legitimate owner's Chrome out from under it. The lock lives
# alongside the profile directory (its parent — the app dir) rather than
# inside it, so it is never mistaken for Chrome's own profile contents.
_PROFILE_LOCK_NAME = "browser_profile.lock"


def _profile_lock_path(user_data_dir: str) -> Path:
    """The lockfile path for ``user_data_dir``'s persistent profile."""
    return Path(user_data_dir).parent / _PROFILE_LOCK_NAME


def _pid_is_alive(pid: int) -> bool:
    """True if ``pid`` names a live process (any owner, not just ours).

    ``os.kill(pid, 0)`` sends no signal — it only probes whether the PID is
    joinable. ``ProcessLookupError`` means the process is gone (a stale
    lock); ``PermissionError`` means it exists but we lack permission to
    signal it — still alive, so treated as such rather than mistaken for a
    stale lock.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock_pid(lock_path: Path) -> int | None:
    """Best-effort read of the PID stored in the lockfile; None on any problem."""
    try:
        content = lock_path.read_text(encoding="utf-8").strip()
        return int(content)
    except (OSError, ValueError):
        return None


def acquire_profile_lock(user_data_dir: str) -> Path:
    """Claim the cross-process lock on ``user_data_dir``'s persistent profile.

    A missing or stale lock (its PID no longer alive) is cleaned up and
    claimed for our own PID, written atomically (write-temp then
    ``os.replace``, so a concurrent reader never observes a partial write). A
    lock whose PID IS alive raises :class:`BrowserProfileBusyError` instead of
    being silently cleared — that PID owns a legitimate, possibly concurrent
    run, and clearing its lock would let ``force_close_chrome`` kill its
    Chrome out from under it.

    Returns the lock path (for :func:`release_profile_lock`).
    """
    lock_path = _profile_lock_path(user_data_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    existing_pid = _read_lock_pid(lock_path)
    if (
        existing_pid is not None
        and existing_pid != os.getpid()
        and _pid_is_alive(existing_pid)
    ):
        raise BrowserProfileBusyError(
            f"The browser profile at {user_data_dir!r} is already in use by "
            f"process {existing_pid} (e.g. another linkedin-tui/linkedin-run "
            "run). Wait for it to finish, or stop it, before starting a new one."
        )
    if existing_pid is not None:
        # Stale (dead-PID) lock or our own leftover — safe to clear.
        try:
            lock_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.debug("Could not remove stale profile lock %s: %s", lock_path, exc)

    tmp_path = lock_path.with_name(f"{lock_path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(str(os.getpid()), encoding="utf-8")
    os.replace(tmp_path, lock_path)
    return lock_path


def release_profile_lock(user_data_dir: str) -> None:
    """Release our profile lock, only if it still names our own PID.

    Best-effort and never raises: called from ``close_browser``'s teardown,
    which must complete regardless. Never unlinks a lock some other (live)
    process has since claimed.
    """
    lock_path = _profile_lock_path(user_data_dir)
    if _read_lock_pid(lock_path) != os.getpid():
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove profile lock %s: %s", lock_path, exc)


@dataclass
class LinkedInProfile:
    """Data class for LinkedIn profile information"""

    name: str
    profile_url: str
    headline: str | None = None
    location: str | None = None
    company: str | None = None
    mutual_connections: int = 0


class LinkedInAutomation:
    """LinkedIn automation engine for networking campaigns"""

    BASE_URL = "https://www.linkedin.com"
    SEARCH_URL = f"{BASE_URL}/search/results/people/"

    # Action labels (EN + ES) dropped from a result card's visible text lines so
    # the headline/location land on the right lines when parsing a card.
    _CARD_ACTION_WORDS = frozenset({
        "conectar", "connect", "seguir", "follow",
        "mensaje", "message", "pendiente", "pending",
    })

    def __init__(self, db_manager: DatabaseManager, settings: AppSettings):
        self.db_manager = db_manager
        self.settings = settings
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.is_authenticated = False
        # Sliding-window per-minute action cap, built lazily from settings so
        # an env override applied after construction still takes effect.
        self._rate_limiter: RateLimiter | None = None
        # The persistent profile's user_data_dir while we hold its
        # cross-process lock (see acquire_profile_lock); None when no lock is
        # held (transient launch path, or before start_browser runs).
        self._locked_user_data_dir: str | None = None

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

    def _mark_session_compromised(self) -> None:
        """Clear ``is_authenticated`` after a mid-run captcha/checkpoint/logout.

        ``is_authenticated`` is set once by :meth:`login` and is otherwise a
        one-way flag; a challenge or logout detected later in the SAME run
        would otherwise leave it stuck True, so ``close_browser`` would
        persist ``session.json`` from a compromised session — clobbering a
        previously-good file with cookies from a session that just got
        flagged. Call this from every call site that detects a mid-run
        challenge/logout (never from :meth:`login` itself, which only sets
        the flag once a session is actually confirmed).
        """
        if self.is_authenticated:
            logger.warning(
                "Marking session compromised (mid-run captcha/checkpoint/"
                "logout detected); session.json will not be overwritten on close"
            )
        self.is_authenticated = False

    def _page_on_challenge_wall(self) -> bool:
        """Best-effort: is the current page sitting on a login/challenge URL?

        Belt-and-braces backstop for :meth:`close_browser`'s session-write
        guard: reuses the same challenge/login path patterns the navigation
        guard uses (``landed_on_challenge``) so a missed
        ``_mark_session_compromised`` call site is still caught directly from
        the page's own URL right before the write.
        """
        if self.page is None:
            return False
        try:
            url = self.page.url
        except Exception:
            return False
        return landed_on_challenge(str(url)) is not None

    async def _refresh_context(self) -> Page:
        """Close and reopen the browser context, keeping the persistent profile.

        Recovery primitive for the resilient-navigation layer (issue #17): when
        a renderer crashes or a per-item watchdog fires, the wedged context is
        torn down and a fresh one launched so one crash does not cascade across
        the rest of the worklist. Login state survives because the persistent
        Chrome profile on disk (and the ``session.json`` ``close_browser``
        writes for the transient path) carries the cookies — ``start_browser``
        re-reads them, so the refreshed context resumes the same session.

        The teardown is bounded *per step* inside ``close_browser`` itself (each
        close gets its own ``asyncio.wait_for``), so on a frozen,
        memory-thrashing renderer a hung step cannot wedge the very refresh meant
        to recover from it — and, crucially, cannot starve the later steps
        (``playwright.stop()`` frees the driver subprocess and must always run).
        ``close_browser`` swallows its own errors, so this never needs to guard
        the whole call with a watchdog that would cancel it mid-teardown.

        Returns the fresh ``Page``.
        """
        logger.warning("Refreshing browser context to recover from a wedged renderer")
        # Invariant: the cross-process profile lock is held across the WHOLE
        # close+relaunch below, never released in between. close_browser then
        # start_browser back-to-back would otherwise open a window — between
        # the release and the re-acquire — where a concurrent run could grab
        # the now-free profile and force_close_chrome our own relaunch out
        # from under it. Stash the lock dir and clear the attribute so
        # close_browser's own release (in its finally) is skipped; the lock
        # file stays on disk naming our PID for the whole gap.
        # start_browser's acquire_profile_lock treats an existing same-PID
        # lock as our own leftover and reclaims it. If start_browser itself
        # then fails, its own failure-path release cleans the lock up.
        locked_user_data_dir = self._locked_user_data_dir
        self._locked_user_data_dir = None
        if locked_user_data_dir is not None:
            logger.debug(
                "Holding profile lock on %s across close+relaunch", locked_user_data_dir
            )
        await self.close_browser()
        # Drop any partial handles so start_browser launches from scratch rather
        # than reusing a half-dead context.
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

    def _nav_kwargs(self) -> dict[str, Any]:
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
        """Initialize the browser, releasing the profile lock if this fails.

        Delegates to :meth:`_start_browser_unlocked` for the actual launch
        mechanics (see its docstring). Wrapped here — not just in
        ``__aenter__`` — so a direct ``start_browser()`` call is covered too,
        not only the ``async with`` path: any exception raised after the
        cross-process profile lock (``acquire_profile_lock``) was
        successfully acquired must release it before propagating, or the lock
        file is left on disk naming a live PID and falsely blocks every other
        run until this process dies.
        """
        try:
            await self._start_browser_unlocked()
        except Exception:
            # _locked_user_data_dir is only set AFTER a successful
            # acquire_profile_lock call, so a BrowserProfileBusyError raised
            # by the acquire itself leaves it None here — this release is
            # then a no-op and the exception propagates untouched, exactly as
            # before.
            if self._locked_user_data_dir is not None:
                release_profile_lock(self._locked_user_data_dir)
                self._locked_user_data_dir = None
            raise

    async def _start_browser_unlocked(self):
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
        not exclusive but conditional: ``login`` writes ``session.json`` on a
        confirmed login, and ``close_browser`` writes it whenever the run's
        session is still believed healthy (``is_authenticated``) — persistent
        runs included — so a later transient run can resume the session a
        persistent run established. ``close_browser`` skips the write when no
        authenticated session was confirmed this run (including one compromised
        mid-run by a detected CAPTCHA/checkpoint/logout, which clears
        ``is_authenticated``), and as a belt-and-braces also skips it when the
        page is sitting on a login/challenge URL at close — so a degraded
        context never clobbers a still-good ``session.json``.
        """
        self.playwright = await async_playwright().start()
        browser_settings = self.settings.get_browser_settings()

        launch_kwargs: dict[str, Any] = {
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
        context_options: dict[str, Any] = {
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
            # The Chrome profile holds live login cookies; keep it — and the
            # app dir above it — owner-only.
            self._restrict_permissions(user_data_path, 0o700)
            self._restrict_permissions(user_data_path.parent, 0o700)

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
            # Claim the cross-process profile lock BEFORE force_close_chrome:
            # a live PID in the lock means another of our own processes (the
            # TUI, a cron linkedin-run) legitimately owns this profile right
            # now, and force_close_chrome killing its Chrome would corrupt
            # that run. A stale (dead-PID) lock is cleaned up and claimed;
            # only a live owner raises. Not caught below — a busy profile
            # must abort start_browser, not silently fall back to a
            # transient (session.json) launch that would let a second run
            # proceed against the same account concurrently.
            acquire_profile_lock(user_data_dir)
            self._locked_user_data_dir = user_data_dir
            # Free the profile before launching: kill any Chrome still holding it
            # (now known to be an orphan, not a locked-in live owner) and (on
            # POSIX) drop stale single-instance locks, so the launch below
            # can't abort with "ProcessSingleton" and bounce the browser.
            force_close_chrome(user_data_dir)
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
            except Exception:
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

    @staticmethod
    def _restrict_permissions(path: Path, mode: int) -> None:
        """Best-effort ``chmod``; POSIX modes may be unsupported (Windows/WSL
        mounts), so failure is logged at debug and never raised."""
        try:
            os.chmod(path, mode)
        except Exception as chmod_error:
            logger.debug("Could not chmod %s to %o: %s", path, mode, chmod_error)

    async def _write_session_state(self, context: BrowserContext) -> None:
        """Write ``session.json`` via ``storage_state``, then lock it down.

        ``session.json`` carries the full LinkedIn auth cookies, so it must not
        be left with default (world-readable) permissions. The containing app
        dir is tightened alongside it. Raises whatever ``storage_state`` raises;
        the permission tightening itself is best-effort.
        """
        session_path = self.settings.session_path
        await context.storage_state(path=str(session_path))
        self._restrict_permissions(session_path, 0o600)
        self._restrict_permissions(session_path.parent, 0o700)

    # Per-step teardown budget (seconds). ``close_browser`` is called during
    # crash recovery against a *wedged* renderer, where an individual close can
    # HANG (not just throw). Each step is bounded on its own so a hung step
    # cannot starve the *later* steps — in particular ``playwright.stop()``,
    # which frees the driver subprocess — and leak a process per crash.
    _CLOSE_STEP_TIMEOUT_S = 10

    # "modal_not_found" outcomes (see _attempt_connect) SINCE THE LAST
    # sent/possibly_sent, not necessarily back-to-back: the counter only
    # resets on a real (or possible) send, so an interleaved "existing" /
    # "email_required" / "blocked" / "send_failed" does not reset it either —
    # it just doesn't advance it. Once it reaches this threshold the whole
    # send loop aborts. A soft "modal_not_found" alone lets the run burn
    # through its entire worklist "successfully" without sending a single
    # invitation (e.g. LinkedIn UI language unsupported, or markup changed) —
    # a run this consistently unproductive is a signal to stop and let a human
    # look, not to keep silently failing every profile.
    _MODAL_NOT_FOUND_ABORT_THRESHOLD = 5

    async def _close_step(self, awaitable, what: str):
        """Run one teardown step, bounded and best-effort (never raises).

        A throw OR a hang on one step must not skip the remaining steps, so each
        is wrapped in its own ``asyncio.wait_for`` and its errors are swallowed.
        """
        try:
            await asyncio.wait_for(awaitable, timeout=self._CLOSE_STEP_TIMEOUT_S)
        except Exception as exc:
            logger.debug("Teardown step %s did not complete cleanly: %s", what, exc)

    async def close_browser(self):
        """Close browser and cleanup.

        Every step is independently bounded and best-effort. ``_refresh_context``
        calls this specifically against *crashed/half-closed* objects, where the
        snapshot, ``context.close``, ``browser.close`` or ``playwright.stop`` can
        each throw *or hang*. Either failure on one step must not skip the later
        steps and orphan the still-running Chrome/Playwright node driver, so a
        repeated crash-recovery refresh would leak a process per crash. Bounding
        each step keeps the teardown total — every handle gets a bounded close
        attempt, and ``stop`` (which frees the driver subprocess) always runs.
        """
        # The cross-process profile lock is released in the finally below —
        # after the session write and teardown steps, so a concurrent run
        # cannot acquire the profile (and force-kill this Chrome) while the
        # storage_state write is still in flight. Every step is bounded, so
        # the finally is always reached whenever close_browser actually runs.
        # A start_browser that raises before returning (so this method is
        # never called — e.g. __aenter__ propagating the failure) is instead
        # covered by start_browser's own failure-path release; between the
        # two, the lock can never outlive the process that failed to launch
        # or that closed cleanly.
        try:
            await self._close_browser_steps()
        finally:
            if self._locked_user_data_dir is not None:
                release_profile_lock(self._locked_user_data_dir)
                self._locked_user_data_dir = None

    async def _close_browser_steps(self):
        if self.context:
            # Only persist session.json when this run actually confirmed an
            # authenticated session. close_browser also runs on crash recovery
            # and failed-login teardowns, where the context may be logged out —
            # writing its storage_state then would clobber a still-good
            # session.json with a degraded one. Belt-and-braces: is_authenticated
            # is set once by login() and only cleared at the specific call sites
            # that detect a mid-run captcha/checkpoint/logout
            # (_mark_session_compromised); if a call site is ever missed, check
            # the page's own URL directly before writing.
            if self.is_authenticated and self._page_on_challenge_wall():
                logger.info(
                    "Skipping session.json write on close: the page is sitting "
                    "on a login/challenge URL despite is_authenticated=True; "
                    "preserving the existing session file"
                )
            elif self.is_authenticated:
                await self._close_step(
                    self._write_session_state(self.context),
                    "storage_state",
                )
            else:
                logger.info(
                    "Skipping session.json write on close: no authenticated "
                    "session was confirmed this run; preserving the existing "
                    "session file"
                )
            await self._close_step(self.context.close(), "context.close")
        if self.browser:
            await self._close_step(self.browser.close(), "browser.close")
        if self.playwright:
            await self._close_step(self.playwright.stop(), "playwright.stop")

    async def login(self, progress_callback: Callable | None = None) -> bool:
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
                try:
                    challenge_exc.evidence = await capture_error_context(
                        self.page,
                        "login_feed_probe_challenge",
                        exc=challenge_exc,
                        context={"landed_url": current_url},
                    )
                except Exception as capture_exc:  # pragma: no cover - defensive
                    logger.error(
                        "Evidence capture failed for login_feed_probe_challenge: %s",
                        capture_exc,
                    )
                raise challenge_exc

            # "Already logged in?" — URL-only. The feed probe stayed on a non-wall
            # URL, and an unauthenticated session is always redirected away from
            # /feed to a /login or /authwall (caught as ``wall`` above), so
            # remaining on the feed URL is itself proof of an active session. We
            # deliberately do NOT require a logged-in nav DOM landmark here:
            # LinkedIn's SDUI rewrites those class/data-test hooks, and the brittle
            # landmark check was misreading valid persistent sessions as logged out
            # and re-driving them through /login. (A /checkpoint in progress is a
            # "challenge" wall, so it does not reach here — it is handed to
            # _wait_for_login_redirect below.)
            if wall is None:
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
                    await self._write_session_state(self.context)
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
                    raise LoginFailedException(
                        f"Manual login timed out: {wait_error}"
                    ) from wait_error

            self.is_authenticated = True
            if progress_callback:
                progress_callback("Login completed successfully!")

            # Save session state
            try:
                await self._write_session_state(self.context)
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
            raise LoginFailedException(f"Login failed: {str(e)}") from e

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
        progress_callback: Callable | None = None,
    ) -> list[LinkedInProfile]:
        """Search for LinkedIn profiles based on campaign criteria"""

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        profiles = []
        # New run boundary: clear per-run diagnostics state (anomaly rate-limit
        # counter + page-snapshot ring) so a long-lived CLI process neither
        # leaks the counter nor mixes ring-buffer evidence across campaigns.
        reset_diagnostics_run()

        from .interactions import detect_captcha

        try:
            if progress_callback:
                progress_callback("Starting profile search...")

            # The navigate→verify→paginate scaffolding lives in the shared page
            # walk; here we just harvest each ready page's profiles. Breaking once
            # we have enough closes the walk at its ``yield`` before it advances
            # pagination, so the next page is never loaded once we are satisfied
            # (the old inline loop clicked Next first, loading one extra page at
            # the boundary). A no-results page yields nothing, so ``walked_any``
            # stays False and we report "no results".
            walked_any = False
            async for page_count in self._walk_search_pages(campaign, progress_callback):
                walked_any = True

                # Inline CAPTCHA can render on the results page without a URL
                # bounce (the landing guard only catches URL-level challenges).
                # Mirrors search_and_connect's per-page check (parity, issue).
                if await detect_captcha(self.page):
                    logger.warning("CAPTCHA detected on search results; stopping")
                    captcha_exc = CaptchaDetectedException(
                        "CAPTCHA detected on search results page"
                    )
                    try:
                        captcha_exc.evidence = await capture_error_context(
                            self.page,
                            "search_profiles_captcha",
                            exc=captcha_exc,
                            context={"campaign": campaign.name, "page": page_count},
                        )
                    except Exception as capture_exc:  # pragma: no cover - defensive
                        logger.error(
                            "Evidence capture failed for search_profiles_captcha: %s",
                            capture_exc,
                        )
                    raise captcha_exc

                profiles_before_page = len(profiles)

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

                if len(profiles) >= limit:
                    break

            if progress_callback:
                if not walked_any:
                    progress_callback("Search complete! Found 0 profiles (no results)")
                else:
                    progress_callback(
                        f"Search complete! Found {len(profiles)} profiles"
                    )

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
            if isinstance(challenge, (CaptchaDetectedException, NotAuthenticatedException)):
                self._mark_session_compromised()
            raise
        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Search failed: {str(e)}")
            return profiles

    async def _walk_search_pages(
        self,
        campaign: Campaign,
        progress_callback: Callable | None = None,
    ):
        """Drive the people-search results page-walk, yielding once per ready page.

        Owns the navigate→verify→paginate scaffolding shared by the two harvest
        flows: :meth:`search_profiles` (text-only extraction) and
        :meth:`search_and_connect` (card-handle connect). Per ready results page
        it waits for the result cards, records a ring-buffer snapshot, scrolls
        like a human, then ``yield``s the 1-based page number so the consumer can
        read the page however it needs. Between yields it advances pagination; the
        consumer ``break``ing closes this generator at the ``yield`` (before the
        pagination click), so no extra page is loaded once the consumer is
        satisfied. (The old inline ``while len(profiles) < limit`` loop clicked
        Next before its top-of-loop re-check stopped it, loading one extra page at
        the limit boundary; closing at the yield avoids that.)

        A genuine empty/no-results page yields nothing (clean stop). A
        challenge/wrong-landing bounce from the guard, and a missing result-cards
        selector, propagate to the consumer with their evidence bundle.
        """
        search_params = self._build_search_params(campaign)
        search_url = f"{self.SEARCH_URL}?{search_params}"

        # Guarded navigation: resilient goto (transient-error retry +
        # renderer-crash watchdog + one crash-recovery refresh) -> settle ->
        # surf benign interstitials -> landing guard. A bounce to a
        # challenge/login wall or a wrong path raises a typed exception with an
        # evidence bundle; ``strict_path`` asserts we are still on the
        # people-search results path even when LinkedIn rewrites the rest of the
        # URL. navigate_guarded returns the page it finished on (a fresh one if a
        # crash was recovered), so rebind self.page.
        self.page = await navigate_guarded(
            self.page,
            search_url,
            strict_path="/search/results/people",
            context={"campaign": campaign.name},
            **self._nav_kwargs(),
        )
        # Disambiguate "empty" from "not rendered yet": race the readiness
        # selector against the explicit no-results marker and, if neither
        # renders, reload ONCE before trusting "no results" (re-checking for a
        # challenge that replaced the listing). Only a still-missing listing
        # after the reload fails loud through the registry (evidence bundle +
        # raise). A genuine empty/no-results page returns False — yield nothing so
        # the consumer stops cleanly rather than waiting on the result-cards
        # selector that will never appear.
        rendered = await verify_listing_rendered(
            self.page,
            sel.SEARCH_RESULTS_READY,
            empty_selector=sel.SEARCH_NO_RESULTS.css,
            ready_timeout_ms=15000,
            context={"campaign": campaign.name},
        )
        if not rendered:
            logger.info("Search returned no results for campaign %r", campaign.name)
            return

        page_count = 0
        max_pages = 10  # Limit to prevent infinite loops

        while page_count < max_pages:
            page_count += 1

            if progress_callback:
                progress_callback(f"Scanning page {page_count}...")

            # Wait for profiles to load (legacy attribute or SDUI links)
            try:
                await self.page.wait_for_selector(
                    sel.SEARCH_RESULT_CARDS.css,
                    timeout=10000,
                )
            except TimeoutError:
                selector_exc = SelectorNotFoundException(
                    "Profile elements not found on search results page",
                    selector=sel.SEARCH_RESULT_CARDS.css,
                    timeout=10000
                )
                try:
                    selector_exc.evidence = await capture_error_context(
                        self.page,
                        "search_results_selector_missing",
                        exc=selector_exc,
                        context={"selector": sel.SEARCH_RESULT_CARDS.css},
                    )
                except Exception as capture_exc:  # pragma: no cover - defensive
                    logger.error(
                        "Evidence capture failed for "
                        "search_results_selector_missing: %s",
                        capture_exc,
                    )
                raise selector_exc  # noqa: B904 — re-raise the original; capture error already logged

            # Record the landed search page into the rolling ring buffer so a
            # later failure can be traced back through how we got here.
            await snapshot_page(self.page, page_count - 1)

            # Scroll the results like a human before harvesting them, so the page
            # isn't read instantly the way a scraper would.
            await scroll_down(self.page)

            yield page_count

            # Check for next page. Absence is the normal end-of-results signal, so
            # this is a non-required locate (no fail-loud); the registry handles
            # the EN/ES aria-label → SDUI text fallback ordering and warns if it
            # drifts off the primary.
            next_button = await sel.PAGINATION_NEXT.locate(self.page)
            if next_button and not await next_button.is_disabled():
                await next_button.scroll_into_view_if_needed()
                # Natural mouse move to the pagination control before clicking.
                await self._throttle_action()
                await move_to_and_click(self.page, next_button, click_timeout=10_000)
                await self.page.wait_for_timeout(3000)  # Wait for page load
            else:
                break

    async def search_and_connect(
        self,
        campaign: Campaign,
        limit: int = 100,
        progress_callback: Callable | None = None,
        max_sends: int | None = None,
        stop_event: Any | None = None,
    ) -> dict[str, int]:
        """Search and connect from the result cards in a single pass.

        The card-first path for issue #25: walk the people-search results **once**
        and, per card, send the invitation straight from the results page via
        :meth:`_attempt_connect` — clicking the card's Connect control opens the
        invitation modal in place (no per-profile navigation, lower bot-detection
        signal). Cards already showing Pending are recorded and skipped; cards with
        no actionable Connect control are deferred to a profile-page pass at the end
        (:meth:`send_connection_requests`).

        There is intentionally **no separate "collect everything first" scan** —
        the whole point of the feature is to connect from the results page directly.
        Card handles are valid only until the walk paginates, so each page's cards
        are drained before advancing (Connect clicks open an overlay modal and never
        navigate). The persisted daily cap is shared with the fallback pass, and a
        day-full / LinkedIn weekly-limit-modal / inline-CAPTCHA / challenge stop
        ends the run.

        Trade-off: with no pre-scan, a renderer wedge mid-walk stops the run with the
        later (un-scanned) pages unprocessed; the wedged card itself and the
        no-control cards still get the profile-page fallback.

        ``limit`` caps the *scan* (unique result cards examined); ``max_sends``,
        when given, additionally caps the *invitations sent* this call (confirmed
        + ambiguous sends), across both the card pass and the profile-page
        fallback. The persisted daily cap still applies on top.

        Returns the same aggregate shape as :meth:`send_connection_requests`, plus
        ``scanned`` (unique cards seen across the result pages) and
        ``stopped_reason`` (``"captcha"``/``"challenge"`` when the run was cut
        short by an account-safety stop, ``"cancelled"`` on a user stop request,
        else ``None``) so callers can tell a protective stop apart from a clean
        empty result.

        ``stop_event``, when given, is a ``threading.Event``-like flag (anything
        with ``is_set()``) polled **between profiles** — never inside
        :meth:`_attempt_connect`, so the irreversible reserve→click→send tail
        (issues #31/#39) always completes for the profile in flight. Once set,
        the run stops at the next safe point (the fallback pass included) and
        returns the normal partial summary with ``stopped_reason="cancelled"``.
        """
        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        from .interactions import detect_captcha

        automation_settings = self.settings.get_automation_settings()
        daily_limit = self._effective_daily_limit(campaign, automation_settings)

        sent_count = 0
        possibly_sent_count = 0  # ambiguous sends that consumed a slot (issue #31)
        failed_count = 0
        existing_count = 0
        seen_urls: set = set()
        fallback_profiles: list[LinkedInProfile] = []
        scan_done = False  # stop walking result pages
        stop_all = False  # also skip the profile-page fallback pass
        stopped_reason: str | None = None  # set on an account-safety stop
        # Consecutive "modal_not_found" streak (see _MODAL_NOT_FOUND_ABORT_THRESHOLD).
        consecutive_modal_not_found = 0
        modal_not_found_exhausted = False

        # Inter-session cooldown notice (advisory; mirrors the profile path).
        self._emit_cooldown_notice(automation_settings, progress_callback)

        # New run boundary: clear per-run diagnostics state (see search_profiles).
        reset_diagnostics_run()

        try:
            async for _page in self._walk_search_pages(campaign, progress_callback):
                # Page boundary is also a safe stop point (issue #43) — without
                # this, a run of card-less result pages would delay the stop.
                if stop_event is not None and stop_event.is_set():
                    if progress_callback:
                        progress_callback(
                            "Stop requested — ending the run at a safe point"
                        )
                    stopped_reason = "cancelled"
                    stop_all = True
                    break
                # Inline CAPTCHA can render on the results page without a URL
                # bounce (the landing guard only catches URL-level challenges).
                # Mirror the profile path's per-navigation detect_captcha.
                if await detect_captcha(self.page):
                    logger.warning("CAPTCHA detected on search results; stopping")
                    if progress_callback:
                        progress_callback(
                            "⚠️ CAPTCHA detected — stopping automation to protect "
                            "the account"
                        )
                    self._mark_session_compromised()
                    stopped_reason = "captcha"
                    stop_all = True
                    break
                # Card handles are valid only until the walk paginates, so act on
                # every card on this page before letting the loop advance.
                for profile, card in await self._extract_profile_cards():
                    # Cooperative cancellation (issue #43): checked between
                    # profiles only, so the profile in flight always finishes
                    # its irreversible send tail before the run winds down.
                    if stop_event is not None and stop_event.is_set():
                        if progress_callback:
                            progress_callback(
                                "Stop requested — ending the run at a safe point"
                            )
                        stopped_reason = "cancelled"
                        scan_done = stop_all = True
                        break
                    url = profile.profile_url
                    # Requested per-run send cap (the `run` subcommand's --max):
                    # counts confirmed + ambiguous sends, NOT cards scanned, so
                    # already-contacted results never consume the budget.
                    if (
                        max_sends is not None
                        and sent_count + possibly_sent_count >= max_sends
                    ):
                        if progress_callback:
                            progress_callback(
                                f"Requested send cap reached ({max_sends} this run)"
                            )
                        scan_done = stop_all = True
                        break
                    if len(seen_urls) >= limit:
                        scan_done = True
                        break
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    # Recompute the local-day key each card (midnight rollover).
                    # Cheap early stop only — the real enforcement is the atomic
                    # reserve in _attempt_connect.
                    today = date.today().isoformat()
                    if self.db_manager.get_daily_connection_count(today) >= daily_limit:
                        if progress_callback:
                            progress_callback(
                                f"Daily connection limit reached "
                                f"({daily_limit}/{daily_limit} used today)"
                            )
                        scan_done = stop_all = True
                        break

                    # Skip targets already in the contact book (any prior run).
                    with self.db_manager.get_session() as session:
                        from sqlmodel import select

                        existing_contact = session.exec(
                            select(Contact).where(Contact.profile_url == url)
                        ).first()
                    if existing_contact:
                        existing_count += 1
                        continue

                    try:
                        connect_button, control_kind = (
                            await self._find_card_connect_control(card)
                        )
                    except Exception as e:
                        # A detached/wedged card read shouldn't kill the run; defer
                        # this one to the resilient profile-page path.
                        logger.warning(
                            "Card connect-control read failed for %s; deferring to "
                            "profile path: %s", profile.name, e,
                        )
                        fallback_profiles.append(profile)
                        continue

                    if control_kind == "pending":
                        logger.info(
                            "Pending invitation already exists for %s (from card)",
                            profile.name,
                        )
                        # upsert (not create): the UniqueConstraint (#39) makes a
                        # plain insert raise if a concurrent same-profile run
                        # already wrote a row; protect_finalized so this never
                        # clobbers that run's confirmed send.
                        self.db_manager.upsert_contact({
                            "campaign_id": campaign.id,
                            "name": profile.name,
                            "profile_url": url,
                            "headline": profile.headline,
                            "location": profile.location,
                            "company": profile.company,
                            "status": ContactStatus.PENDING.value,
                            "notes": "Already sent (found Pending button on card)",
                        }, protect_finalized=True)
                        existing_count += 1
                        if progress_callback:
                            progress_callback(f"⚠️ Already pending for {profile.name}")
                        continue

                    if control_kind != "connect" or not connect_button:
                        # No actionable Connect control on the card — defer to the
                        # profile-page path, which can read controls (and the name
                        # disambiguation) the card view doesn't expose.
                        fallback_profiles.append(profile)
                        continue

                    if progress_callback:
                        progress_callback(f"Connecting with {profile.name} (from card)")

                    # Reserve-before-send → click+modal → send → record (reused from
                    # the profile path; owns the daily-slot lifecycle and bounds its
                    # own click+modal unit, so a watchdog timeout / crash propagates
                    # out here exactly as in send_connection_requests' loop).
                    try:
                        result = await self._attempt_connect(
                            campaign, profile, connect_button, progress_callback
                        )
                    except (CaptchaDetectedException, NotAuthenticatedException) as challenge:
                        logger.warning(
                            "Challenge/login wall during card connect for %s; "
                            "stopping: %s", profile.name, challenge,
                        )
                        if progress_callback:
                            progress_callback(
                                "⚠️ Challenge/login wall detected — stopping "
                                "automation to protect the account"
                            )
                        stopped_reason = (
                            "captcha"
                            if isinstance(challenge, CaptchaDetectedException)
                            else "challenge"
                        )
                        self._mark_session_compromised()
                        scan_done = stop_all = True
                        break
                    except builtins.TimeoutError:
                        # The bounded click+modal wedged (pre-send, so nothing was
                        # delivered); run_bounded refreshed the browser, so the
                        # remaining card handles on this page are stale. Defer THIS
                        # card to the profile path (counted there, not here) and end
                        # the walk. Later un-scanned pages aren't recovered — the
                        # single-pass design trades that for not pre-scanning.
                        logger.warning(
                            "Card-connect watchdog fired for %s; deferring it to the "
                            "profile path and ending the card pass", profile.name,
                        )
                        fallback_profiles.append(profile)
                        scan_done = True
                        break
                    except Exception as e:
                        logger.error("Card connect failed for %s: %s", profile.name, e)
                        if _is_crash_error(e):
                            try:
                                await self._refresh_context()
                            except Exception as refresh_exc:
                                logger.error(
                                    "Browser refresh after crash-shaped card-connect "
                                    "failure failed: %s", refresh_exc,
                                )
                        # Page state uncertain after a crash/refresh; defer this card
                        # to the profile path and end the walk.
                        fallback_profiles.append(profile)
                        scan_done = True
                        break

                    if result.outcome in ("day_full", "limit_reached"):
                        scan_done = stop_all = True
                        break
                    # A concurrent run finalized this profile in the dedup->marker
                    # window, so _attempt_connect aborted without sending. Count it
                    # as already-contacted, not a failure, and move on.
                    if result.outcome == "existing":
                        existing_count += 1
                        continue
                    # "sent" and "possibly_sent" both consumed the reserved slot
                    # (an ambiguous send counts against the cap; #31). Tally them
                    # apart but treat them identically for cap/delay so neither is
                    # re-contacted via the profile fallback.
                    if result.outcome in ("sent", "possibly_sent"):
                        if result.outcome == "sent":
                            sent_count += 1
                        else:
                            possibly_sent_count += 1
                        consecutive_modal_not_found = 0  # a real send breaks the streak
                        # Random delay between connections (mirrors the profile path).
                        # A possibly_sent may have refreshed the browser, so its
                        # delay is a page-independent wall-clock sleep. The wait
                        # is cancellable: it only humanizes the NEXT action,
                        # which a stop cancels anyway (issue #43).
                        delay = random.randint(
                            automation_settings["connection_delay_min"],
                            automation_settings["connection_delay_max"],
                        )
                        await self._cancellable_delay(
                            delay,
                            stop_event,
                            page_based=result.outcome == "sent",
                        )
                        if (
                            result.total_today is not None
                            and result.total_today >= daily_limit
                        ):
                            if progress_callback:
                                progress_callback(
                                    f"Daily connection limit reached "
                                    f"({result.total_today}/{daily_limit} used today)"
                                )
                            scan_done = stop_all = True
                            break
                        # A possibly_sent refreshed the browser mid-walk, so the
                        # remaining card handles on this page are stale. End the
                        # card pass cleanly (later pages are not recovered — the
                        # single-pass design's trade-off, same as a card wedge).
                        if result.outcome == "possibly_sent":
                            scan_done = True
                            break
                        continue
                    # Soft failure (email_required / blocked / modal_not_found /
                    # send_failed): _attempt_connect already recorded the contact
                    # and released the reserved slot. Don't defer — the profile path
                    # would only re-hit the same wall.
                    failed_count += 1
                    if result.outcome == "modal_not_found":
                        consecutive_modal_not_found += 1
                        if (
                            consecutive_modal_not_found
                            >= self._MODAL_NOT_FOUND_ABORT_THRESHOLD
                        ):
                            modal_not_found_exhausted = True
                            scan_done = stop_all = True
                            break

                if scan_done:
                    break

            # Profile-page fallback for cards with no actionable Connect control
            # (and any card the pass deferred on a wedge). send_connection_requests
            # owns its own cap preamble, cooldown notice, per-item watchdogs and
            # backoff; the persisted cap is shared, so the passes can't jointly
            # exceed the daily limit.
            # Hand the fallback pass only what remains of the per-run send
            # budget (None = uncapped, mirroring this pass). An exactly-spent
            # budget skips the fallback outright — nothing could be sent.
            remaining_sends = (
                max_sends - (sent_count + possibly_sent_count)
                if max_sends is not None
                else None
            )
            if (
                not stop_all
                and fallback_profiles
                and (remaining_sends is None or remaining_sends > 0)
            ):
                if progress_callback:
                    progress_callback(
                        f"Visiting {len(fallback_profiles)} profile(s) without a card "
                        "Connect button..."
                    )
                fb = await self.send_connection_requests(
                    campaign,
                    fallback_profiles,
                    progress_callback,
                    max_sends=remaining_sends,
                    stop_event=stop_event,
                )
                sent_count += fb["sent"]
                possibly_sent_count += fb.get("possibly_sent", 0)
                failed_count += fb["failed"]
                existing_count += fb["existing"]
                stopped_reason = stopped_reason or fb.get("stopped_reason")

            self.db_manager.update_campaign_stats(campaign.id)
            if modal_not_found_exhausted:
                # The invitation modal never appeared for
                # _MODAL_NOT_FOUND_ABORT_THRESHOLD profiles in a row — abort
                # loudly instead of returning a normal-looking summary that
                # hides a run which sent nothing useful.
                raise LinkedInAutomationError(
                    "Invitation modal not found "
                    f"{self._MODAL_NOT_FOUND_ABORT_THRESHOLD} times in a row — "
                    "LinkedIn UI language unsupported or markup changed; "
                    "aborting to avoid a useless run"
                )
            return {
                "sent": sent_count,
                "possibly_sent": possibly_sent_count,
                "failed": failed_count,
                "existing": existing_count,
                "total_processed": (
                    sent_count + possibly_sent_count + failed_count + existing_count
                ),
                "scanned": len(seen_urls),
                "stopped_reason": stopped_reason,
            }

        except (
            CaptchaDetectedException,
            NotAuthenticatedException,
            UnexpectedLandingException,
        ) as challenge:
            # The page-walk bounced to a challenge/login wall or the wrong path
            # (evidence already captured). Re-raise so the run stops loudly,
            # mirroring search_profiles.
            logger.warning(
                "Search-and-connect hit a challenge/wrong landing; aborting: %s",
                challenge,
            )
            if progress_callback:
                progress_callback(
                    "⚠️ Challenge or wrong landing detected — stopping to protect "
                    "the account"
                )
            if isinstance(challenge, (CaptchaDetectedException, NotAuthenticatedException)):
                self._mark_session_compromised()
            raise
        # No catch-all here: an unexpected error must propagate to the CLI's failure
        # handler. Swallowing it into a partial-counter return would let
        # run_automation stamp a failed run as ``status="success"``.

    @staticmethod
    def _effective_daily_limit(campaign, automation_settings) -> int:
        """The daily invitation cap actually enforced for this run.

        Delegates to the shared ``cli.helpers.effective_daily_limit`` rule —
        the same one display surfaces use — so what a run enforces can never
        drift from what the UI shows (issue #46).
        """
        return effective_daily_limit(
            getattr(campaign, "daily_limit", None),
            automation_settings["daily_connection_limit"],
        )

    def _emit_cooldown_notice(self, automation_settings, progress_callback) -> None:
        """Warn (advisory only) when a prior run sent within the configured
        inter-session cooldown window.

        Shared by :meth:`send_connection_requests` and :meth:`search_and_connect`
        so the card-first path surfaces the same notice the profile path does
        (issue #25). Advisory only: it never blocks — it warns the user that a
        recent run may warrant waiting before continuing.
        """
        cooldown_seconds = automation_settings.get("connection_cooldown", 0)
        if cooldown_seconds <= 0:
            return
        last_action_at = self.db_manager.get_last_connection_at()
        if last_action_at is None:
            return
        if last_action_at.tzinfo is None:
            last_action_at = last_action_at.replace(tzinfo=UTC)
        elapsed = (datetime.now(UTC) - last_action_at).total_seconds()
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

    async def _cancellable_delay(
        self, seconds: float, stop_event: Any | None, *, page_based: bool
    ) -> None:
        """Humanization wait that ends early once a stop is requested (#43).

        The inter-connection delays and the failure backoff are pure waits —
        they shield the NEXT action, which a stop cancels — so a stop landing
        mid-sleep must not run out the full wait (the backoff alone reaches
        300s). With a ``stop_event`` the wait runs in short slices, polling
        the flag between them; without one it degrades to the original single
        blocking wait. ``page_based`` keeps the sent-path's page-driven
        ``wait_for_timeout`` semantics; the wall-clock (asyncio) variant never
        touches the page, for waits that must survive a dead/refreshing page.
        """
        if stop_event is None:
            if page_based:
                await self.page.wait_for_timeout(seconds * 1000)
            else:
                await asyncio.sleep(seconds)
            return
        deadline = time.monotonic() + seconds
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            slice_s = min(0.5, remaining)
            if page_based:
                await self.page.wait_for_timeout(slice_s * 1000)
            else:
                await asyncio.sleep(slice_s)

    async def send_connection_requests(
        self,
        campaign: Campaign,
        profiles: list[LinkedInProfile],
        progress_callback: Callable | None = None,
        max_sends: int | None = None,
        stop_event: Any | None = None,
    ) -> dict[str, int]:
        """Send connection requests to profiles.

        ``max_sends``, when given, caps the invitations sent this call
        (confirmed + ambiguous sends) on top of the persisted daily
        limit. The returned dict carries ``stopped_reason``
        (``"captcha"``/``"challenge"`` when the run was cut short to protect
        the account, ``"cancelled"`` on a user stop request, else ``None``).

        ``stop_event``, when given, is a ``threading.Event``-like flag polled
        **between profiles** — never inside :meth:`_attempt_connect` — so a
        stop request lets the in-flight send finish and returns the normal
        partial summary (issue #43).
        """

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        from .interactions import detect_captcha

        automation_settings = self.settings.get_automation_settings()
        daily_limit = self._effective_daily_limit(campaign, automation_settings)
        sent_count = 0
        possibly_sent_count = 0  # ambiguous sends that consumed a slot (issue #31)
        failed_count = 0
        existing_count = 0
        stopped_reason: str | None = None  # set on an account-safety stop
        # Consecutive "modal_not_found" streak (see _MODAL_NOT_FOUND_ABORT_THRESHOLD).
        consecutive_modal_not_found = 0
        modal_not_found_exhausted = False

        # Persisted, restart-safe daily cap. The count is keyed by the local
        # day, so quitting and reopening the CLI cannot blow past the limit,
        # and the counter self-clears when a new local day begins.
        today = date.today().isoformat()
        already_sent_today = self.db_manager.get_daily_connection_count(today)

        # Optional inter-session cooldown: if a previous run sent a request
        # within the configured window, warn the user before continuing.
        self._emit_cooldown_notice(automation_settings, progress_callback)

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
                "possibly_sent": 0,
                "failed": 0,
                "existing": 0,
                "total_processed": 0,
                "stopped_reason": None,
            }

        # Backoff state: repeated failures may signal a restricted account.
        consecutive_failures = 0
        backoff_base_seconds = 5
        backoff_cap_seconds = 300

        for i, profile in enumerate(profiles):
            # Cooperative cancellation (issue #43): between profiles only, so
            # the in-flight reserve→click→send tail is never interrupted.
            if stop_event is not None and stop_event.is_set():
                if progress_callback:
                    progress_callback(
                        "Stop requested — ending the run at a safe point"
                    )
                stopped_reason = "cancelled"
                break
            today = date.today().isoformat()
            try:
                # Requested per-run send cap (see search_and_connect): counts
                # confirmed + ambiguous sends, not profiles processed.
                if (
                    max_sends is not None
                    and sent_count + possibly_sent_count >= max_sends
                ):
                    if progress_callback:
                        progress_callback(
                            f"Requested send cap reached ({max_sends} this run)"
                        )
                    break

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
                # The closure reads the CURRENT `profile`; it is awaited within
                # this same iteration, so the late-binding hazard cannot occur.
                async def _navigate_and_read():
                    self.page = await navigate_guarded(
                        self.page,
                        profile.profile_url,  # noqa: B023 — awaited in-iteration
                        check_path=False,
                        context={"profile_url": profile.profile_url},  # noqa: B023
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
                    button, kind = await self._find_connect_control(profile)  # noqa: B023
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
                    self._mark_session_compromised()
                    stopped_reason = "captcha"
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
                        "status": ContactStatus.PENDING.value,
                        "notes": "Already sent (found Pending button)",
                    }
                    # upsert + protect_finalized: the UniqueConstraint (#39) would
                    # make a plain insert raise under a concurrent same-profile
                    # run; never clobber that run's confirmed send.
                    self.db_manager.upsert_contact(
                        contact_data, protect_finalized=True
                    )
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
                    # upsert + protect_finalized: avoid a UniqueConstraint (#39)
                    # IntegrityError under a concurrent same-profile run, and
                    # never downgrade that run's confirmed send.
                    self.db_manager.upsert_contact(
                        contact_data, protect_finalized=True
                    )
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"⚠️ No Connect button for {profile.name}")
                    continue

                # Reserve a slot, click Connect, and send the invitation. The
                # helper owns the daily-slot lifecycle for this attempt: a
                # confirmed send keeps the reserved slot; every other outcome
                # (including a watchdog timeout or crash propagating out of the
                # bounded click) releases it exactly once. The bounded-click
                # watchdog still raises asyncio.TimeoutError out of the helper,
                # caught by the except arm below after the slot was released.
                result = await self._attempt_connect(
                    campaign, profile, connect_button, progress_callback
                )
                if result.outcome in ("day_full", "limit_reached"):
                    break
                # A concurrent run finalized this profile in the dedup->marker
                # window, so _attempt_connect aborted without sending (the durable
                # skip marker already exists). Count it as already-contacted, not
                # a failure.
                if result.outcome == "existing":
                    existing_count += 1
                    continue
                # "sent" and "possibly_sent" both consumed their reserved daily
                # slot, so for cap accounting they are equivalent — an ambiguous
                # send counts against the cap (no drift, no re-contact). Only the
                # tally is split: confirmed sends bump ``sent``, ambiguous ones
                # ``possibly_sent``, so neither is mis-reported as a retryable
                # ``failed``.
                if result.outcome in ("sent", "possibly_sent"):
                    if result.outcome == "sent":
                        sent_count += 1
                    else:
                        possibly_sent_count += 1
                    consecutive_failures = 0  # successful action resets backoff
                    consecutive_modal_not_found = 0  # a real send breaks the streak
                    # A possibly_sent already refreshed the browser if it crashed;
                    # the inter-connection delay is a wall-clock pause, so use
                    # asyncio.sleep when the page may be mid-refresh and
                    # page.wait_for_timeout otherwise (keeps existing behavior).
                    # The wait is cancellable: it only humanizes the NEXT
                    # action, which a stop cancels anyway (issue #43).
                    delay = random.randint(
                        automation_settings["connection_delay_min"],
                        automation_settings["connection_delay_max"],
                    )
                    await self._cancellable_delay(
                        delay,
                        stop_event,
                        page_based=result.outcome == "sent",
                    )
                    # Check the persisted daily limit (cumulative across restarts).
                    if (
                        result.total_today is not None
                        and result.total_today >= daily_limit
                    ):
                        if progress_callback:
                            progress_callback(
                                f"Daily connection limit reached "
                                f"({result.total_today}/{daily_limit} used today)"
                            )
                        break
                    continue
                # Soft failures (email_required, blocked, modal_not_found,
                # send_failed): the helper already recorded the contact and
                # released the reserved slot. These do NOT bump the consecutive-
                # failure backoff counter (only hard exceptions do).
                failed_count += 1
                if result.outcome == "modal_not_found":
                    consecutive_modal_not_found += 1
                    if (
                        consecutive_modal_not_found
                        >= self._MODAL_NOT_FOUND_ABORT_THRESHOLD
                    ):
                        # A bare `break` (not a raise) so it exits the `for`
                        # loop directly without being caught by the `except
                        # Exception` below; the abort is raised once outside
                        # the loop, after campaign stats are updated.
                        modal_not_found_exhausted = True
                        break
                continue

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
                # Mark the protective stop so callers (the `run` subcommand,
                # the TUI) never report this run as a clean success — mirrors
                # the card pass's challenge handler.
                stopped_reason = (
                    "captcha"
                    if isinstance(challenge, CaptchaDetectedException)
                    else "challenge"
                )
                self._mark_session_compromised()
                break
            except builtins.TimeoutError:
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
                    # out of this handler and abort the whole run. Cancellable:
                    # the backoff protects the NEXT attempt, which a stop
                    # cancels anyway, and it reaches 300s — a stop landing
                    # mid-backoff must not wait it out (issue #43).
                    await self._cancellable_delay(
                        wait_seconds, stop_event, page_based=False
                    )
                continue

        # Update campaign statistics
        self.db_manager.update_campaign_stats(campaign.id)

        if modal_not_found_exhausted:
            # The invitation modal never appeared for
            # _MODAL_NOT_FOUND_ABORT_THRESHOLD profiles in a row — abort
            # loudly instead of returning a normal-looking summary that hides
            # a run which sent nothing useful.
            raise LinkedInAutomationError(
                "Invitation modal not found "
                f"{self._MODAL_NOT_FOUND_ABORT_THRESHOLD} times in a row — "
                "LinkedIn UI language unsupported or markup changed; "
                "aborting to avoid a useless run"
            )

        return {
            "sent": sent_count,
            "possibly_sent": possibly_sent_count,
            "failed": failed_count,
            "existing": existing_count,
            "total_processed": (
                sent_count + possibly_sent_count + failed_count + existing_count
            ),
            "stopped_reason": stopped_reason,
        }

    async def _attempt_connect(
        self, campaign, profile, connect_button, progress_callback=None
    ) -> ConnectResult:
        """Reserve a slot, click Connect, send the invitation — the connect core.

        Extracted verbatim from ``send_connection_requests``' per-profile loop so
        the same reserve-before-send → click+modal → send sequence can be reused
        (foundation for issue #25's connect-from-search-card flow). Behavior is
        identical to the inline version: this helper OWNS the daily-slot lifecycle
        for the attempt — it reserves the slot, and its ``finally`` releases the
        slot on every outcome except a confirmed send (which consumes it).

        The bounded click+modal unit raises ``asyncio.TimeoutError`` (or a crash
        exception) on a wedged renderer; those propagate OUT of this method (the
        ``finally`` still releases the reserved slot), so the caller's
        ``except asyncio.TimeoutError`` / ``except Exception`` arms catch them.

        The send/finalize tail (issue #31) is split at the irreversible "Send"
        click: a wedge BEFORE the click propagates out (slot released, safe to
        retry); a wedge AFTER it is captured here as a conservative
        ``possibly_sent`` (slot KEPT, contact recorded non-retryable) rather than
        released as a plain ``failed`` — the invite may already be out.

        Returns a :class:`ConnectResult` whose ``outcome`` is one of "day_full",
        "email_required", "blocked", "modal_not_found", "send_failed",
        "limit_reached", "sent", or "possibly_sent". ``total_today`` is set on
        both "sent" and "possibly_sent" (each consumes its reserved slot).
        """
        today = date.today().isoformat()
        automation_settings = self.settings.get_automation_settings()
        daily_limit = self._effective_daily_limit(campaign, automation_settings)

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
            return ConnectResult("day_full")

        # Tracks whether this attempt has consumed (vs. merely reserved) its
        # slot; the finally gives back any slot a non-send outcome left reserved.
        slot_consumed = False
        # Per-attempt ownership token for the pre-send ``reserved`` marker (#39
        # concurrency). Stamped on this attempt's reservation so a retryable
        # cleanup/downgrade in a concurrent attempt on the same profile can never
        # erase/clobber a reservation THIS attempt may already have clicked Send
        # on (and vice versa).
        reservation_token = uuid.uuid4().hex
        try:
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
                # upsert + protect_finalized: avoid a UniqueConstraint (#39)
                # IntegrityError under a concurrent same-profile run; never
                # downgrade that run's confirmed send.
                self.db_manager.upsert_contact(
                    contact_data, protect_finalized=True
                )
                if progress_callback:
                    progress_callback(f"❌ Email required for {profile.name}")
                await random_wait(self.page, min_ms=1000, max_ms=2000)
                return ConnectResult("email_required")

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
                # upsert + protect_finalized: avoid a UniqueConstraint (#39)
                # IntegrityError under a concurrent same-profile run; never
                # downgrade that run's confirmed send.
                self.db_manager.upsert_contact(
                    contact_data, protect_finalized=True
                )
                if progress_callback:
                    progress_callback(f"⚠️ Invitation blocked for {profile.name} (cooldown)")
                await random_wait(self.page, min_ms=1000, max_ms=2000)
                return ConnectResult("blocked")

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
                # upsert + protect_finalized: avoid a UniqueConstraint (#39)
                # IntegrityError under a concurrent same-profile run; never
                # downgrade that run's confirmed send.
                self.db_manager.upsert_contact(
                    contact_data, protect_finalized=True
                )
                if progress_callback:
                    progress_callback(f"❌ Invitation modal not found for {profile.name}")
                await random_wait(self.page, min_ms=1000, max_ms=2000)
                return ConnectResult("modal_not_found")

            # Send without a personalized note. LinkedIn gates custom notes
            # behind Premium (and a small free quota); attempting "Add a note"
            # leads to an upsell with no note field, so "Send without a note"
            # is the reliable path that always delivers the invitation.
            if campaign.message_template and campaign.message_template.strip():
                logger.info(
                    "Campaign has a message template, but custom notes require "
                    "LinkedIn Premium; sending without a note"
                )

            # === Resilient send/finalize tail (issue #31) ===
            #
            # The send tail is split at the irreversible "Send" click so a
            # renderer wedge cannot hang the loop indefinitely AND a watchdog
            # firing around the send cannot mis-account a real delivery:
            #
            #   1. PRE-CLICK (safe to bound): locating the send control is just
            #      ``locator.count()`` reads — which a crashed renderer can wedge
            #      forever (count() carries no timeout) — so it runs under
            #      run_bounded. A timeout here fired BEFORE anything irreversible,
            #      so it propagates out and the caller skips the profile and the
            #      ``finally`` releases the reserved slot (safe to retry).
            #
            #   2. THE CLICK + POST-CLICK CHECKS (must NOT be naively bounded):
            #      the click is irreversible, and the weekly-limit check after it
            #      is again wedge-prone page work. ``send_click_attempted`` is set
            #      the instant before the click, so any timeout/crash from the
            #      click onward is resolved conservatively as "possibly_sent":
            #      assume the invite went out, KEEP the reserved slot (no cap
            #      drift), record the contact non-retryable (no re-contact), and
            #      refresh the wedged browser — instead of releasing the slot and
            #      marking a plain retryable ``failed``.
            send_click_attempted = False

            async def _locate_send_control():
                send_no_note = self.page.locator(sel.INVITE_SEND_NO_NOTE.css).first
                send_exact = self.page.locator(sel.INVITE_SEND.css).first
                if await send_no_note.count():
                    return send_no_note
                return send_exact

            # Pre-click: bound the control lookup. A wedge here is pre-send, so
            # let TimeoutError propagate (caller skips, finally releases).
            send_target = await run_bounded(
                _locate_send_control(),
                timeout_s=self.settings.get_navigation_settings()[
                    "interaction_watchdog_s"
                ],
                recover=self._recover,
                label=f"send-locate:{profile.name}",
            )

            # Durable pre-send skip marker (issue #39). Persist a per-profile
            # row BEFORE the irreversible click so the durable "don't re-contact"
            # marker does NOT hinge on a post-send write succeeding. Without this,
            # a post-send contact-write failure (DB locked / disk full) keeps the
            # slot for THIS run but leaves no row for a FUTURE run to skip on, so
            # it could re-contact someone already (possibly) invited — the exact
            # harm #31 prevents, displaced to a later run. Written as the
            # dedicated ``reserved`` status (#39 retry), NOT ``possibly_sent``: a
            # reservation marker is a skip-key for future runs but no invite is
            # known to be out yet, so it must stay clobberable by a retryable
            # cleanup and must NOT count as a sent invite in stats. Keeping it
            # distinct from ``possibly_sent`` (which now unambiguously means "the
            # invite may be out, never delete") closes the concurrency hazard
            # where a same-profile cleanup could delete the durable marker of an
            # invite that already went out. The post-click handlers below
            # reconcile it to its true final status (``sent``, ``possibly_sent``
            # on an ambiguous send, downgraded to ``found`` on a clean
            # send-failure, or deleted if the invite was provably NOT sent at the
            # weekly limit). This write runs BEFORE the click boundary: if IT
            # fails nothing irreversible has happened yet, so we abort to a clean
            # retryable ``send_failed`` and the finally releases the slot — never
            # click without a durable marker.
            presend_marker = {
                "campaign_id": campaign.id,
                "name": profile.name,
                "profile_url": profile.profile_url,
                "headline": profile.headline,
                "location": profile.location,
                "company": profile.company,
                "status": "reserved",
                "reservation_token": reservation_token,
                "notes": (
                    "Reserved before Send click (durable skip marker #39); "
                    "reconciled after send"
                ),
            }
            try:
                # protect_finalized: if a concurrent run on this same profile
                # already recorded a real outcome, don't downgrade it to a
                # reservation marker — the existing durable row stands.
                # protect_other_reservation: don't steal a live reservation a
                # concurrent attempt already holds (it may have clicked Send).
                marker_row = self.db_manager.upsert_contact(
                    presend_marker,
                    protect_finalized=True,
                    protect_other_reservation=True,
                )
            except Exception as marker_exc:
                # Could not persist the durable marker. Nothing irreversible has
                # happened, so do NOT click: a send with no durable skip marker is
                # exactly the gap #39 closes. Abort to a clean retryable
                # send_failed; the finally releases the reserved slot.
                logger.error(
                    "Could not persist pre-send marker for %s; skipping send to "
                    "avoid an unrecorded invite: %s",
                    profile.name,
                    marker_exc,
                )
                if progress_callback:
                    progress_callback(
                        f"❌ Skipped {profile.name}: could not persist send marker"
                    )
                return ConnectResult("send_failed")

            # A concurrent run on this same profile may have finalized it in the
            # window between this run's dedup check and the marker write. In that
            # case protect_finalized left the existing finalized row untouched
            # (our reservation was NOT written), so clicking Send now would
            # double-contact someone already (possibly) invited — exactly the
            # harm #39 fixes. The durable skip marker already exists, so abort
            # WITHOUT clicking; the finally releases our reserved slot.
            if marker_row is not None and self.db_manager._is_finalized_contact(
                marker_row
            ):
                logger.info(
                    "Profile %s was finalized (status=%s) by a concurrent run "
                    "before the pre-send marker write; skipping the send to "
                    "avoid a double-contact",
                    profile.name, marker_row.status,
                )
                if progress_callback:
                    progress_callback(
                        f"⚠️ Skipped {profile.name}: already contacted by a "
                        "concurrent run"
                    )
                return ConnectResult("existing")

            # A concurrent attempt may instead hold a LIVE ``reserved`` marker
            # (it reserved first and may already have clicked Send). In that case
            # protect_other_reservation left ITS row untouched, so the returned
            # row carries a different token. Don't click — that would duplicate a
            # send the other attempt is mid-flight on; abort to ``existing`` (the
            # finally releases our slot). When WE own the reservation the tokens
            # match and we fall through to the click.
            if (
                marker_row is not None
                and marker_row.status == "reserved"
                and marker_row.reservation_token != reservation_token
            ):
                logger.info(
                    "Profile %s is reserved by a concurrent attempt; skipping "
                    "the send to avoid a double-contact", profile.name,
                )
                if progress_callback:
                    progress_callback(
                        f"⚠️ Skipped {profile.name}: reserved by a concurrent run"
                    )
                return ConnectResult("existing")

            try:
                logger.info("Clicking 'Send without a note' button")
                # Natural mouse move to the send button before clicking. The
                # click keeps its own failure handling (records the contact and
                # skips), so don't route it through move_to_and_click's JS
                # fallback — only humanize the approach. The move is pre-click
                # and reversible, so a failure here is still a clean
                # ``send_failed`` (the slot is released by the finally).
                await self._throttle_action()
                await move_to_element(self.page, send_target)
                try:
                    # Mark the boundary: from here the action is irreversible, so
                    # an exception out of the click (or anything after it) is
                    # resolved as "possibly_sent", never released as a plain fail.
                    send_click_attempted = True
                    await send_target.click(timeout=5000)
                except Exception as send_error:
                    # The click itself raised. Playwright resolves the click
                    # AFTER the element handled the action, so a raise here almost
                    # always means the click never landed (target detached / not
                    # actionable / its own 5s timeout) — treat it as a clean
                    # pre-send ``send_failed`` and release the slot. A genuine
                    # crash-shaped error is the ambiguous case: the renderer may
                    # have died mid-dispatch, so fall through to "possibly_sent".
                    if _is_crash_error(send_error):
                        raise
                    send_click_attempted = False
                    logger.warning(
                        f"Send click failed for {profile.name}: {send_error}"
                    )
                    # Clean pre-send failure (the click never landed). Downgrade
                    # the durable pre-send marker (#39) from ``reserved`` back to
                    # ``found``: nothing irreversible happened, so the recorded
                    # status must stay the retryable ``found`` this path has always
                    # used. Scoped to OUR reservation token, so it can never
                    # clobber a concurrent attempt's live reservation (which that
                    # attempt may already have clicked Send on) into a retryable
                    # ``found`` that a later run would re-contact (finding 2). A
                    # no-op if our reservation was already reconciled/claimed away.
                    self.db_manager.downgrade_own_reservation_to_found(
                        campaign.id,
                        profile.profile_url,
                        reservation_token,
                        notes="Send button not clickable after clicking Connect",
                    )
                    if progress_callback:
                        progress_callback(
                            f"❌ Send button not clickable for {profile.name}"
                        )
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    return ConnectResult("send_failed")

                await random_wait(self.page, min_ms=2000, max_ms=3000)

                # Post-click weekly-limit check — wedge-prone page work AFTER the
                # irreversible click. Bound it so a crashed renderer can't hang
                # the loop, but resolve a timeout as "possibly_sent" (handled by
                # the except below), not as a released failure.
                limit_reached = await run_bounded(
                    self._handle_invitation_limit_modal(profile),
                    timeout_s=self.settings.get_navigation_settings()[
                        "interaction_watchdog_s"
                    ],
                    recover=self._recover,
                    label=f"send-limit:{profile.name}",
                )
            except Exception as tail_error:
                # A wedge/crash struck after the irreversible click. The invite
                # may already be out, so assume sent on ambiguity: keep the
                # reserved slot, record the contact non-retryable, and let the
                # outer flow continue on the refreshed browser. run_bounded
                # already refreshed on a TimeoutError; refresh here for a
                # crash-shaped raise that escaped it so the page is live again.
                if not send_click_attempted:
                    # Pre-click failure that wasn't a plain send_failed (e.g. the
                    # locate watchdog, or a failure in the reversible
                    # throttle/move window between the marker write and the click
                    # boundary): nothing irreversible happened, so let it
                    # propagate and the finally release the slot — the caller
                    # skips this profile. Clear the durable pre-send marker (#39)
                    # first: the invite was provably NOT sent, so leaving a
                    # ``reserved`` row would wrongly block a legitimate future
                    # re-contact (the opposite harm to the one #39 fixes).
                    # reserved_only: clears ONLY this run's ``reserved`` marker,
                    # never a concurrent run's finalized send OR its durable
                    # ``found``/``failed`` skip record. This is a no-op when the
                    # failure preceded the marker write (e.g. the locate wedge);
                    # best-effort so it never masks the original error. Symmetric
                    # with the weekly-limit clear below.
                    try:
                        self.db_manager.delete_contacts_by_profile(
                            campaign.id, profile.profile_url,
                            reserved_only=True,
                            reservation_token=reservation_token,
                        )
                    except Exception as clear_exc:
                        logger.error(
                            "Failed to clear pre-send marker for %s after a "
                            "pre-click failure (profile may be skipped next "
                            "run): %s", profile.name, clear_exc,
                        )
                    raise
                logger.warning(
                    "Send-tail wedge/crash for %s AFTER the send click "
                    "(%s) — recording 'possibly sent' (slot kept)",
                    profile.name,
                    tail_error,
                )
                if not isinstance(tail_error, asyncio.TimeoutError) and _is_crash_error(
                    tail_error
                ):
                    try:
                        await self._refresh_context()
                    except Exception as refresh_exc:
                        logger.error(
                            "Browser refresh after possibly-sent crash failed: %s",
                            refresh_exc,
                        )
                # Keep the reserved slot BEFORE any further DB write: the
                # irreversible click already fired, so the slot decision must not
                # hinge on the contact-record write succeeding. If the reconcile
                # write raised here (DB locked / disk full) AFTER slot_consumed was
                # set, the finally would otherwise release a slot whose invite may
                # already be out — exactly the mis-accounting #31 prevents.
                slot_consumed = True
                total_today = reserved_count
                contact_data = {
                    "campaign_id": campaign.id,
                    "name": profile.name,
                    "profile_url": profile.profile_url,
                    "headline": profile.headline,
                    "location": profile.location,
                    "company": profile.company,
                    "status": "possibly_sent",
                    "connection_sent_at": datetime.now(UTC),
                    # Authoritative post-click outcome: clear the reservation
                    # token so the finalized row carries no stale owner.
                    "reservation_token": None,
                    "notes": (
                        "Possibly sent: renderer wedged after the Send click; "
                        "assuming sent to avoid re-contact and cap drift"
                    ),
                }
                # Best-effort reconcile of the durable pre-send marker (#39) from
                # ``reserved`` to ``possibly_sent``: the invite may be out, so the
                # row must become non-deletable (``possibly_sent`` is finalized,
                # so a concurrent ``only_unfinalized`` cleanup can no longer erase
                # it — finding 1). The marker was already persisted before the
                # click, so the skip-key survives even if THIS write fails (DB
                # locked / disk full). On failure, fall back to the minimal
                # single-row promotion of the ``reserved`` marker to
                # ``possibly_sent``: a reserved row left behind IS still
                # deletable by a concurrent cleanup, so promoting it to a
                # finalized status closes that residual re-contact window whenever
                # the DB is writable at all. The slot is likewise already kept
                # above, so the cap stays conservative regardless.
                try:
                    self.db_manager.upsert_contact(contact_data)
                except Exception as record_exc:
                    logger.error(
                        "Failed to reconcile possibly_sent contact for %s "
                        "(slot kept; promoting reserved marker as a fallback): %s",
                        profile.name, record_exc,
                    )
                    self.db_manager.promote_reserved_to_possibly_sent(
                        campaign.id, profile.profile_url,
                        reservation_token=reservation_token,
                    )
                # Stamp the cooldown timestamp: we are treating this as a real
                # send, so the inter-session cooldown should reflect it.
                self.db_manager.mark_connection_sent(today)
                if progress_callback:
                    progress_callback(
                        f"⚠️ Possibly sent to {profile.name} (renderer wedged "
                        "after Send) — counted to avoid re-contact"
                    )
                return ConnectResult("possibly_sent", total_today=total_today)

            # Confirmed limit modal (no wedge): real weekly limit reached.
            if limit_reached:
                # The weekly-limit modal means LinkedIn refused the invite — it
                # was provably NOT sent. This outcome is retryable (the finally
                # releases the slot), so clear the durable pre-send marker (#39)
                # so a future run, after the weekly limit resets, can legitimately
                # re-contact this profile. reserved_only: delete ONLY this run's
                # ``reserved`` reservation, never a concurrent run's finalized send
                # OR its durable ``found``/``failed`` skip record on the same
                # profile. Best-effort: if the delete fails the row stays
                # ``reserved``, which is merely over-conservative (skips a
                # still-contactable profile) — never a re-contact.
                try:
                    self.db_manager.delete_contacts_by_profile(
                        campaign.id, profile.profile_url, reserved_only=True,
                        reservation_token=reservation_token,
                    )
                except Exception as clear_exc:
                    logger.error(
                        "Failed to clear pre-send marker for %s after weekly "
                        "limit (profile may be skipped next run): %s",
                        profile.name, clear_exc,
                    )
                if progress_callback:
                    progress_callback("❌ LinkedIn weekly invitation limit reached!")
                return ConnectResult("limit_reached")

            # Success - connection sent. Consume the slot BEFORE the record
            # write: the send already happened, so a record-write failure must
            # not release a slot whose invite is already out (same invariant as
            # the possibly_sent path). reserved_count is the cumulative day total.
            slot_consumed = True
            total_today = reserved_count
            contact_data = {
                "campaign_id": campaign.id,
                "name": profile.name,
                "profile_url": profile.profile_url,
                "headline": profile.headline,
                "location": profile.location,
                "company": profile.company,
                "status": "sent",
                "connection_sent_at": datetime.now(UTC),
                # Authoritative post-click outcome: clear the reservation token so
                # the finalized row carries no stale owner.
                "reservation_token": None,
                "notes": None,
            }
            # Reconcile the durable pre-send marker (#39) from ``reserved`` to the
            # confirmed ``sent`` status via upsert (no protect_finalized: this is
            # the authoritative post-click outcome and must win over the
            # reservation). Best-effort: the marker already persisted before the
            # click. On failure, fall back to promoting the ``reserved`` marker to
            # ``possibly_sent`` (a reserved row left behind is still deletable by a
            # concurrent cleanup; promoting it to a finalized status keeps it a
            # protected skip-key — finding 1). The slot is already kept so the cap
            # stays conservative; the only loss on a failed write is the less
            # precise ``possibly_sent`` label instead of ``sent``.
            try:
                self.db_manager.upsert_contact(contact_data)
            except Exception as record_exc:
                logger.error(
                    "Failed to reconcile sent contact for %s "
                    "(slot kept; promoting reserved marker as a fallback): %s",
                    profile.name, record_exc,
                )
                self.db_manager.promote_reserved_to_possibly_sent(
                    campaign.id, profile.profile_url,
                    reservation_token=reservation_token,
                )
            # Stamp the cooldown timestamp now (only on a real send, not on
            # reservation), so a failed send never triggers a false cooldown.
            self.db_manager.mark_connection_sent(today)
            logger.info(f"Successfully sent connection request to {profile.name}")

            if progress_callback:
                progress_callback(f"✅ Sent connection request to {profile.name}")
                progress_callback(
                    f"📊 {total_today}/{daily_limit} used today"
                )

            return ConnectResult("sent", total_today=total_today)
        finally:
            # Give back a reserved slot that wasn't consumed by a confirmed send
            # (email-required, blocked, modal-not-found, failed send) or by a
            # conservative "possibly_sent" (which sets slot_consumed). A wedge
            # BEFORE the irreversible click propagates out here with
            # slot_consumed still False, so its slot is released; a wedge AFTER
            # the click is captured as possibly_sent above (slot kept).
            if not slot_consumed:
                self.db_manager.release_daily_slot(today)

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
            geo_urn = str(geo_urn).strip()
            if not (geo_urn.isascii() and geo_urn.isdigit()):
                # A geoUrn is numeric. Percent-encode anything else so a
                # malformed campaign value cannot break out of the ["..."]
                # wrapper or inject extra query params.
                logger.warning(
                    "Non-numeric geo_urn %r in campaign; percent-encoding it",
                    geo_urn,
                )
                geo_urn = urllib.parse.quote(geo_urn, safe="")
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
            network = str(network).strip()
            # Expected shape: ["F"] / ["F","S"] — a bracketed list of short
            # uppercase degree codes. Percent-encode anything else whole so a
            # malformed campaign value cannot corrupt the URL or inject params.
            if not re.fullmatch(r'\["[A-Z]{1,2}"(?:,"[A-Z]{1,2}")*\]', network):
                logger.warning(
                    "Unexpected network filter %r in campaign; percent-encoding it",
                    network,
                )
                network = urllib.parse.quote(network, safe="")
            params.append(f"network={network}")

        # Origin - use FACETED_SEARCH as per LinkedIn's current format
        params.append("origin=FACETED_SEARCH")

        return "&".join(params)

    async def search_location(self, query: str) -> list[dict[str, str]]:
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
    ) -> list[dict[str, str]]:
        """Resolve location names to geoUrn codes by driving the search filter UI.

        Each suggestion is clicked and applied so its geoUrn appears in the
        results URL, then the page is reset for the next suggestion.
        """
        base_url = f"{self.SEARCH_URL}?origin=FACETED_SEARCH"
        results: list[dict[str, str]] = []
        total_options: int | None = None
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

    @classmethod
    def _parse_card_profile(
        cls, href: str, text: str | None
    ) -> LinkedInProfile | None:
        """Build a :class:`LinkedInProfile` from one card's href + visible text.

        Shared by the text-only SDUI extractor (:meth:`_extract_profiles_new_ui`)
        and the handle-returning card extractor (:meth:`_extract_profile_cards`):
        the first non-empty line is ``"Name • 2º"`` (degree marker after the
        bullet); action labels are dropped so the headline/location fall on the
        right lines. Returns ``None`` when no usable name can be parsed.
        """
        lines = [s.strip() for s in (text or "").split("\n") if s.strip()]
        if not lines:
            return None
        name = lines[0].split("•")[0].strip()
        if not name:
            return None
        rest = [ln for ln in lines[1:] if ln.lower() not in cls._CARD_ACTION_WORDS]
        return LinkedInProfile(
            name=name,
            profile_url=href,
            headline=rest[0] if rest else None,
            location=rest[1] if len(rest) > 1 else None,
        )

    async def _enumerate_card_handles(self) -> list:
        """Return one ElementHandle per result card on the current page.

        Tries :data:`SEARCH_RESULT_CARD`'s candidates most-stable-first and
        returns the first that matches any node: the SDUI result-list items
        (``main div[role="list"] > div``, verified against a real 2026 DOM dump),
        then the legacy ``data-chameleon-result-urn`` anchor, then a broad
        ``main [componentkey]`` drift fallback. :meth:`_extract_profile_cards`
        filters these to the ones that actually carry a ``/in/`` profile link and
        dedups by URL, so an over-broad fallback still yields the right cards.
        Handles detach on navigation, so the caller must use them before the
        page-walk paginates.
        """
        for candidate in sel.SEARCH_RESULT_CARD.candidates:
            handles = await self.page.query_selector_all(candidate)
            if handles:
                return handles
        return []

    async def _extract_profile_cards(self) -> list[tuple]:
        """Harvest ``(LinkedInProfile, card_handle)`` for the current results page.

        The handle-returning sibling of :meth:`_extract_profiles_new_ui`: rather
        than parsing every card inside one ``page.evaluate`` (which discards the
        element handles), it keeps each card's ElementHandle so
        :meth:`_find_card_connect_control` can click that card's Connect control in
        place — no per-profile navigation. Cards with no ``/in/`` link, a duplicate
        href, or no usable name are skipped. Handles detach on navigation, so the
        caller must act on them before the page-walk paginates.
        """
        results: list[tuple] = []
        seen: set = set()
        for card in await self._enumerate_card_handles():
            try:
                link = await card.query_selector("a[href*='/in/']")
                if link is None:
                    continue
                # get_attribute returns the raw (usually relative) href; every
                # other harvest path stores an absolute URL (the SDUI JS path
                # reads the resolved .href property, _extract_profile_info
                # prepends BASE_URL). Normalize here too so the fallback goto and
                # the contact-book dedup compare like for like.
                href = (await link.get_attribute("href") or "").split("?")[0]
                if href and not href.startswith("http"):
                    href = self.BASE_URL + href
                if not href or href in seen:
                    continue
                text = await card.inner_text()
            except Exception:
                # A handle can detach between enumeration and read; skip it,
                # mirroring _find_card_connect_control's per-control guard.
                continue
            profile = self._parse_card_profile(href, text)
            if profile is None:
                continue
            seen.add(href)
            results.append((profile, card))
        return results

    async def _extract_profiles_new_ui(self) -> list[LinkedInProfile]:
        """Extract search results from LinkedIn's SDUI search layout (2026).

        The new layout uses obfuscated class names, so result cards are
        located via the stable ``SearchResults_FirstResult_people``
        componentkey and parsed from their visible text in one JS pass.
        """
        raw = await self.page.evaluate(
            """
            () => {
                // SDUI (2026): results render as <div role="list"> whose direct
                // > div children are the per-person cards. Keep only those that
                // hold a profile link; fall back to the old FirstResult-parent /
                // componentkey enumeration if the role=list shape is gone.
                const hasProfile = c => c.querySelector("a[href*='/in/']");
                let cards = [...document.querySelectorAll('main div[role="list"] > div')]
                    .filter(hasProfile);
                if (!cards.length) {
                    const first = document.querySelector(
                        '[componentkey="SearchResults_FirstResult_people"]'
                    );
                    if (first && first.parentElement) {
                        cards = [...first.parentElement.children];
                    }
                }
                if (!cards.length) {
                    cards = [...document.querySelectorAll('main [componentkey]')]
                        .filter(hasProfile);
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

        profiles = []
        for item in raw:
            profile = self._parse_card_profile(
                item.get("href", ""), "\n".join(item.get("lines") or [])
            )
            if profile is not None:
                profiles.append(profile)
        return profiles

    @staticmethod
    def _normalize(text: str | None) -> str:
        """Casefold, strip accents, and collapse whitespace for comparison."""
        decomposed = unicodedata.normalize("NFKD", text or "")
        no_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
        return " ".join(no_marks.casefold().split())

    @staticmethod
    def _name_matches_exactly(name_norm: str, aria: str) -> bool:
        """True when ``name_norm`` appears in ``aria`` as a delimited phrase.

        Bounded on both sides by a non-word character or the string edge, so
        a short name cannot match merely because it happens to be a substring
        of a longer, unrelated name (e.g. "ana gomez" is a plain substring of
        "juliana gomez", which would wrongly match under naive containment).
        Covers both an aria-label that IS exactly the name (``aria == name``)
        and one where the name is embedded in a template sentence ("Invita a
        {Name} a conectar" / "Invite {Name} to connect").
        """
        if not name_norm:
            return False
        pattern = r"(?:^|\W)" + re.escape(name_norm) + r"(?:$|\W)"
        return re.search(pattern, aria) is not None

    async def _find_connect_control(self, profile: "LinkedInProfile"):
        """Find the Connect/Pending control for THIS profile (SDUI layout, 2026).

        Both the top-card action button and the scroll-activated sticky header
        carry the person's name in their ``aria-label`` (e.g. "Invita a
        {Name} a conectar"), which disambiguates the real action from the
        "People also viewed" sidebar that is full of other Connect buttons.

        Two-pass name match: an exact (delimited-phrase) match is tried first
        across all controls; only when NO control matches exactly does the
        loose substring-containment match run as a fallback. Plain
        containment alone can pick an unrelated, longer name whose aria-label
        happens to contain this profile's (shorter) name as a substring — the
        exact pass avoids that whenever a real match exists, and the fallback
        preserves the previous (more permissive) behavior for aria-label
        shapes the exact pattern doesn't cover.

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
        async def match(keywords, *, exact: bool) -> Any | None:
            controls = await self.page.query_selector_all(
                sel.CONNECT_CONTROL.css
            )
            best = None
            best_y = -1.0
            for ctrl in controls:
                aria = self._normalize(await ctrl.get_attribute("aria-label"))
                name_ok = (
                    self._name_matches_exactly(name_norm, aria)
                    if exact
                    else name_norm in aria
                )
                if name_ok and any(k in aria for k in keywords):
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

        async def match_either_pass(keywords) -> Any | None:
            return await match(keywords, exact=True) or await match(
                keywords, exact=False
            )

        # Stay at the top of the page so the top-card action (visible in a
        # 1080px viewport) is used, with no sticky header / promo overlapping.
        try:
            await self.page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

        # SDUI action controls render shortly after load, so poll a few times.
        for _ in range(5):
            connect = await match_either_pass(("conectar", "connect"))
            if connect:
                return connect, "connect"
            pending = await match_either_pass(("pendiente", "pending"))
            if pending:
                return pending, "pending"
            await self.page.wait_for_timeout(1000)

        return None, "none"

    async def _find_card_connect_control(self, card) -> tuple:
        """Find the Connect/Pending control INSIDE one search-result card.

        The card-scoped sibling of :meth:`_find_connect_control`: same idea, but
        it queries ``CONNECT_CONTROL`` *within* a single card element handle
        instead of across the whole profile page. Because one card is exactly one
        person, there is no name to disambiguate against and no top-card vs.
        sticky-header y-preference to resolve — the first visible Connect/Pending
        control in the card is THE control.

        SDUI action buttons can hydrate a beat after the card shell renders, so —
        like :meth:`_find_connect_control` — re-query and poll a few times before
        giving up. Without it a slow render would classify a connectable card as
        'none' and needlessly defer it to the profile-page path (the very extra
        navigation the card flow exists to avoid).

        Returns ``(handle, kind)`` where kind is 'connect', 'pending' or 'none'.
        """
        async def match(keywords):
            # Re-query each poll: the action controls may not be in the DOM yet on
            # the first pass. A handle can also detach between enumeration and
            # read; skip it, mirroring the profile-page sibling.
            controls = await card.query_selector_all(sel.CONNECT_CONTROL.css)
            for ctrl in controls:
                try:
                    if not await ctrl.is_visible():
                        continue
                    aria = self._normalize(await ctrl.get_attribute("aria-label"))
                except Exception:
                    continue
                if any(k in aria for k in keywords):
                    return ctrl
            return None

        for attempt in range(3):
            connect = await match(("conectar", "connect"))
            if connect:
                return connect, "connect"
            pending = await match(("pendiente", "pending"))
            if pending:
                return pending, "pending"
            if attempt < 2:
                await self.page.wait_for_timeout(500)
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

    async def _extract_profile_info(self, element) -> LinkedInProfile | None:
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

    async def smart_connection_checker(
        self,
        campaign_id: int,
        progress_callback: Callable | None = None,
        stop_event: Any | None = None,
    ) -> dict[str, int]:
        """Smart checker that monitors LinkedIn connections page for newly accepted connections"""
        from .checker import smart_connection_checker

        return await smart_connection_checker(
            self, campaign_id, progress_callback, stop_event=stop_event
        )

    async def extract_detailed_profile(
        self, profile_url: str, progress_callback: Callable | None = None
    ) -> dict[str, Any]:
        """Extract comprehensive profile data using enhanced scraping"""
        from .scraping import collect_public_information, get_contact_info, get_open_to_work_status

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        try:
            if progress_callback:
                progress_callback("Extracting detailed profile data...")

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
                "extracted_at": datetime.now(UTC),
            }

            if progress_callback:
                progress_callback("✅ Extracted profile data successfully")

            return profile_data

        except Exception as e:
            logger.error(f"Failed to extract profile data: {str(e)}")
            if progress_callback:
                progress_callback(f"❌ Failed to extract profile data: {str(e)}")
            return {}
