"""Central selector registry with ordered fallback candidates.

LinkedIn ships a Server-Driven UI (SDUI) whose obfuscated CSS classes and
component keys churn between releases. A single inline selector that stops
matching is silently read as "no results", so the automation misreads a layout
change as an empty page instead of failing loudly. This module makes every
load-bearing element an **ordered list of fallback candidates** so the run can:

- prefer a *stable anchor* (``data-test*``, ``data-testid``, ``componentkey``)
  over hashed CSS classes (candidate #0 is the anchor),
- keep going when the primary candidate drifts, while logging a WARNING so the
  drift is visible and the registry can be updated,
- **fail loud** when a *required* selector matches nothing: it raises
  ``SelectorNotFoundException`` and captures a diagnostics evidence bundle
  (screenshot + DOM + structured log line) recording the selector name, the
  full candidate list tried, and the URL.

Modeled on the LinkedIn Worker project's ``agent/src/browser/selectors.py``
``Selector`` class.

All ``Selector`` methods that touch a page are async and operate on an async
Playwright ``Page``.
"""

import sys
from pathlib import Path
from typing import List, Optional, Sequence

sys.path.append(str(Path(__file__).parent.parent))

from automation.diagnostics import capture_error_context
from utils.logging import get_logger
from exceptions import SelectorNotFoundException

logger = get_logger(__name__)


class Selector:
    """An ordered list of fallback CSS candidates for one load-bearing element.

    Candidate #0 should be a stable anchor (a ``data-test*`` / ``data-testid`` /
    ``componentkey`` attribute) so it survives LinkedIn's SDUI class-name churn;
    later candidates are ES/EN text or legacy-class fallbacks.

    Candidates may use Playwright's selector syntax (``:has-text(...)``,
    ``:text-is(...)``) in addition to plain CSS, so they must only be consumed
    by Playwright APIs (``query_selector`` / ``locator`` / ``wait_for_selector``)
    — never handed to a non-Playwright CSS engine.

    **Primary peers vs. drift fallbacks.** Not every later candidate signals DOM
    drift. Locale variants ("Next" / "Siguiente") and equally-stable anchors are
    *co-equal primaries*: matching any of them is the normal happy path, not a
    "go update the selector" event. ``primary_count`` marks how many leading
    candidates are such peers; :meth:`locate` only warns when it has to reach
    *past* that primary group into a true fallback.

    The class never *waits*: ``.locate`` and ``.count`` reflect the page's
    current state. Callers that need to wait for an element to appear pass
    :attr:`css` to Playwright's ``wait_for_selector`` (a presence-only race
    across all candidates at once).
    """

    def __init__(
        self, name: str, candidates: Sequence[str], *, primary_count: int = 1
    ):
        """Build a selector.

        Args:
            name: Stable identifier used in evidence bundles and warnings.
            candidates: Ordered CSS candidates, most-stable first. Must be
                non-empty; each entry must be a single (non-comma) CSS selector
                so a fallback to candidate #N can be detected per-candidate.
            primary_count: How many leading candidates are co-equal primaries
                (e.g. EN/ES locale variants). Matching any of these is the
                happy path; :meth:`locate` only warns when it falls back *past*
                them. Defaults to 1 (only candidate #0 is primary).
        """
        cleaned = [c.strip() for c in candidates if c and c.strip()]
        if not cleaned:
            raise ValueError(f"Selector {name!r} needs at least one candidate")
        if primary_count < 1:
            raise ValueError(
                f"Selector {name!r}: primary_count must be >= 1, got {primary_count}"
            )
        self.name = name
        self.candidates: List[str] = cleaned
        # Cap at the candidate count so an over-specified primary_count cannot
        # silence a genuine fallback that simply doesn't exist yet.
        self.primary_count = min(primary_count, len(cleaned))

    @property
    def anchor(self) -> str:
        """The most-stable candidate (#0).

        Callers that need the bare anchor for a presence-only enumeration (e.g.
        ``query_selector_all`` over the legacy structured cards) use this so the
        "stable anchor" stays a named concept rather than a positional index
        reached for at the call site.
        """
        return self.candidates[0]

    @property
    def css(self) -> str:
        """Comma-joined candidate list for presence-only ``wait_for_selector``.

        This races all candidates at once, so it does not report *which*
        candidate matched (use :meth:`locate` for fallback detection). It is the
        right tool for "is any variant of this element present yet?" waits.
        """
        return ", ".join(self.candidates)

    async def count(self, page) -> int:
        """Return how many elements match across all candidates right now.

        Presence-only and non-raising: a zero count is a valid answer, not a
        failure. Use this for "is the modal up yet?" style polling.
        """
        return await page.locator(self.css).count()

    async def locate(self, page, *, required: bool = False, context=None):
        """Return the first matching element handle, trying candidates in order.

        Walks the candidates most-stable-first and returns the first
        ``query_selector`` hit. Falling back *past the primary group* (to a
        candidate at or beyond ``primary_count``) logs a WARNING so DOM drift is
        visible while the run continues; matching a co-equal primary (e.g. the
        ES locale variant of an EN/ES pair) is the happy path and stays quiet.

        Args:
            page: An async Playwright ``Page``.
            required: When True, a no-match outcome is fatal — it captures a
                diagnostics evidence bundle and raises
                ``SelectorNotFoundException``. When False (default), returns
                ``None`` on no match.
            context: Optional dict merged into the evidence bundle (e.g.
                ``{"campaign": name}``). Only used on the required-missing path.

        Returns:
            The first matching element handle, or ``None`` when nothing matched
            and ``required`` is False.

        Raises:
            SelectorNotFoundException: when ``required`` is True and no
                candidate matched.
        """
        for index, candidate in enumerate(self.candidates):
            handle = await page.query_selector(candidate)
            if handle is not None:
                if index >= self.primary_count:
                    primaries = ", ".join(self.candidates[: self.primary_count])
                    logger.warning(
                        "Selector %r matched fallback candidate #%d (%s); no "
                        "primary candidate (%s) matched — update the primary.",
                        self.name,
                        index,
                        candidate,
                        primaries,
                    )
                return handle

        if required:
            await self.fail_loud(page, context=context)
        return None

    async def fail_loud(self, page, *, context=None, timeout: Optional[int] = None):
        """Capture an evidence bundle and raise. Never returns normally.

        Callers that detect a missing element on a path other than
        :meth:`locate` (e.g. a ``wait_for_selector`` timeout) call this to get
        the same fail-loud behavior: a best-effort evidence bundle followed by a
        ``SelectorNotFoundException``. The capture cannot raise and never masks
        the exception, so a layout change always leaves a screenshot + DOM
        snapshot plus a structured log line naming the selector, the full
        candidate list, and the URL.

        Args:
            page: An async Playwright ``Page``.
            context: Optional dict merged into the evidence bundle.
            timeout: Optional wait timeout (ms) to record on the exception when
                the caller failed loud after a timed wait.
        """
        not_found = SelectorNotFoundException(
            f"Required selector {self.name!r} matched none of its candidates - "
            "LinkedIn page structure may have changed",
            selector=self.css,
            timeout=timeout,
        )
        bundle_context = {"selector": self.name, "candidates": self.candidates}
        if context:
            bundle_context.update(context)
        await capture_error_context(
            page,
            f"selector_not_found_{self.name}",
            exc=not_found,
            context=bundle_context,
        )
        raise not_found


# ---------------------------------------------------------------------------
# Registry of load-bearing navigation selectors.
#
# Ordering rule: candidate #0 is the most stable anchor available
# (``data-test*`` / ``data-testid`` / ``componentkey`` / a structural
# attribute), with hashed-class and ES/EN text variants after it. The ES/EN
# text variants are kept as additional candidates so a language switch never
# breaks a primary.
# ---------------------------------------------------------------------------

# --- Login form ---
LOGIN_USERNAME = Selector("login_username", ["input#username"])
LOGIN_PASSWORD = Selector("login_password", ["input#password"])
LOGIN_SUBMIT = Selector("login_submit", ["button[type=submit]"])

# --- Search readiness / result cards ---
# Legacy UI exposes ``.search-results-container``; the SDUI rollout (2026) only
# renders profile links inside <main>.
SEARCH_RESULTS_READY = Selector(
    "search_results_ready",
    [".search-results-container", "main a[href*='/in/']"],
)
# Per-page "profiles loaded" readiness + structured legacy result cards. The
# ``data-chameleon-result-urn`` attribute is the stable anchor for legacy cards.
SEARCH_RESULT_CARDS = Selector(
    "search_result_cards",
    ["[data-chameleon-result-urn]", "main a[href*='/in/']"],
)

# --- Pagination ---
# The EN/ES aria-labels are co-equal primaries (same stable role attribute, just
# a locale difference), so matching either is the happy path — only falling
# through to the SDUI text candidates signals real drift. Hence primary_count=2.
PAGINATION_NEXT = Selector(
    "pagination_next",
    [
        "button[aria-label='Next']",
        "button[aria-label='Siguiente']",
        "main button:has-text('Siguiente')",
        "main button:has-text('Next')",
    ],
    primary_count=2,
)

# --- Connect control ---
# The Connect/Pending control is disambiguated at the call site by matching the
# profile's name inside the ``aria-label`` (the SDUI renders a name+keyword
# label, e.g. "Invita a {Name} a conectar"). This selector only narrows the
# candidate pool to elements that carry an ``aria-label``: the profile's own
# primary action is an <a>, while the "People also viewed" sidebar uses
# <button>; ``role=button`` covers SDUI custom controls.
CONNECT_CONTROL = Selector(
    "connect_control",
    ["a[aria-label]", "button[aria-label]", "[role='button'][aria-label]"],
)

# --- Invitation modal buttons ---
# The invitation modal is not a standard <dialog>, so its buttons are located by
# text. ``:text-is`` matches the exact "Enviar"/"Send" label so it never
# collides with "Enviar sin nota" / "Enviar mensaje".
INVITE_ADD_NOTE = Selector(
    "invite_add_note",
    ["button:has-text('Añadir una nota')", "button:has-text('Add a note')"],
)
INVITE_SEND_NO_NOTE = Selector(
    "invite_send_no_note",
    [
        "button:has-text('Enviar sin nota')",
        "button:has-text('Send without a note')",
    ],
)
INVITE_SEND = Selector(
    "invite_send",
    ["button:text-is('Enviar')", "button:text-is('Send')"],
)

# --- Limit modal ---
# The weekly-invitation-limit modal. The ``data-test-modal-id`` test anchor and
# the ``ip-fuse-limit-alert`` component-class selector are co-equal primaries
# (LinkedIn ships the component id consistently but the test anchor is not
# guaranteed present on every SDUI variant, so neither alone is "the" primary);
# only the ES/EN dialog-text last-resort candidates signal real drift. Hence
# primary_count=2.
LIMIT_MODAL = Selector(
    "limit_modal",
    [
        "[data-test-modal-id='ip-fuse-limit-alert']",
        "div.artdeco-modal.ip-fuse-limit-alert",
        "dialog:has-text('límite semanal')",
        "dialog:has-text('weekly invitation limit')",
    ],
    primary_count=2,
)
# The locked-padlock icon inside the limit modal marks a *true* weekly limit
# (vs. a dismissable "near limit" warning). The header-text variants are the
# fallback for when LinkedIn swaps the icon.
LIMIT_TRUE_MARKER = Selector(
    "limit_true_marker",
    [
        "svg[data-test-icon='locked']",
        "#ip-fuse-limit-alert__header",
        "h2.ip-fuse-limit-alert__header",
    ],
)
