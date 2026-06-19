import asyncio
import random
import subprocess
import time
import unicodedata
import urllib.parse
from datetime import datetime, timezone
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
from automation.interactions import random_wait, _is_true_limit
from automation.localization import (
    detect_contact_language,
    is_important_contact,
    select_message_template,
)
from utils.logging import get_logger
from exceptions import (
    NotAuthenticatedException,
    LoginFailedException,
    SelectorNotFoundException,
    RateLimitExceededException,
    CaptchaDetectedException,
)


logger = get_logger(__name__)


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

    async def __aenter__(self):
        """Async context manager entry"""
        await self.start_browser()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        """Async context manager exit"""
        await self.close_browser()

    async def start_browser(self):
        """Initialize Playwright browser with enhanced session management"""
        # Force close any existing Chrome processes
        force_close_chrome()

        self.playwright = await async_playwright().start()
        browser_settings = self.settings.get_browser_settings()

        launch_kwargs: Dict[str, Any] = {
            "headless": browser_settings["headless"],
            "timeout": 60_000,  # Increased timeout
        }

        browser_executable = browser_settings.get("executable_path")
        browser_channel = browser_settings.get("channel")
        user_data_dir = browser_settings.get("user_data_dir")

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
            persistent_kwargs["viewport"] = browser_settings["viewport"]
            logger.info("Using persistent context with user data dir %s", user_data_dir)
            try:
                logger.info("Launching persistent Chrome…")
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir,
                    **persistent_kwargs,
                )
                self.browser = self.context.browser
                # Persistent context already opens a page; reuse it instead
                # of creating a second tab.
                if self.context.pages:
                    self.page = self.context.pages[0]
                    logger.info("Using existing page from persistent context")
            except Exception as persistent_error:
                logger.exception(
                    "Failed persistent context, falling back to transient browser…"
                )
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
                        headless=browser_settings["headless"]
                    )
                else:
                    raise

            # Try to load existing session
            session_path = self.settings.session_path
            if session_path.exists():
                try:
                    self.context = await self.browser.new_context(
                        storage_state=str(session_path),
                        viewport=browser_settings["viewport"],
                    )
                    logger.info("Loaded existing LinkedIn session")
                except Exception as session_error:
                    logger.warning("Failed to load session state: %s", session_error)
                    self.context = await self.browser.new_context(
                        viewport=browser_settings["viewport"]
                    )
                    logger.info("Starting fresh LinkedIn session")
            else:
                self.context = await self.browser.new_context(
                    viewport=browser_settings["viewport"]
                )
                logger.info("Starting fresh LinkedIn session")

        if self.page is None:
            self.page = await self.context.new_page()
            logger.info("Created new page for browser context")

    async def close_browser(self):
        """Close browser and cleanup"""
        if self.context:
            # Save session state
            await self.context.storage_state(path=str(self.settings.session_path))
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def login(self, progress_callback: Optional[Callable] = None) -> bool:
        """Login to LinkedIn with enhanced session detection"""
        try:
            if progress_callback:
                progress_callback("Checking LinkedIn session...")

            # Check if already logged in by attempting to access feed
            # If redirected to login, we need to authenticate
            await self.page.goto(f"{self.BASE_URL}/feed", timeout=30_000, wait_until="domcontentloaded")
            # Give a moment for redirect to happen if not logged in
            await self.page.wait_for_timeout(2000)

            current_url = self.page.url

            # If we're NOT on a login page, we're already logged in
            if "/login" not in current_url and "/uas/login" not in current_url:
                self.is_authenticated = True
                if progress_callback:
                    progress_callback("Session already active on LinkedIn!")
                return True

            # We were redirected to login, proceed with authentication
            if progress_callback:
                progress_callback("Not logged in, proceeding with login...")

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

                await self.page.fill("input#username", email)
                await self.page.fill("input#password", password)

                # Submit login
                await self.page.click("button[type=submit]")

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
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Login failed: {str(e)}")
            raise LoginFailedException(f"Login failed: {str(e)}")

    async def _wait_for_login_redirect(self, timeout_ms: int) -> None:
        """Wait until the page leaves the login/checkpoint flow.

        URL-based detection survives LinkedIn UI redesigns better than
        waiting for a specific nav element.
        """
        def logged_in(url) -> bool:
            value = str(url)
            return not any(
                part in value for part in ("/login", "/uas/", "/checkpoint/")
            )

        await self.page.wait_for_url(logged_in, timeout=timeout_ms)

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

        try:
            # Build search URL
            search_params = self._build_search_params(campaign)
            search_url = f"{self.SEARCH_URL}?{search_params}"

            if progress_callback:
                progress_callback("Starting profile search...")

            await self.page.goto(search_url, timeout=30000)
            # Legacy UI exposes .search-results-container; the SDUI rollout
            # (2026) only renders profile links inside <main>.
            try:
                await self.page.wait_for_selector(
                    ".search-results-container, main a[href*='/in/']", timeout=15000
                )
            except TimeoutError:
                raise SelectorNotFoundException(
                    "Search results not found - LinkedIn page structure may have changed",
                    selector=".search-results-container, main a[href*='/in/']",
                    timeout=15000
                )

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
                        "[data-chameleon-result-urn], main a[href*='/in/']",
                        timeout=10000,
                    )
                except TimeoutError:
                    raise SelectorNotFoundException(
                        "Profile elements not found on search results page",
                        selector="[data-chameleon-result-urn], main a[href*='/in/']",
                        timeout=10000
                    )

                # Legacy UI: structured result elements with a stable attribute
                profile_elements = await self.page.query_selector_all(
                    "[data-chameleon-result-urn]"
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

                # Check for next page (EN/ES aria-labels, then SDUI text button)
                next_button = await self.page.query_selector(
                    "button[aria-label='Next'], button[aria-label='Siguiente']"
                )
                if not next_button:
                    next_button = await self.page.query_selector(
                        "main button:has-text('Siguiente'), main button:has-text('Next')"
                    )
                if next_button and not await next_button.is_disabled():
                    await next_button.scroll_into_view_if_needed()
                    await next_button.click()
                    await self.page.wait_for_timeout(3000)  # Wait for page load
                else:
                    break

            if progress_callback:
                progress_callback(f"Search complete! Found {len(profiles)} profiles")

            return profiles

        except Exception as e:
            logger.error(f"Search failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Search failed: {str(e)}")
            return profiles

    async def _try_send_with_note(self, profile: "LinkedInProfile", note_text: str) -> bool:
        """Attempt to send the invitation with a personalized note.

        Returns True only when the note textarea is reachable, filled and the
        invitation is sent. Returns False when LinkedIn shows the Premium upsell
        (no note field) or the note UI is otherwise unavailable, so the caller
        can fall back to sending without a note.
        """
        note_button = self.page.locator(
            "button:has-text('Añadir una nota'), button:has-text('Add a note')"
        ).first
        if not await note_button.count():
            return False

        try:
            await note_button.click(timeout=5000)

            # On free accounts past the quota, clicking "Add a note" opens an
            # upsell with no textarea; wait briefly and bail out if none appears.
            textarea = self.page.locator("textarea").first
            try:
                await textarea.wait_for(state="visible", timeout=3000)
            except TimeoutError:
                logger.info(
                    f"Note field did not appear for {profile.name} (likely Premium upsell)"
                )
                return False

            await textarea.fill(note_text)
            await random_wait(self.page, min_ms=500, max_ms=1200)

            send_btn = self.page.locator(
                "button:text-is('Enviar'), button:text-is('Send')"
            ).first
            if not await send_btn.count():
                return False
            await send_btn.click(timeout=5000)
            logger.info(f"Sent personalized note to {profile.name}")
            return True
        except Exception as note_error:
            logger.warning(f"Note attempt failed for {profile.name}: {note_error}")
            return False

    async def _dismiss_open_modal(self) -> None:
        """Best-effort close of a leftover modal (e.g. a Premium upsell)."""
        try:
            for dismiss_sel in (
                'button[aria-label="Descartar"]',
                'button[aria-label="Dismiss"]',
                'button[aria-label="Cerrar"]',
                'button[aria-label="Close"]',
            ):
                dismiss_btn = self.page.locator(dismiss_sel).first
                if await dismiss_btn.count():
                    await dismiss_btn.click(timeout=3000)
                    return
            await self.page.keyboard.press("Escape")
        except Exception as dismiss_error:
            logger.debug(f"Could not dismiss modal: {dismiss_error}")

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
        sent_count = 0
        failed_count = 0
        existing_count = 0

        # Backoff state: repeated failures may signal a restricted account.
        consecutive_failures = 0
        backoff_base_seconds = 5
        backoff_cap_seconds = 300

        for i, profile in enumerate(profiles):
            try:
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

                # Navigate to profile
                await self.page.goto(profile.profile_url, timeout=30000)
                await self.page.wait_for_timeout(2000)

                # Stop early if LinkedIn challenges us — pushing through a
                # CAPTCHA is the fastest way to get an account restricted.
                if await detect_captcha(self.page):
                    logger.warning("CAPTCHA detected during connection run; stopping")
                    if progress_callback:
                        progress_callback(
                            "⚠️ CAPTCHA detected — stopping automation to protect the account"
                        )
                    break

                # Find the Connect / Pending control for THIS profile.
                connect_button, control_kind = await self._find_connect_control(profile)

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

                # Click Connect. The top-card control is already in view at
                # scroll-top, so a real Playwright click reaches it and the
                # SDUI opens the invitation modal (a JS click is a last resort
                # for the rare case the control is still occluded).
                logger.info("Clicking 'Connect' button")
                try:
                    await connect_button.click(timeout=5000)
                except Exception as click_error:
                    logger.info(f"Normal click intercepted ({click_error}); using JS click")
                    await connect_button.evaluate("el => el.click()")
                await random_wait(self.page, min_ms=2500, max_ms=4000)

                # Check if email is required to connect (dismiss and skip)
                email_label = await self.page.query_selector('label[for="email"]')
                if email_label:
                    logger.info(
                        f"Email request modal detected for {profile.name}. Dismissing..."
                    )
                    for dismiss_sel in (
                        'button[aria-label="Descartar"]',
                        'button[aria-label="Dismiss"]',
                    ):
                        dismiss_btn = await self.page.query_selector(dismiss_sel)
                        if dismiss_btn:
                            await dismiss_btn.click()
                            break

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

                # The invitation modal renders a moment after the click and is
                # not a standard <dialog>, so locate its buttons by text and
                # poll until they appear. ":text-is" matches the exact label so
                # "Enviar" never collides with "Enviar sin nota" / "Enviar mensaje".
                note_loc = self.page.locator(
                    "button:has-text('Añadir una nota'), button:has-text('Add a note')"
                ).first
                send_no_note_loc = self.page.locator(
                    "button:has-text('Enviar sin nota'), button:has-text('Send without a note')"
                ).first
                send_exact_loc = self.page.locator(
                    "button:text-is('Enviar'), button:text-is('Send')"
                ).first

                blocked = False
                modal_ready = False
                for _ in range(8):
                    # LinkedIn blocks re-inviting for 3 weeks after a withdrawal;
                    # clicking Connect then shows an error toast and no modal.
                    if await self._invitation_blocked_toast():
                        blocked = True
                        break
                    if (
                        await note_loc.count()
                        or await send_no_note_loc.count()
                        or await send_exact_loc.count()
                    ):
                        modal_ready = True
                        break
                    await self.page.wait_for_timeout(1000)

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

                # Personalized notes are scarce (LinkedIn Premium / a small free
                # quota), so only spend them on "important" contacts and pick the
                # template language from the contact's location. Everyone else is
                # sent without a note, which is the path that always delivers.
                note_text = None
                if is_important_contact(campaign, profile.headline, profile.company):
                    template = select_message_template(campaign, profile.location)
                    if template and template.strip():
                        try:
                            note_text = template.format(name=profile.name)
                        except (KeyError, IndexError, ValueError):
                            note_text = template
                        note_lang = detect_contact_language(
                            profile.location, campaign.native_language or "es"
                        )
                        logger.info(
                            f"{profile.name} is an important contact; "
                            f"attempting a note in '{note_lang}'"
                        )

                note_sent = False
                if note_text:
                    note_sent = await self._try_send_with_note(profile, note_text)

                if not note_sent:
                    if note_text:
                        logger.info(
                            "Could not attach a note (Premium/quota); sending without a note"
                        )
                    send_no_note = self.page.locator(
                        "button:has-text('Enviar sin nota'), button:has-text('Send without a note')"
                    ).first
                    send_exact = self.page.locator(
                        "button:text-is('Enviar'), button:text-is('Send')"
                    ).first
                    send_target = send_no_note if await send_no_note.count() else send_exact

                    # A failed note attempt may have left a Premium upsell modal
                    # with no send button; bail out cleanly for this contact.
                    if not await send_target.count():
                        logger.warning(
                            f"No send button available for {profile.name}; skipping"
                        )
                        await self._dismiss_open_modal()
                        contact_data = {
                            "campaign_id": campaign.id,
                            "name": profile.name,
                            "profile_url": profile.profile_url,
                            "headline": profile.headline,
                            "location": profile.location,
                            "company": profile.company,
                            "status": "found",
                            "notes": "Send button unavailable after note upsell",
                        }
                        self.db_manager.create_contact(contact_data)
                        failed_count += 1
                        if progress_callback:
                            progress_callback(f"❌ Could not send to {profile.name}")
                        await random_wait(self.page, min_ms=1000, max_ms=2000)
                        continue

                    logger.info("Clicking 'Send without a note' button")
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
                logger.info(f"Successfully sent connection request to {profile.name}")

                if progress_callback:
                    progress_callback(f"✅ Sent connection request to {profile.name}")

                # Random delay between connections
                delay = random.randint(
                    automation_settings["connection_delay_min"],
                    automation_settings["connection_delay_max"],
                )
                await self.page.wait_for_timeout(delay * 1000)

                # Check daily limits
                if sent_count >= automation_settings["daily_connection_limit"]:
                    if progress_callback:
                        progress_callback("Daily connection limit reached")
                    break

            except Exception as e:
                logger.error(f"Failed to process {profile.name}: {str(e)}")
                failed_count += 1
                consecutive_failures += 1

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
                    await self.page.wait_for_timeout(wait_seconds * 1000)
                continue

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
                "a[aria-label], button[aria-label], [role='button'][aria-label]"
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
        modal = await self.page.query_selector(
            "div.artdeco-modal.ip-fuse-limit-alert, "
            "[data-test-modal-id='ip-fuse-limit-alert'], "
            "dialog:has-text('límite semanal'), dialog:has-text('weekly invitation limit')"
        )
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

    async def send_connection_with_retry(
        self,
        profile_url: str,
        candidate_name: str,
        message_template: Optional[str] = None,
        max_retries: int = 3,
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, Any]:
        """Send connection request with enhanced error handling and retries"""
        from .interactions import send_connection_request
        # RateLimitExceededException is imported at the top from exceptions module

        if not self.is_authenticated:
            raise NotAuthenticatedException("Not authenticated. Please login first.")

        for attempt in range(max_retries):
            try:
                if progress_callback:
                    progress_callback(f"Attempt {attempt + 1}: Connecting with {candidate_name}")

                # Navigate to profile
                await self.page.goto(profile_url, timeout=30000)

                # Send connection request using enhanced interactions
                result = await send_connection_request(
                    self.page,
                    candidate_name,
                    message_template,
                    progress_callback
                )

                if result["success"]:
                    return result
                else:
                    logger.warning(f"Connection attempt {attempt + 1} failed: {result['message']}")
                    if attempt < max_retries - 1:
                        await self.page.wait_for_timeout(random.randint(3000, 6000))

            except RateLimitExceededException:
                # Don't retry on limit reached
                raise
            except Exception as e:
                logger.error(f"Connection attempt {attempt + 1} failed: {str(e)}")
                if attempt < max_retries - 1:
                    await self.page.wait_for_timeout(random.randint(5000, 10000))

        return {
            "success": False,
            "status": "max_retries_exceeded",
            "message": f"Failed after {max_retries} attempts"
        }
