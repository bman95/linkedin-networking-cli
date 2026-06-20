"""
Page interactions and connection management with human-like behavior.

All functions operate on an async Playwright ``Page`` and must be awaited.
"""

import asyncio
import math
import random
import sys
import time
from collections import deque
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
    """Smooth scrolling down to the end of the page with natural behavior.

    Bounded by two guards so it terminates on LinkedIn's infinite/lazy-loading
    lists (where ``document.body.scrollHeight`` grows as you approach the
    bottom and a naive "scroll to the end" loop would never finish):

    - ``MAX_STEPS``: a hard cap on iterations (a human skims, they don't scroll
      an endless feed to its true end).
    - ``MAX_STALLED_STEPS``: stop early once the real scroll position stops
      advancing (already at the bottom, or the page clamped the scroll).
    """
    MEAN_STEP = 120
    STEP_JITTER = 40
    MIN_PAUSE = 200
    MAX_PAUSE = 900
    LONG_PAUSE_CHANCE = 0.10
    LONG_PAUSE_MS = (1_500, 3_000)
    MAX_STEPS = 40
    MAX_STALLED_STEPS = 3

    logger.info("Scrolling down")

    current = await page.evaluate("window.scrollY")
    viewport = await page.evaluate("window.innerHeight")
    total = await page.evaluate("document.body.scrollHeight")

    stalled = 0
    for _ in range(MAX_STEPS):
        if current + viewport >= total:
            break

        # Calculate easing progress
        progress = current / total if total else 0
        easing = 0.5 * (1 - math.cos(math.pi * progress))
        base = MEAN_STEP * (1.5 - easing)

        # Add jitter to step size
        step = max(40, int(random.gauss(base, STEP_JITTER / 3)))
        delta = step

        # Scroll with mouse wheel
        await page.mouse.wheel(0, delta + random.randint(-10, 10))

        # Random pause between scrolls
        pause = random.randint(MIN_PAUSE, MAX_PAUSE)
        if random.random() < LONG_PAUSE_CHANCE:
            pause = random.randint(*LONG_PAUSE_MS)

        await page.wait_for_timeout(pause)

        # Update values from the page (not the intended delta): the browser
        # clamps scrollY at the bottom, which is how we detect a stall.
        previous = current
        viewport = await page.evaluate("window.innerHeight")
        total = await page.evaluate("document.body.scrollHeight")
        current = await page.evaluate("window.scrollY")

        # If the real position didn't advance, the page won't scroll further
        # (or is clamped); bail after a few stalled iterations.
        if current <= previous:
            stalled += 1
            if stalled >= MAX_STALLED_STEPS:
                break
        else:
            stalled = 0


async def human_type(
    box, text: str, *, delay_min: int = 50, delay_max: int = 150
) -> None:
    """Type ``text`` into ``box`` character-by-character like a human.

    Focuses the field with a short pause first, then types each key with a
    randomized per-key delay (in ms). ``box`` is a Playwright Locator;
    ``press_sequentially`` drives one keystroke at a time, unlike ``fill``
    which sets the value instantly and reads as scripted.

    The field is cleared first so any pre-existing value (browser autofill, a
    remembered credential in the persistent profile, or a prior failed
    attempt) is overwritten rather than appended to — matching ``fill``'s
    overwrite semantics that this replaces.
    """
    await box.click()
    await box.clear()
    # Brief focus pause before the first keystroke.
    await asyncio.sleep(random.uniform(0.15, 0.4))
    for char in text:
        await box.press_sequentially(
            char, delay=random.randint(delay_min, delay_max)
        )


async def _human_mouse_move(page, x: float, y: float) -> None:
    """Move the cursor toward (x, y) in a few jittered steps.

    A hand-rolled, slightly noisy path: ``random(5, 10)`` segments toward the
    target with ±5px jitter per step and a 0.01–0.03s pause between moves, so
    the trajectory doesn't teleport straight to the element the way a bare
    ``element.click()`` does.
    """
    steps = random.randint(5, 10)
    for i in range(1, steps + 1):
        progress = i / steps
        jitter_x = random.uniform(-5, 5) if i < steps else 0
        jitter_y = random.uniform(-5, 5) if i < steps else 0
        await page.mouse.move(x * progress + jitter_x, y * progress + jitter_y)
        await asyncio.sleep(random.uniform(0.01, 0.03))


async def move_to_element(page, element) -> None:
    """Move the mouse toward ``element``'s center along a human path.

    Best-effort: if the bounding box can't be read or the mouse move fails
    (e.g. a mocked page), this is a no-op so the caller's click still proceeds.
    """
    try:
        box = await element.bounding_box()
        if box:
            target_x = box["x"] + box["width"] / 2
            target_y = box["y"] + box["height"] / 2
            await _human_mouse_move(page, target_x, target_y)
    except Exception as move_error:
        logger.debug("Human mouse move skipped: %s", move_error)


async def move_to_and_click(page, element, *, click_timeout: int = 5_000) -> None:
    """Move the mouse to ``element`` along a human path, then click it.

    A JS click is the last resort when a real click is intercepted, matching
    the existing connect-path behavior.
    """
    await move_to_element(page, element)
    try:
        await element.click(timeout=click_timeout)
    except Exception as click_error:
        logger.info("Normal click intercepted (%s); using JS click", click_error)
        await element.evaluate("el => el.click()")


# Probabilistic reading/dwell distribution: most pauses are a normal scan, with
# occasional quick glances and slower careful reads. Each profile's (low, high)
# are fractions of the configured (min_s, max_s) window — 0.0 == min_s,
# 1.0 == max_s — so the three profiles together span the whole window.
_DWELL_PROFILES = (
    (0.25, (0.0, 0.35)),   # quick scan
    (0.55, (0.30, 0.70)),  # normal read
    (0.20, (0.65, 1.0)),   # careful read
)


async def dwell(page, *, min_s: float = 1.0, max_s: float = 4.0) -> None:
    """Pause between major actions as if reading the page.

    Picks a dwell profile (quick-scan / normal / careful) by weight, then a
    duration inside the configured ``(min_s, max_s)`` window scaled by that
    profile, simulating variable human attention.
    """
    roll = random.random()
    cumulative = 0.0
    low_frac, high_frac = _DWELL_PROFILES[1][1]
    for weight, (lo, hi) in _DWELL_PROFILES:
        cumulative += weight
        if roll <= cumulative:
            low_frac, high_frac = lo, hi
            break

    span = max_s - min_s
    seconds = min_s + span * random.uniform(low_frac, high_frac)
    seconds = max(min_s, min(seconds, max_s))
    logger.debug("Dwelling %.2fs between actions", seconds)
    await page.wait_for_timeout(int(seconds * 1_000))


class RateLimiter:
    """Sliding 60s-window cap on actions to avoid bursty, bot-like traffic.

    ``acquire`` records each action's timestamp and, once the rolling
    60-second window holds ``max_per_minute`` actions, sleeps until the oldest
    action ages out before allowing the next one through (like the reference
    project's ``antidetect.rate_limit``).
    """

    WINDOW_SECONDS = 60.0

    def __init__(self, max_per_minute: int = 20):
        self.max_per_minute = max_per_minute
        self._timestamps: deque[float] = deque()

    async def acquire(self) -> None:
        if self.max_per_minute <= 0:
            return

        now = time.monotonic()
        self._prune(now)

        if len(self._timestamps) >= self.max_per_minute:
            wait_for = self.WINDOW_SECONDS - (now - self._timestamps[0])
            if wait_for > 0:
                logger.info(
                    "Rate limit reached (%d/min); waiting %.1fs",
                    self.max_per_minute,
                    wait_for,
                )
                await asyncio.sleep(wait_for)
                now = time.monotonic()
                self._prune(now)

        self._timestamps.append(time.monotonic())

    def _prune(self, now: float) -> None:
        cutoff = now - self.WINDOW_SECONDS
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()


async def _is_true_limit(modal) -> bool:
    """Check if the modal indicates a true invitation limit.

    The icon and header candidates come from the central
    ``LIMIT_TRUE_MARKER`` selector: its stable anchor is the locked-padlock icon
    that marks a true weekly limit; the remaining candidates are the header-text
    fallback (used when LinkedIn swaps the icon).
    """
    icon_css = sel.LIMIT_TRUE_MARKER.anchor
    header_css = sel.LIMIT_TRUE_MARKER.candidates[1:]

    # Check for LinkedIn invitation limit icon
    if await modal.query_selector(icon_css):
        return True

    # Fallback by heading text (in case they change the icon)
    header_el = (
        await modal.query_selector(", ".join(header_css)) if header_css else None
    )
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
