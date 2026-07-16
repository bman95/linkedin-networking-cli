"""
Connection status monitoring and smart checking for LinkedIn automation.
"""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from exceptions import (
    CaptchaDetectedException,
    NotAuthenticatedException,
    UnexpectedLandingException,
)
from utils.logging import get_logger

from .interactions import detect_captcha
from .navigation import navigate_guarded
from .scraping import get_contact_info

logger = get_logger(__name__)


async def smart_connection_checker(
    automation,  # LinkedInAutomation instance
    campaign_id: int,
    progress_callback: Callable | None = None,
    stop_event: Any | None = None,
) -> dict[str, int]:
    """
    Smart checker that monitors LinkedIn connections page to find newly accepted connections.

    This is the main function that should be called from your CLI.

    ``stop_event`` (a ``threading.Event``-like flag, issue #43) is polled
    between profiles; once set, the walk ends at the next safe point and the
    partial stats carry ``"stopped": True``.

    A challenge/login wall on the connections page — a URL-level bounce
    (guarded navigation) or an in-page CAPTCHA widget — raises the matching
    typed exception (``CaptchaDetectedException`` / ``NotAuthenticatedException``
    / ``UnexpectedLandingException``) instead of returning a clean empty
    result: a walled page read as "no connections found" would both misreport
    to the user and let the scroll loop hammer keypresses against a checkpoint.

    An unexpected (non-challenge) failure — e.g. a Playwright error mid-scroll
    or a database error — is *not* swallowed into a clean-looking zero-stat
    result either: it propagates to the caller, mirroring
    ``search_and_connect`` in ``linkedin.py`` (issue #59).

    Returns:
        Dict with counts: {"checked": int, "newly_accepted": int, "updated": int},
        plus ``stopped: True`` after a stop request and ``truncated: True`` when
        the walk gave up inconclusively — the scroll-rounds backstop tripped,
        or (with a stop marker expected from a prior check) the walk ended on
        a single-observation heuristic without reaching the marker or
        confirming the true end of the list.
    """
    if not automation.is_authenticated:
        raise Exception("Not authenticated. Please login first.")

    if progress_callback:
        progress_callback("Starting smart connection checker...")

    # Get pending contacts for this campaign. "possibly_sent" (issue #31) is an
    # assumed-sent invite awaiting acceptance, so sweep it alongside "sent".
    pending_contacts = (
        automation.db_manager.get_contacts_by_status(campaign_id, "sent")
        + automation.db_manager.get_contacts_by_status(campaign_id, "possibly_sent")
    )

    if not pending_contacts:
        if progress_callback:
            progress_callback("No pending connections found for this campaign.")
        return {"checked": 0, "newly_accepted": 0, "updated": 0}

    if progress_callback:
        progress_callback(f"Found {len(pending_contacts)} pending connections to check")

    # Navigate to LinkedIn connections page
    connections_url = "https://www.linkedin.com/mynetwork/invite-connect/connections/"

    try:
        # Guarded navigation (same as the search flows): resilient goto ->
        # settle -> surf -> landing guard. A challenge/login bounce raises a
        # typed exception with evidence instead of silently landing on a wall.
        automation.page = await navigate_guarded(
            automation.page,
            connections_url,
            strict_path="/mynetwork/invite-connect/connections",
            context={"campaign_id": campaign_id},
            **automation._nav_kwargs(),
        )
        if progress_callback:
            progress_callback(f"Opened connections page: {connections_url}")

        # Wait for page to load
        await automation.page.wait_for_timeout(5000)

        # An in-page CAPTCHA can render without a URL bounce (the landing
        # guard above only catches URL-level challenges) — without this check
        # the page reads as "no connections found" and the scroll loop below
        # would hammer keypresses against a checkpoint.
        if await detect_captcha(automation.page):
            raise CaptchaDetectedException(
                "CAPTCHA detected on the connections page"
            )

        # Focus on "Recently added" section
        recent_added = await automation.page.query_selector('[data-view-name="connections-profile"]')
        if recent_added:
            await recent_added.focus()
            logger.info("Focused on 'Recently added' connections")
            await automation.page.wait_for_timeout(random.randint(3000, 5000))

        # Get the most recent connection from database as a limit
        limit_contact = _get_connection_limit(automation.db_manager, campaign_id)

        if limit_contact:
            limit_name = limit_contact.name
            # Normalize through the same cleaner applied to page-side hrefs:
            # DB-stored URLs (from search-time extraction) carry no guaranteed
            # trailing slash, while the walk's URLs always get one — an
            # unnormalized marker could silently never match (issue #59).
            # ``or None``: an empty stored URL is "no marker", not a marker
            # that can never be reached (which would flag every inconclusive
            # exit as truncated).
            limit_url = _clean_profile_url(limit_contact.profile_url) or None
            if progress_callback:
                progress_callback(f"Checking connections until: {limit_name}")
        else:
            limit_name = None
            limit_url = None

        # Start checking connections
        stats, updated_campaign_ids = await _check_connections_page(
            automation,
            pending_contacts,
            limit_url,
            progress_callback,
            stop_event=stop_event,
        )

        # Refresh the persisted stats (e.g. total_accepted) for every campaign
        # that had a contact updated, so the Campaigns/Detail screens don't
        # show a stale count until the next unrelated write happens to touch it.
        for updated_campaign_id in updated_campaign_ids:
            automation.db_manager.update_campaign_stats(updated_campaign_id)

        # A stopped walk already announced itself; a "completed" line on top
        # would contradict the stop acknowledgement in the progress stream.
        if progress_callback and not stats.get("stopped"):
            progress_callback(
                f"Checker completed: {stats['newly_accepted']} newly accepted connections found"
            )

        return stats

    except (
        CaptchaDetectedException,
        NotAuthenticatedException,
        UnexpectedLandingException,
    ) as challenge:
        # A challenge/login wall (or a wrong landing) must not be swallowed
        # into a clean empty result — re-raise so the run stops loudly,
        # mirroring the search flows' handling in linkedin.py.
        logger.warning(
            "Connections checker hit a challenge/wrong landing; aborting: %s",
            challenge,
        )
        if progress_callback:
            progress_callback(
                "⚠️ Challenge or wrong landing detected while checking "
                "connections — stopping to protect the account"
            )
        if isinstance(challenge, (CaptchaDetectedException, NotAuthenticatedException)):
            automation._mark_session_compromised()
        raise
    # No catch-all here: an unexpected error must propagate to the caller's
    # failure handler, mirroring search_and_connect in linkedin.py. Swallowing
    # it into a zero-stat return would let the TUI's run panel stamp a
    # crashed check as a clean "success" (issue #59) — any acceptances the
    # walk had already reconciled before the failure still stand in the DB;
    # only the misleading "all zero, all fine" summary is the problem.


async def _check_connections_page(
    automation,
    pending_contacts: list,
    limit_url: str | None,
    progress_callback: Callable | None = None,
    stop_event: Any | None = None,
) -> tuple[dict[str, int], set[int]]:
    """Check the connections page and update database for newly accepted connections.

    Returns ``(stats, updated_campaign_ids)``: the campaign ids are those
    whose contact was actually updated during the walk, so the caller can
    refresh each affected campaign's persisted stats.
    """

    stats = {"checked": 0, "newly_accepted": 0, "updated": 0}
    updated_campaign_ids: set[int] = set()
    finish_process = False

    def _stop_requested() -> bool:
        # Cooperative cancellation (issue #43): polled between profiles, at
        # round boundaries and between scroll steps — never inside a
        # per-profile DB update, so contact writes never tear mid-way. The
        # acknowledgement is emitted once, however often the poll runs.
        if stop_event is not None and stop_event.is_set():
            if not stats.get("stopped"):
                stats["stopped"] = True
                if progress_callback:
                    progress_callback(
                        "Stop requested — ending the check at a safe point"
                    )
            return True
        return False

    # Create a lookup dict for faster searching. Keys normalized like the
    # page-side URLs (trailing slash, no query): DB-stored URLs carry no
    # guaranteed trailing slash, and an unnormalized key would silently never
    # match — missing the acceptance sweep's entire payload (issue #59).
    pending_lookup = {
        _clean_profile_url(contact.profile_url): contact
        for contact in pending_contacts
    }

    # Every profile URL processed so far. A scroll round that surfaces no NEW
    # profile for several consecutive rounds is the true end of the list
    # (whether the DOM accumulates cards or recycles them), giving the walk a
    # natural terminator alongside the limit_url marker — and sparing
    # already-updated contacts a duplicate pass.
    seen_urls: set[str] = set()

    # Whether the walk actually confirmed reaching the limit_url marker (as
    # opposed to concluding "end of list" via a heuristic below, or hitting
    # the scroll-rounds backstop). Only meaningful when limit_url is set.
    reached_limit_marker = False

    # Whether the walk confirmed the true end of the list (several consecutive
    # empty rounds — a deliberate, repeated observation). The single-round
    # heuristics (short page, empty page) and the scroll backstop do NOT set
    # this: they are inconclusive exits.
    confirmed_end_of_list = False

    # Backstop only: the natural terminators are the limit_url marker, the
    # no-new-profiles detection above, and the short-page heuristic below. The
    # cap exists so a pathological page that keeps feeding cards can't scroll
    # forever; it is generous enough (hundreds of cards) that a real
    # reconciliation reaches its marker or the list end first. A cap hit is
    # flagged in the returned stats (``truncated``) so callers can tell it
    # apart from a complete check.
    scroll_rounds = 0
    max_scroll_rounds = 40

    # A single scroll round surfacing zero new profiles is not reliable proof
    # of "end of list": the wait strategy here is fixed timeouts (ArrowDown
    # presses + flat pauses — no network-idle or wait-for-new-card
    # condition), so on a slow connection one round's query can run before
    # LinkedIn paints the next batch. Require several consecutive empty
    # rounds — each its own multi-second scroll phase — before concluding the
    # list is actually exhausted (issue #59).
    empty_rounds_end_threshold = 3
    consecutive_empty_rounds = 0

    while not finish_process:
        if _stop_requested():
            break
        if scroll_rounds >= max_scroll_rounds:
            logger.warning(
                "Connections walk hit the max scroll rounds backstop (%d) "
                "without reaching a stop marker or the end of the list; "
                "results may be incomplete",
                max_scroll_rounds,
            )
            if progress_callback:
                progress_callback(
                    "Reached maximum scroll rounds — stopping; the check may "
                    "be incomplete"
                )
            stats["truncated"] = True
            break
        scroll_rounds += 1

        # A challenge can render mid-walk without a URL bounce; check between
        # scroll rounds so the keypress-mashing loop below never hammers a
        # checkpoint that appeared after the initial navigation.
        if await detect_captcha(automation.page):
            raise CaptchaDetectedException(
                "CAPTCHA detected while checking connections"
            )

        # Scroll down to load more connections. A full scroll phase takes tens
        # of seconds, and it is pure loading — no DB writes — so the stop flag
        # is polled between keypresses and waits to keep cancellation
        # responsive here too (issue #43); abandoning it mid-scroll tears
        # nothing.
        for _ in range(random.randint(4, 6)):
            if _stop_requested():
                break
            if progress_callback:
                progress_callback("Scrolling to load more connections...")

            for _ in range(random.randint(18, 24)):
                if _stop_requested():
                    break
                await automation.page.keyboard.press("ArrowDown")
                await automation.page.wait_for_timeout(random.randint(20, 40))

            if _stop_requested():
                break
            await automation.page.wait_for_timeout(random.randint(2000, 4000))
        if _stop_requested():
            break

        # Get connection elements
        connections = await automation.page.query_selector_all('[data-view-name="connections-list"]')

        if not connections:
            if progress_callback:
                progress_callback("No connections found on page, stopping...")
            break

        new_this_round = 0
        for connection in connections:
            if _stop_requested():
                finish_process = True
                break
            try:
                # Get profile element
                profile = await connection.query_selector('[data-view-name="connections-profile"]')
                if not profile:
                    continue

                profile_url = await profile.get_attribute("href")
                if not profile_url:
                    continue

                # Clean the URL to match database format
                profile_url = _clean_profile_url(profile_url)

                # Already handled in an earlier round (the DOM keeps loaded
                # cards around) — skip the duplicate pass.
                if profile_url in seen_urls:
                    continue
                seen_urls.add(profile_url)
                new_this_round += 1

                # Check if this is a pending contact we're tracking
                if profile_url in pending_lookup:
                    contact = pending_lookup[profile_url]
                    stats["checked"] += 1

                    if progress_callback:
                        progress_callback(f"Checking: {contact.name}")

                    # This person was pending and is now in connections - they accepted!
                    await _update_accepted_connection(automation, contact, progress_callback)
                    stats["newly_accepted"] += 1
                    stats["updated"] += 1
                    updated_campaign_ids.add(contact.campaign_id)

                # Check if we've reached our limit
                if limit_url and profile_url == limit_url:
                    if progress_callback:
                        progress_callback("Reached connection limit, stopping checker")
                    finish_process = True
                    reached_limit_marker = True
                    break

            except (CaptchaDetectedException, NotAuthenticatedException):
                # A challenge/login wall hit while enriching an accepted
                # contact (see _update_accepted_connection) must stop the
                # walk, not be swallowed like an ordinary per-card parsing
                # hiccup — propagate to smart_connection_checker's own
                # challenge handling below (issue #58).
                raise
            except Exception as e:
                logger.warning(f"Error processing connection element: {e}")
                continue

        if not finish_process and new_this_round == 0:
            consecutive_empty_rounds += 1
            if consecutive_empty_rounds >= empty_rounds_end_threshold:
                # Several consecutive rounds surfaced nothing new: treat this
                # as the true end of the list rather than one stalled
                # lazy-load.
                confirmed_end_of_list = True
                if progress_callback:
                    progress_callback("Reached end of connections list")
                break
            if progress_callback:
                progress_callback(
                    "No new connections this round — giving the page a "
                    "moment to catch up..."
                )
        else:
            consecutive_empty_rounds = 0

        if not finish_process and len(connections) < 10 and limit_url is None:
            # If we got fewer than 10 connections, we might be at the end.
            # Only taken when no stop marker is expected: a single short
            # observation can't distinguish a genuinely short list from a
            # stalled render, so with a marker pending the walk keeps going
            # until a conclusive exit (the marker itself, a confirmed end of
            # list, or the backstop) instead of guessing — a short list is
            # exhausted within a few empty rounds anyway, and a healthy small
            # account must never be flagged "incomplete" forever just because
            # its marker legitimately vanished (issue #59 review).
            if progress_callback:
                progress_callback("Reached end of connections list")
            break

    if (
        limit_url is not None
        and not reached_limit_marker
        and not confirmed_end_of_list
        and not stats.get("stopped")
    ):
        # There was a specific stop marker to reconcile against (the
        # campaign's most-recently-accepted connection) and the walk ended on
        # an INCONCLUSIVE exit — the empty-page break or the scroll-rounds
        # backstop — without ever confirming the marker. Flag the result so
        # callers don't present a silently over-confident "success" (issue
        # #59). A walk that confirmed the true end of the list is NOT flagged
        # even when the marker was missing: the marker can legitimately
        # vanish (the connection was removed, the list reordered), and
        # flagging it would make "incomplete" the permanent steady state for
        # a perfectly healthy campaign.
        stats["truncated"] = True

    return stats, updated_campaign_ids


async def _update_accepted_connection(
    automation,
    contact,
    progress_callback: Callable | None = None,
) -> None:
    """Update contact in database as accepted and collect additional info.

    Opens a NEW tab for the contact's profile and drives it through the same
    guarded navigation the search flows use (``navigate_guarded`` on
    ``new_page`` — never ``automation.page``, which stays on the connections
    list throughout), so a challenge/login bounce is detected against the tab
    actually navigated. Without this, a checkpoint on this tab raised no
    typed exception and never marked the session compromised, so
    ``close_browser`` could persist a still-good ``session.json`` overwritten
    with cookies from a session that had, in fact, just been challenged
    (issue #58). A detected ``CaptchaDetectedException`` /
    ``NotAuthenticatedException`` propagates instead of being swallowed; any
    other failure (a slow/odd profile page, a scrape hiccup) remains a soft
    failure that is logged, not raised. ``recover`` is deliberately omitted
    (unlike the main navigation paths): a crash on this side tab must not
    trigger a full context refresh of the still-in-progress connections walk.
    """

    # The tab is closed in the finally so a failure in goto/get_contact_info
    # can't leak it.
    new_page = await automation.context.new_page()
    try:
        try:
            new_page = await navigate_guarded(
                new_page,
                contact.profile_url,
                check_path=False,
                context={"profile_url": contact.profile_url},
                **{**automation._nav_kwargs(), "recover": None},
            )
            await new_page.wait_for_timeout(random.randint(5000, 8000))

            # An in-page CAPTCHA can render without a URL bounce (the guard
            # above only catches URL-level challenges) — mirrors the same
            # check the connections-page walk does for exactly this reason.
            # Without it, a checkpoint widget on this tab would leave
            # get_contact_info to just return an empty dict, no exception at
            # all, and the session would stay marked authenticated (issue #58).
            if await detect_captcha(new_page):
                raise CaptchaDetectedException(
                    f"CAPTCHA detected while enriching accepted connection "
                    f"{contact.name!r}"
                )

            # Get updated contact info
            contact_info = await get_contact_info(new_page)

            # Update the contact in database
            update_data = {
                "status": "accepted",
                "connection_accepted_at": datetime.now(UTC),
            }

            # Add contact info if available
            if contact_info.get("email"):
                update_data["email"] = contact_info["email"]
            if contact_info.get("phone"):
                update_data["phone"] = contact_info["phone"]
            if contact_info.get("address"):
                update_data["notes"] = f"Address: {contact_info['address']}"

            # Update in database
            automation.db_manager.update_contact(contact.id, update_data)
        finally:
            await new_page.close()

        if progress_callback:
            progress_callback(f"✅ Updated {contact.name} as accepted connection")

        logger.info(f"Updated contact {contact.name} as accepted connection")

    except (CaptchaDetectedException, NotAuthenticatedException):
        raise
    except Exception as e:
        logger.error(f"Error updating accepted connection {contact.name}: {e}")
        if progress_callback:
            progress_callback(f"❌ Failed to update {contact.name}: {str(e)}")


def _get_connection_limit(db_manager, campaign_id: int):
    """Get the most recently accepted connection to use as a stopping point."""
    try:
        # Get the most recent accepted connection for this campaign
        with db_manager.get_session() as session:
            from sqlmodel import select

            from database.models import Contact

            recent_accepted = session.exec(
                select(Contact)
                .where(Contact.campaign_id == campaign_id)
                .where(Contact.status == "accepted")
                .order_by(Contact.connection_accepted_at.desc())
            ).first()

            return recent_accepted
    except Exception as e:
        logger.warning(f"Error getting connection limit: {e}")
        return None


def _clean_profile_url(url: str) -> str:
    """Clean and normalize LinkedIn profile URL for comparison."""
    if not url:
        return ""

    # Remove query parameters and fragments
    if "?" in url:
        url = url.split("?")[0]
    if "#" in url:
        url = url.split("#")[0]

    # Ensure it ends with /
    if not url.endswith("/"):
        url += "/"

    # Convert to standard format
    if url.startswith("/in/"):
        url = "https://www.linkedin.com" + url

    return url