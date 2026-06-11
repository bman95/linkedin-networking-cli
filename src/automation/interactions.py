"""
Page interactions and connection management with human-like behavior.

All functions operate on an async Playwright ``Page`` and must be awaited.
"""

import math
import random
from datetime import datetime
from typing import Optional, Dict, Any
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from utils.logging import get_logger
from exceptions import RateLimitExceededException, CaptchaDetectedException

logger = get_logger(__name__)


async def random_wait(
    page, *, min_ms: int = 3_000, max_ms: int = 7_000, verbose: bool = True
) -> None:
    """Wait a random time to simulate human latency."""
    timeout = random.randint(min_ms, max_ms)
    if verbose:
        logger.info("Waiting random time of %.2fs", timeout / 1_000)
    await page.wait_for_timeout(timeout)


async def scroll_down(page) -> None:
    """Smooth scrolling down to the end of the page with natural behavior."""
    MEAN_STEP = 120
    STEP_JITTER = 40
    MIN_PAUSE = 200
    MAX_PAUSE = 900
    LONG_PAUSE_CHANCE = 0.10
    LONG_PAUSE_MS = (1_500, 3_000)

    logger.info("Scrolling down")

    current = await page.evaluate("window.scrollY")
    viewport = await page.evaluate("window.innerHeight")
    total = await page.evaluate("document.body.scrollHeight")

    while current + viewport < total:
        # Calculate easing progress
        progress = current / total
        easing = 0.5 * (1 - math.cos(math.pi * progress))
        base = MEAN_STEP * (1.5 - easing)

        # Add jitter to step size
        step = max(40, int(random.gauss(base, STEP_JITTER / 3)))
        delta = step

        # Scroll with mouse wheel
        await page.mouse.wheel(0, delta + random.randint(-10, 10))
        current += delta

        # Random pause between scrolls
        pause = random.randint(MIN_PAUSE, MAX_PAUSE)
        if random.random() < LONG_PAUSE_CHANCE:
            pause = random.randint(*LONG_PAUSE_MS)

        await page.wait_for_timeout(pause)

        # Update values
        viewport = await page.evaluate("window.innerHeight")
        total = await page.evaluate("document.body.scrollHeight")
        current = await page.evaluate("window.scrollY")


async def _is_true_limit(modal) -> bool:
    """Check if the modal indicates a true invitation limit."""
    # Check for LinkedIn invitation limit icon
    if await modal.query_selector("svg[data-test-icon='locked']"):
        return True

    # Fallback by heading text (in case they change the icon)
    header_el = await modal.query_selector(
        "#ip-fuse-limit-alert__header, h2.ip-fuse-limit-alert__header"
    )
    header = (await header_el.inner_text()).strip().lower() if header_el else ""

    true_texts = {
        "has alcanzado el límite semanal de invitaciones",
        "has alcanzado el límite semanal de invitaciones.",
        "you've reached the weekly invitation limit",
        "you've reached the weekly invitation limit.",
    }
    return any(t in header for t in true_texts)


async def check_connection_email_required(page) -> bool:
    """Check that email is not required to connect."""
    logger.info("Checking if email is required to connect")
    label = await page.query_selector('label[for="email"]')
    if label:
        logger.info("Email request modal detected. Not clicking connect...")
        # Try to dismiss the modal
        dismiss_button = await page.query_selector('button[aria-label="Dismiss"]')
        if dismiss_button:
            await dismiss_button.click()
        return True
    return False


async def detect_captcha(page) -> bool:
    """Detect if a CAPTCHA challenge is present on the page."""
    try:
        # Look for common CAPTCHA indicators
        captcha_selectors = [
            "iframe[src*='recaptcha']",
            "iframe[title*='reCAPTCHA']",
            ".g-recaptcha",
            "#captcha",
            "[data-test-id='captcha']",
            ".captcha-container",
        ]

        for selector in captcha_selectors:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                logger.warning(f"CAPTCHA detected: {selector}")
                return True

        # Also check for CAPTCHA-related text in the page
        captcha_texts = [
            "please verify you're not a robot",
            "verify you are human",
            "complete the captcha",
            "security verification",
            "prove you're not a robot",
        ]

        page_content = (await page.content()).lower()
        for text in captcha_texts:
            if text in page_content:
                logger.warning(f"CAPTCHA text detected: {text}")
                return True

        return False

    except Exception as e:
        logger.warning(f"Error detecting CAPTCHA: {e}")
        return False


async def detect_invitation_limit(page) -> bool:
    """Detect if LinkedIn invitation limit has been reached."""
    try:
        # Look for common invitation limit modal indicators
        limit_modals = [
            "#ip-fuse-limit-alert",
            "[data-test-modal-id='ip-fuse-limit-alert']",
            ".artdeco-modal[role='dialog']",
        ]

        for selector in limit_modals:
            modal = await page.query_selector(selector)
            if modal and await modal.is_visible():
                if await _is_true_limit(modal):
                    logger.warning("LinkedIn invitation limit detected!")
                    return True

        # Also check for limit text in the page
        limit_texts = [
            "weekly invitation limit",
            "límite semanal de invitaciones",
            "invitation limit reached",
        ]

        page_content = (await page.content()).lower()
        for text in limit_texts:
            if text in page_content:
                logger.warning(f"Invitation limit text detected: {text}")
                return True

        return False

    except Exception as e:
        logger.warning(f"Error detecting invitation limit: {e}")
        return False


async def send_connection_request(
    page,
    candidate_name: str,
    message_template: Optional[str] = None,
    progress_callback: Optional[callable] = None,
) -> Dict[str, Any]:
    """
    Send connection request with enhanced error handling and limit detection.

    Returns:
        Dict with keys: success (bool), status (str), message (str)
    """
    try:
        if progress_callback:
            progress_callback(f"Attempting to connect with {candidate_name}")

        # Wait for page to load
        await random_wait(page, min_ms=2000, max_ms=4000)

        # Check for CAPTCHA challenge
        if await detect_captcha(page):
            raise CaptchaDetectedException("CAPTCHA challenge detected - manual verification required")

        # Check if email is required for connection
        if await check_connection_email_required(page):
            return {
                "success": False,
                "status": "email_required",
                "message": "Email required for connection"
            }

        # Look for connect button
        connect_selectors = [
            "button:has-text('Connect')",
            "button[aria-label*='Invite'][aria-label*='connect']",
            "button[data-test-app-aware-link]",
        ]

        connect_button = None
        for selector in connect_selectors:
            connect_button = await page.query_selector(selector)
            if connect_button and await connect_button.is_visible():
                break

        if not connect_button:
            return {
                "success": False,
                "status": "no_connect_button",
                "message": "No connect button found"
            }

        # Click connect button
        await connect_button.click()
        await random_wait(page, min_ms=1000, max_ms=2000)

        # Check for CAPTCHA after clicking
        if await detect_captcha(page):
            raise CaptchaDetectedException("CAPTCHA challenge detected after clicking connect")

        # Check for invitation limit after clicking
        if await detect_invitation_limit(page):
            raise RateLimitExceededException(
                "LinkedIn weekly invitation limit reached",
                limit_type="weekly"
            )

        # Handle connection modal
        send_button = None

        # Look for send buttons
        send_selectors = [
            "button:has-text('Send without a note')",
            "button:has-text('Send')",
            "button[aria-label='Send now']",
        ]

        for selector in send_selectors:
            send_button = await page.query_selector(selector)
            if send_button and await send_button.is_visible():
                break

        if send_button:
            # If we have a message template and there's an option to add a note
            if message_template and message_template.strip():
                note_button = await page.query_selector("button:has-text('Add a note')")
                if note_button and await note_button.is_visible():
                    await note_button.click()
                    await random_wait(page, min_ms=500, max_ms=1000)

                    # Fill in the personalized message
                    message = message_template.format(name=candidate_name)
                    textarea = await page.query_selector("textarea")
                    if textarea:
                        await textarea.fill(message)
                        await random_wait(page, min_ms=500, max_ms=1000)

            # Send the connection request
            await send_button.click()
            await random_wait(page, min_ms=1000, max_ms=2000)

            # Check again for invitation limit after sending
            if await detect_invitation_limit(page):
                raise RateLimitExceededException(
                    "LinkedIn weekly invitation limit reached",
                    limit_type="weekly"
                )

            if progress_callback:
                progress_callback(f"✅ Connection request sent to {candidate_name}")

            return {
                "success": True,
                "status": "sent",
                "message": f"Connection request sent to {candidate_name}"
            }
        else:
            return {
                "success": False,
                "status": "no_send_button",
                "message": "No send button found in modal"
            }

    except (RateLimitExceededException, CaptchaDetectedException):
        raise  # Re-raise limit / CAPTCHA exceptions for callers to handle
    except Exception as e:
        logger.error(f"Error sending connection request to {candidate_name}: {e}")

        # Take screenshot on error
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"Error_Screenshots/connection_error_{timestamp}.png"
            await page.screenshot(path=screenshot_path)
            logger.info(f"Screenshot saved: {screenshot_path}")
        except Exception as screenshot_error:
            logger.warning(f"Failed to save screenshot: {screenshot_error}")

        return {
            "success": False,
            "status": "error",
            "message": f"Error: {str(e)}"
        }


async def check_if_connected(page) -> bool:
    """Check if already connected to the profile."""
    try:
        # Look for connection indicators
        connected_indicators = [
            "span:has-text('Connected')",
            "button:has-text('Message')",
            "span:has-text('1st')",  # 1st degree connection
            "[data-test-icon='message-icon']",
        ]

        for selector in connected_indicators:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return True

        return False

    except Exception as e:
        logger.warning(f"Error checking connection status: {e}")
        return False


async def get_connection_status(page) -> str:
    """Get the current connection status of a profile."""
    try:
        if await check_if_connected(page):
            return "connected"

        # Check for pending invitation
        pending_indicators = [
            "span:has-text('Pending')",
            "button:has-text('Pending')",
            "span:has-text('Invitation sent')",
        ]

        for selector in pending_indicators:
            element = await page.query_selector(selector)
            if element and await element.is_visible():
                return "pending"

        # Check if connect button is available
        connect_button = await page.query_selector("button:has-text('Connect')")
        if connect_button and await connect_button.is_visible():
            return "not_connected"

        return "unknown"

    except Exception as e:
        logger.warning(f"Error getting connection status: {e}")
        return "error"
