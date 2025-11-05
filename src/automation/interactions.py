"""
Page interactions and connection management with human-like behavior.
"""

import math
import random
import logging
from datetime import datetime
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class LimitReachedException(Exception):
    """Exception raised when LinkedIn invitation limit is reached."""
    pass


def random_wait(
    page, *, min_ms: int = 3_000, max_ms: int = 7_000, verbose: bool = True
) -> None:
    """Wait a random time to simulate human latency."""
    timeout = random.randint(min_ms, max_ms)
    if verbose:
        logger.info("Waiting random time of %.2fs", timeout / 1_000)
    page.wait_for_timeout(timeout)


def scroll_down(page) -> None:
    """Smooth scrolling down to the end of the page with natural behavior."""
    MEAN_STEP = 120
    STEP_JITTER = 40
    MIN_PAUSE = 200
    MAX_PAUSE = 900
    LONG_PAUSE_CHANCE = 0.10
    LONG_PAUSE_MS = (1_500, 3_000)

    logger.info("Scrolling down")

    current = page.evaluate("window.scrollY")
    viewport = page.evaluate("window.innerHeight")
    total = page.evaluate("document.body.scrollHeight")

    while current + viewport < total:
        # Calculate easing progress
        progress = current / total
        easing = 0.5 * (1 - math.cos(math.pi * progress))
        base = MEAN_STEP * (1.5 - easing)

        # Add jitter to step size
        step = max(40, int(random.gauss(base, STEP_JITTER / 3)))
        delta = step

        # Scroll with mouse wheel
        page.mouse.wheel(0, delta + random.randint(-10, 10))
        current += delta

        # Random pause between scrolls
        pause = random.randint(MIN_PAUSE, MAX_PAUSE)
        if random.random() < LONG_PAUSE_CHANCE:
            pause = random.randint(*LONG_PAUSE_MS)

        page.wait_for_timeout(pause)

        # Update values
        viewport = page.evaluate("window.innerHeight")
        total = page.evaluate("document.body.scrollHeight")
        current = page.evaluate("window.scrollY")


def _is_true_limit(modal) -> bool:
    """Check if the modal indicates a true invitation limit."""
    # Check for LinkedIn invitation limit icon
    if modal.query_selector("svg[data-test-icon='locked']"):
        return True

    # Fallback by heading text (in case they change the icon)
    header_el = modal.query_selector(
        "#ip-fuse-limit-alert__header, h2.ip-fuse-limit-alert__header"
    )
    header = header_el.inner_text().strip().lower() if header_el else ""

    true_texts = {
        "has alcanzado el límite semanal de invitaciones",
        "has alcanzado el límite semanal de invitaciones.",
        "you've reached the weekly invitation limit",
        "you've reached the weekly invitation limit.",
    }
    return any(t in header for t in true_texts)


def check_connection_email_required(page) -> bool:
    """Check that email is not required to connect."""
    logger.info("Checking if email is required to connect")
    label = page.query_selector('label[for="email"]')
    if label:
        logger.info("Email request modal detected. Not clicking connect...")
        # Try to dismiss the modal
        dismiss_button = page.query_selector('button[aria-label="Dismiss"]')
        if dismiss_button:
            dismiss_button.click()
        return True
    return False


def detect_invitation_limit(page) -> bool:
    """Detect if LinkedIn invitation limit has been reached."""
    try:
        # Look for common invitation limit modal indicators
        limit_modals = [
            "#ip-fuse-limit-alert",
            "[data-test-modal-id='ip-fuse-limit-alert']",
            ".artdeco-modal[role='dialog']",
        ]

        for selector in limit_modals:
            modal = page.query_selector(selector)
            if modal and modal.is_visible():
                if _is_true_limit(modal):
                    logger.warning("LinkedIn invitation limit detected!")
                    return True

        # Also check for limit text in the page
        limit_texts = [
            "weekly invitation limit",
            "límite semanal de invitaciones",
            "invitation limit reached",
        ]

        page_content = page.content().lower()
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
        random_wait(page, min_ms=2000, max_ms=4000)

        # Check if email is required for connection
        if check_connection_email_required(page):
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
            connect_button = page.query_selector(selector)
            if connect_button and connect_button.is_visible():
                break

        if not connect_button:
            return {
                "success": False,
                "status": "no_connect_button",
                "message": "No connect button found"
            }

        # Click connect button
        connect_button.click()
        random_wait(page, min_ms=1000, max_ms=2000)

        # Check for invitation limit after clicking
        if detect_invitation_limit(page):
            raise LimitReachedException("LinkedIn weekly invitation limit reached")

        # Handle connection modal
        send_button = None

        # Look for send buttons
        send_selectors = [
            "button:has-text('Send without a note')",
            "button:has-text('Send')",
            "button[aria-label='Send now']",
        ]

        for selector in send_selectors:
            send_button = page.query_selector(selector)
            if send_button and send_button.is_visible():
                break

        if send_button:
            # If we have a message template and there's an option to add a note
            if message_template and message_template.strip():
                note_button = page.query_selector("button:has-text('Add a note')")
                if note_button and note_button.is_visible():
                    note_button.click()
                    random_wait(page, min_ms=500, max_ms=1000)

                    # Fill in the personalized message
                    message = message_template.format(name=candidate_name)
                    textarea = page.query_selector("textarea")
                    if textarea:
                        textarea.fill(message)
                        random_wait(page, min_ms=500, max_ms=1000)

            # Send the connection request
            send_button.click()
            random_wait(page, min_ms=1000, max_ms=2000)

            # Check again for invitation limit after sending
            if detect_invitation_limit(page):
                raise LimitReachedException("LinkedIn weekly invitation limit reached")

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

    except LimitReachedException:
        raise  # Re-raise limit exceptions
    except Exception as e:
        logger.error(f"Error sending connection request to {candidate_name}: {e}")

        # Take screenshot on error
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            screenshot_path = f"Error_Screenshots/connection_error_{timestamp}.png"
            page.screenshot(path=screenshot_path)
            logger.info(f"Screenshot saved: {screenshot_path}")
        except Exception as screenshot_error:
            logger.warning(f"Failed to save screenshot: {screenshot_error}")

        return {
            "success": False,
            "status": "error",
            "message": f"Error: {str(e)}"
        }


def check_if_connected(page) -> bool:
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
            element = page.query_selector(selector)
            if element and element.is_visible():
                return True

        return False

    except Exception as e:
        logger.warning(f"Error checking connection status: {e}")
        return False


def get_connection_status(page) -> str:
    """Get the current connection status of a profile."""
    try:
        if check_if_connected(page):
            return "connected"

        # Check for pending invitation
        pending_indicators = [
            "span:has-text('Pending')",
            "button:has-text('Pending')",
            "span:has-text('Invitation sent')",
        ]

        for selector in pending_indicators:
            element = page.query_selector(selector)
            if element and element.is_visible():
                return "pending"

        # Check if connect button is available
        connect_button = page.query_selector("button:has-text('Connect')")
        if connect_button and connect_button.is_visible():
            return "not_connected"

        return "unknown"

    except Exception as e:
        logger.warning(f"Error getting connection status: {e}")
        return "error"