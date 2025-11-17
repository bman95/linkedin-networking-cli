import asyncio
import logging
import random
import subprocess
import time
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


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("linkedin_automation")


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


async def random_wait(
    page: Page, *, min_ms: int = 3_000, max_ms: int = 7_000, verbose: bool = True
) -> None:
    """Wait a random time to simulate human latency."""
    timeout = random.randint(min_ms, max_ms)
    if verbose:
        logger.info("Waiting random time of %.2fs", timeout / 1_000)
    await page.wait_for_timeout(timeout)


def _is_true_limit(modal) -> bool:
    """Check if the modal indicates a true invitation limit."""
    # 1) Robust signal by icon
    if modal.query_selector("svg[data-test-icon='locked']"):
        return True
    # 2) Fallback by heading text (in case they change the icon)
    header_el = modal.query_selector(
        "#ip-fuse-limit-alert__header, h2.ip-fuse-limit-alert__header"
    )
    header = header_el.inner_text().strip().lower() if header_el else ""
    true_texts = {
        "has alcanzado el límite semanal de invitaciones",
        "has alcanzado el límite semanal de invitaciones.",  # with period
        "you've reached the weekly invitation limit",
        "you've reached the weekly invitation limit",
    }
    return any(t in header for t in true_texts)


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
                # Persistent context already has a page, use it instead of creating a new one
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

            # Only create a new page for non-persistent contexts
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

                # Wait for login success
                if progress_callback:
                    progress_callback("Waiting for login confirmation...")

                await self.page.wait_for_selector(
                    "img.global-nav__me-photo", timeout=30000
                )
            else:
                if progress_callback:
                    progress_callback(
                        "No credentials configured. Complete the login manually in the Chrome window."
                    )

                try:
                    await self.page.wait_for_selector(
                        "img.global-nav__me-photo", timeout=300000
                    )
                except Exception as wait_error:
                    logger.error(f"Manual login timed out: {wait_error}")
                    if progress_callback:
                        progress_callback("Manual login timed out before confirmation.")
                    return False

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

        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            if progress_callback:
                progress_callback(f"Login failed: {str(e)}")
            return False

    async def search_profiles(
        self,
        campaign: Campaign,
        limit: int = 100,
        progress_callback: Optional[Callable] = None,
    ) -> List[LinkedInProfile]:
        """Search for LinkedIn profiles based on campaign criteria"""

        if not self.is_authenticated:
            raise Exception("Not authenticated. Please login first.")

        profiles = []

        try:
            # Build search URL
            search_params = self._build_search_params(campaign)
            search_url = f"{self.SEARCH_URL}?{search_params}"

            if progress_callback:
                progress_callback("Starting profile search...")

            await self.page.goto(search_url, timeout=30000)
            await self.page.wait_for_selector(
                ".search-results-container", timeout=10000
            )

            page_count = 0
            max_pages = 10  # Limit to prevent infinite loops

            while len(profiles) < limit and page_count < max_pages:
                page_count += 1

                if progress_callback:
                    progress_callback(f"Scanning page {page_count}...")

                # Wait for profiles to load (use data-chameleon-result-urn attribute)
                await self.page.wait_for_selector(
                    "[data-chameleon-result-urn]", timeout=10000
                )

                # Extract profile information
                profile_elements = await self.page.query_selector_all(
                    "[data-chameleon-result-urn]"
                )

                profiles_on_page = 0
                for element in profile_elements:
                    try:
                        profile = await self._extract_profile_info(element)
                        if profile and len(profiles) < limit:
                            profiles.append(profile)
                            profiles_on_page += 1
                    except Exception as e:
                        logger.warning(f"Failed to extract profile info: {e}")
                        continue

                if progress_callback:
                    progress_callback(
                        f"Found {profiles_on_page} profiles on page {page_count} (Total: {len(profiles)})"
                    )

                # Check for next page
                next_button = await self.page.query_selector(
                    "button[aria-label='Next']"
                )
                if next_button and not await next_button.is_disabled():
                    await next_button.click()
                    await self.page.wait_for_timeout(2000)  # Wait for page load
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

    async def send_connection_requests(
        self,
        campaign: Campaign,
        profiles: List[LinkedInProfile],
        progress_callback: Optional[Callable] = None,
    ) -> Dict[str, int]:
        """Send connection requests to profiles"""

        if not self.is_authenticated:
            raise Exception("Not authenticated. Please login first.")

        automation_settings = self.settings.get_automation_settings()
        sent_count = 0
        failed_count = 0
        existing_count = 0

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
                await random_wait(self.page, min_ms=2000, max_ms=3000)

                # Check if profile is accessible
                logger.info(f"Looking for Connect button for {profile.name}")
                sel_container = await self.page.query_selector("div.pvs-sticky-header-profile-actions")

                if not sel_container:
                    logger.warning(f"Profile actions container not found for {profile.name} - profile may be private or inaccessible")
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Profile not accessible or private",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    continue

                # Strategy: Try to find Connect button directly first, then try More dropdown
                connect_button = None

                # First, try to find Connect button directly visible on the profile
                logger.info("Looking for Connect button directly on profile")
                direct_selectors = [
                    "button:has(span.artdeco-button__text:has-text('Conectar'))",
                    "button:has(span.artdeco-button__text:has-text('Connect'))",
                ]

                for selector in direct_selectors:
                    connect_button = await sel_container.query_selector(selector)
                    if connect_button:
                        try:
                            is_visible = await connect_button.is_visible()
                            if is_visible:
                                logger.info(f"Found Connect button directly visible using selector: {selector}")
                                break
                            else:
                                logger.debug(f"Connect button found but not visible: {selector}")
                                connect_button = None
                        except Exception:
                            connect_button = None

                # If Connect button not found directly, try the "More actions" dropdown
                if not connect_button:
                    logger.info("Connect button not visible directly, looking for 'More actions' dropdown")
                    mas_btn = await sel_container.query_selector("button[aria-label='Más acciones']")
                    if not mas_btn:
                        mas_btn = await sel_container.query_selector("button[aria-label='More actions']")

                    if mas_btn:
                        # Scroll button into view and check if visible
                        try:
                            await mas_btn.scroll_into_view_if_needed()
                            await self.page.wait_for_timeout(500)  # Brief wait after scroll
                            is_visible = await mas_btn.is_visible()
                            if not is_visible:
                                logger.warning(f"More button found but not visible for {profile.name}")
                                mas_btn = None
                        except Exception as e:
                            logger.warning(f"Could not scroll to or verify More button: {e}")
                            mas_btn = None

                    if mas_btn:
                        logger.info("Clicking 'More' button to reveal connection options")
                        await mas_btn.click()

                        # Wait for dropdown to appear and be ready
                        try:
                            await self.page.wait_for_selector('div.artdeco-dropdown__content-inner', timeout=5000, state='visible')
                            logger.info("Dropdown menu is visible")
                        except Exception as e:
                            logger.warning(f"Dropdown didn't appear after clicking More: {e}")

                        await random_wait(self.page, min_ms=1000, max_ms=2000)

                        logger.info("Looking for 'Connect' button in dropdown menu")

                        # Try multiple selectors for the Connect button in dropdown
                        dropdown_selectors = [
                            'div.artdeco-dropdown__content-inner div.artdeco-dropdown__item:has-text("Conectar")',
                            'div.artdeco-dropdown__content-inner div.artdeco-dropdown__item:has-text("Connect")',
                            'div.artdeco-dropdown__content-inner div[role="button"]:has-text("Conectar")',
                            'div.artdeco-dropdown__content-inner div[role="button"]:has-text("Connect")',
                            'div[aria-label*="Invita"][aria-label*="conectar"]',
                            'div[aria-label*="Invite"][aria-label*="connect"]',
                        ]

                        for selector in dropdown_selectors:
                            connect_button = await self.page.query_selector(selector)
                            if connect_button:
                                try:
                                    is_visible = await connect_button.is_visible()
                                    if is_visible:
                                        logger.info(f"Found Connect button in dropdown using selector: {selector}")
                                        break
                                    else:
                                        logger.debug(f"Connect button found but not visible: {selector}")
                                        connect_button = None
                                except Exception:
                                    connect_button = None
                    else:
                        logger.debug("'More' button not found or not visible")

                if not connect_button:
                    # Check if already pending
                    pending_btn = await self.page.query_selector(
                        "main button:has(span.artdeco-button__text:has-text('Pendiente'))"
                    )
                    if not pending_btn:
                        pending_btn = await self.page.query_selector(
                            "main button:has(span.artdeco-button__text:has-text('Pending'))"
                        )

                    if pending_btn:
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
                    else:
                        logger.info(f"No 'Connect' or 'Pending' button found for {profile.name}, probably already connected")
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

                # Click Connect button
                logger.info("Clicking 'Connect' button")
                await connect_button.click()
                await random_wait(self.page, min_ms=3000, max_ms=4500)

                # Check if email is required
                email_label = await self.page.query_selector('label[for="email"]')
                if email_label:
                    logger.info(f"Email request modal detected for {profile.name}. Dismissing...")
                    dismiss_btn = await self.page.query_selector('button[aria-label="Descartar"]')
                    if not dismiss_btn:
                        dismiss_btn = await self.page.query_selector('button[aria-label="Dismiss"]')
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

                # Handle message modal
                send_button = None
                if campaign.message_template and campaign.message_template.strip():
                    # Try to add a note
                    add_note_btn = await self.page.query_selector(
                        "button:has(span.artdeco-button__text:has-text('Añadir una nota'))"
                    )
                    if not add_note_btn:
                        add_note_btn = await self.page.query_selector(
                            "button:has(span.artdeco-button__text:has-text('Add a note'))"
                        )

                    if add_note_btn:
                        logger.info("Clicking 'Add a note' button")
                        await add_note_btn.click()
                        await random_wait(self.page, min_ms=1000, max_ms=1500)

                        try:
                            await self.page.wait_for_selector("textarea", timeout=5000)
                            first_name = profile.name.split()[0]
                            logger.info("Writing personalized message")
                            message = campaign.message_template.format(name=first_name.lower().title())
                            await self.page.fill("textarea", message)
                            await random_wait(self.page, min_ms=1000, max_ms=1500)
                        except Exception as msg_error:
                            logger.warning(f"Failed to add note: {msg_error}")

                    # Look for Send button
                    send_button = await self.page.query_selector("button:has-text('Enviar')")
                    if not send_button:
                        send_button = await self.page.query_selector("button:has-text('Send')")
                else:
                    # Send without note
                    send_button = await self.page.query_selector(
                        "button:has(span.artdeco-button__text:has-text('Enviar sin nota'))"
                    )
                    if not send_button:
                        send_button = await self.page.query_selector(
                            "button:has(span.artdeco-button__text:has-text('Send without a note'))"
                        )
                    if not send_button:
                        # Fallback to generic Send
                        send_button = await self.page.query_selector("button:has-text('Enviar')")
                    if not send_button:
                        send_button = await self.page.query_selector("button:has-text('Send')")

                if not send_button:
                    logger.warning(f"Send button not found for {profile.name}")
                    contact_data = {
                        "campaign_id": campaign.id,
                        "name": profile.name,
                        "profile_url": profile.profile_url,
                        "headline": profile.headline,
                        "location": profile.location,
                        "company": profile.company,
                        "status": "found",
                        "notes": "Send button not found after clicking Connect",
                    }
                    self.db_manager.create_contact(contact_data)
                    failed_count += 1
                    if progress_callback:
                        progress_callback(f"❌ Send button not found for {profile.name}")
                    await random_wait(self.page, min_ms=1000, max_ms=2000)
                    continue

                # Click Send button
                logger.info("Clicking 'Send' button")
                await send_button.click()
                await random_wait(self.page, min_ms=2000, max_ms=3000)

                # Check for invitation limit modal
                modal = await self.page.query_selector("div.artdeco-modal.ip-fuse-limit-alert")
                if modal:
                    if _is_true_limit(modal):
                        # Real limit reached
                        logger.warning(f"Weekly invitation limit reached. Connection not sent to {profile.name}")

                        # Close the modal
                        try:
                            close_btn = await modal.query_selector("button.ip-fuse-limit-alert__primary-action")
                            if close_btn:
                                logger.info("Closing limit reached modal")
                                await close_btn.click()
                                await random_wait(self.page, min_ms=1000, max_ms=2000)
                        except Exception:
                            logger.debug("Could not close limit modal (non-blocking)")

                        # Stop processing
                        if progress_callback:
                            progress_callback("❌ LinkedIn weekly invitation limit reached!")
                        break
                    else:
                        # Just a warning (near limit)
                        logger.info(f"'Near limit' warning for {profile.name}. Closing modal and continuing.")
                        try:
                            close_btn = await modal.query_selector("button.ip-fuse-limit-alert__primary-action")
                            if close_btn:
                                await close_btn.click()
                                await random_wait(self.page, min_ms=1000, max_ms=2000)
                        except Exception:
                            logger.debug("Could not close warning modal (non-blocking)")

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
                logger.info(f"Successfully sent connection request to {profile.name}")

                if progress_callback:
                    progress_callback(f"✅ Sent connection request to {profile.name}")

                # Random delay between connections (human-like behavior)
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
        Search for locations using LinkedIn Voyager API typeahead

        Args:
            query: Location search query (e.g., "San Francisco", "London", "Tokyo")

        Returns:
            List of dicts with keys: 'name' (display name) and 'geoUrn' (code)

        Raises:
            Exception: If not authenticated or request fails
        """
        if not self.is_authenticated:
            raise Exception("Not authenticated. Please login first.")

        if not query or not query.strip():
            return []

        try:
            # Build Voyager API typeahead URL
            query_encoded = urllib.parse.quote(query.strip())
            url = (
                f"{self.BASE_URL}/voyager/api/typeahead/hitsV2"
                f"?keywords={query_encoded}"
                "&origin=OTHER"
                "&q=type"
                "&queryContext=List(geoVersion->3,bingGeoSubTypeFilters->MARKET_AREA|COUNTRY_REGION|ADMIN_DIVISION_1|CITY)"
                "&type=GEO"
            )

            logger.info(f"Searching location: {query}")

            # Make request using Playwright's page context (uses existing cookies/auth)
            response = await self.page.request.get(url)

            if not response.ok:
                logger.warning(f"Location search failed with status: {response.status}")
                return []

            data = await response.json()

            # Parse results
            results = []
            elements = data.get("data", {}).get("elements", [])

            for element in elements:
                # Extract geoUrn from targetUrn (format: "urn:li:fs_geo:90000084")
                target_urn = element.get("targetUrn", "")
                if ":" in target_urn:
                    geo_urn = target_urn.split(":")[-1]
                else:
                    geo_urn = target_urn

                # Extract display name
                text_obj = element.get("text", {})
                name = text_obj.get("text", "") if isinstance(text_obj, dict) else str(text_obj)

                if name and geo_urn:
                    results.append({
                        "name": name.strip(),
                        "geoUrn": geo_urn.strip()
                    })

            logger.info(f"Found {len(results)} locations for '{query}'")
            return results

        except Exception as e:
            logger.error(f"Error searching location: {e}")
            return []

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
            raise Exception("Not authenticated. Please login first.")

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
            raise Exception("Not authenticated. Please login first.")

        try:
            if progress_callback:
                progress_callback(f"Extracting detailed profile data...")

            await self.page.goto(profile_url, timeout=30000)
            await self.page.wait_for_timeout(2000)

            # Collect comprehensive profile information
            profession, location, experience, education = collect_public_information(self.page)

            # Get contact information
            contact_info = get_contact_info(self.page)

            # Check open to work status
            open_to_work = get_open_to_work_status(self.page)

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
        from .interactions import send_connection_request, LimitReachedException

        if not self.is_authenticated:
            raise Exception("Not authenticated. Please login first.")

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

            except LimitReachedException:
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
