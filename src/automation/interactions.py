"""
Page interactions and connection management with human-like behavior.

All functions operate on an async Playwright ``Page`` and must be awaited.
"""

import math
import random
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from automation import selectors as sel
from utils.logging import get_logger

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
    """Check if the modal indicates a true invitation limit.

    The icon and header candidates come from the central
    ``LIMIT_TRUE_MARKER`` selector (candidate #0 is the locked-padlock icon
    that marks a true weekly limit; the rest are the header-text fallback).
    """
    icon_css, *header_css = sel.LIMIT_TRUE_MARKER.candidates

    # Check for LinkedIn invitation limit icon
    if await modal.query_selector(icon_css):
        return True

    # Fallback by heading text (in case they change the icon)
    header_el = await modal.query_selector(", ".join(header_css))
    header = (await header_el.inner_text()).strip().lower() if header_el else ""

    true_texts = {
        "has alcanzado el límite semanal de invitaciones",
        "has alcanzado el límite semanal de invitaciones.",
        "you've reached the weekly invitation limit",
        "you've reached the weekly invitation limit.",
    }
    return any(t in header for t in true_texts)


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
