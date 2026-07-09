"""
Connection status monitoring and smart checking for LinkedIn automation.
"""

import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from utils.logging import get_logger

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

    Returns:
        Dict with counts: {"checked": int, "newly_accepted": int, "updated": int},
        plus ``stopped: True`` after a stop request and ``truncated: True`` when
        the scroll-rounds backstop cut the walk short.
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
        await automation.page.goto(connections_url, timeout=60000)
        if progress_callback:
            progress_callback(f"Opened connections page: {connections_url}")

        # Wait for page to load
        await automation.page.wait_for_timeout(5000)

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
            limit_url = limit_contact.profile_url
            if progress_callback:
                progress_callback(f"Checking connections until: {limit_name}")
        else:
            limit_name = None
            limit_url = None

        # Start checking connections
        stats = await _check_connections_page(
            automation,
            pending_contacts,
            limit_url,
            progress_callback,
            stop_event=stop_event,
        )

        # A stopped walk already announced itself; a "completed" line on top
        # would contradict the stop acknowledgement in the progress stream.
        if progress_callback and not stats.get("stopped"):
            progress_callback(
                f"Checker completed: {stats['newly_accepted']} newly accepted connections found"
            )

        return stats

    except Exception as e:
        logger.error(f"Error in smart connection checker: {e}")
        if progress_callback:
            progress_callback(f"Checker failed: {str(e)}")
        return {"checked": 0, "newly_accepted": 0, "updated": 0}


async def _check_connections_page(
    automation,
    pending_contacts: list,
    limit_url: str | None,
    progress_callback: Callable | None = None,
    stop_event: Any | None = None,
) -> dict[str, int]:
    """Check the connections page and update database for newly accepted connections."""

    stats = {"checked": 0, "newly_accepted": 0, "updated": 0}
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

    # Create a lookup dict for faster searching
    pending_lookup = {contact.profile_url: contact for contact in pending_contacts}

    # Every profile URL processed so far. A scroll round that surfaces no NEW
    # profile is the true end of the list (whether the DOM accumulates cards
    # or recycles them), giving the walk a natural terminator alongside the
    # limit_url marker — and sparing already-updated contacts a duplicate pass.
    seen_urls: set[str] = set()

    # Backstop only: the natural terminators are the limit_url marker, the
    # no-new-profiles detection above, and the short-page heuristic below. The
    # cap exists so a pathological page that keeps feeding cards can't scroll
    # forever; it is generous enough (hundreds of cards) that a real
    # reconciliation reaches its marker or the list end first. A cap hit is
    # flagged in the returned stats (``truncated``) so callers can tell it
    # apart from a complete check.
    scroll_rounds = 0
    max_scroll_rounds = 40

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

                # Check if we've reached our limit
                if limit_url and profile_url == limit_url:
                    if progress_callback:
                        progress_callback("Reached connection limit, stopping checker")
                    finish_process = True
                    break

            except Exception as e:
                logger.warning(f"Error processing connection element: {e}")
                continue

        if not finish_process and new_this_round == 0:
            # A whole scroll round surfaced nothing new: the end of the list.
            if progress_callback:
                progress_callback("Reached end of connections list")
            break

        if not finish_process and len(connections) < 10:
            # If we got fewer than 10 connections, we might be at the end
            if progress_callback:
                progress_callback("Reached end of connections list")
            break

    return stats


async def _update_accepted_connection(
    automation,
    contact,
    progress_callback: Callable | None = None,
) -> None:
    """Update contact in database as accepted and collect additional info."""

    try:
        # Open the contact's profile to get additional info. The tab is closed
        # in the finally so a failure in goto/get_contact_info can't leak it.
        new_page = await automation.context.new_page()
        try:
            await new_page.goto(contact.profile_url, timeout=30000)
            await new_page.wait_for_timeout(random.randint(5000, 8000))

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


async def check_specific_contacts(
    automation,
    contact_ids: list[int],
    progress_callback: Callable | None = None,
    stop_event: Any | None = None,
) -> dict[str, int]:
    """
    Check specific contacts by visiting their profiles directly.

    This is an alternative to the smart checker for checking specific contacts.
    ``stop_event`` (issue #43) is polled between profiles; once set, the loop
    ends at the next safe point and the partial stats carry ``"stopped": True``.
    """
    if not automation.is_authenticated:
        raise Exception("Not authenticated. Please login first.")

    stats = {"checked": 0, "newly_accepted": 0, "failed": 0}

    for contact_id in contact_ids:
        # Cooperative cancellation (issue #43): between profiles only.
        if stop_event is not None and stop_event.is_set():
            stats["stopped"] = True
            if progress_callback:
                progress_callback("Stop requested — ending the check at a safe point")
            break
        try:
            # Get contact from database. "possibly_sent" (issue #31) is an
            # assumed-sent invite awaiting acceptance, so check it like "sent".
            contact = automation.db_manager.get_contact(contact_id)
            if not contact or contact.status not in ("sent", "possibly_sent"):
                continue

            if progress_callback:
                progress_callback(f"Checking {contact.name}...")

            # Navigate to profile
            await automation.page.goto(contact.profile_url, timeout=30000)
            await automation.page.wait_for_timeout(random.randint(3000, 5000))

            # Check connection status. ``is_visible`` ignores ``timeout`` in
            # the async API and returns immediately, so a slow-hydrating
            # profile would be misread as not-connected; ``wait_for_selector``
            # actually waits (timing out means not connected). The ES/EN text
            # variants are co-equal locale primaries, mirroring the registry
            # style in ``selectors.py``.
            try:
                await automation.page.wait_for_selector(
                    "span:has-text('Connected'), "
                    "span:has-text('Conectado'), "
                    ".pv-top-card__distance-badge:has-text('1st'), "
                    ".pv-top-card__distance-badge:has-text('1.º'), "
                    "button:has-text('Message'), "
                    "button:has-text('Enviar mensaje')",
                    timeout=5000,
                    state="visible",
                )
                is_connected = True
            except PlaywrightTimeoutError:
                is_connected = False

            stats["checked"] += 1

            if is_connected:
                # Update as accepted
                update_data = {
                    "status": "accepted",
                    "connection_accepted_at": datetime.now(UTC)
                }

                # Try to get contact info
                try:
                    contact_info = await get_contact_info(automation.page)
                    if contact_info.get("email"):
                        update_data["email"] = contact_info["email"]
                    if contact_info.get("phone"):
                        update_data["phone"] = contact_info["phone"]
                except Exception as contact_error:
                    logger.warning(f"Failed to get contact info for {contact.name}: {contact_error}")

                automation.db_manager.update_contact(contact.id, update_data)
                stats["newly_accepted"] += 1

                if progress_callback:
                    progress_callback(f"✅ {contact.name} accepted connection")

            # Random delay between checks — a pure humanization wait, skipped
            # once a stop was requested (mirrors the send loops, issue #43).
            if stop_event is None or not stop_event.is_set():
                await automation.page.wait_for_timeout(random.randint(2000, 4000))

        except Exception as e:
            logger.error(f"Error checking contact {contact_id}: {e}")
            stats["failed"] += 1
            continue

    return stats


async def monitor_pending_connections(
    automation,
    campaign_ids: list[int],
    check_interval_minutes: int = 60,
    max_iterations: int = 24,  # 24 hours if checking every hour
    progress_callback: Callable | None = None,
) -> dict[str, Any]:
    """
    Continuously monitor pending connections for multiple campaigns.

    This function can run in the background to periodically check for new acceptances.
    """
    total_stats = {
        "iterations": 0,
        "total_checked": 0,
        "total_newly_accepted": 0,
        "campaigns_monitored": len(campaign_ids),
    }

    for iteration in range(max_iterations):
        if progress_callback:
            progress_callback(f"Starting monitoring iteration {iteration + 1}/{max_iterations}")

        iteration_stats = {"checked": 0, "newly_accepted": 0}

        for campaign_id in campaign_ids:
            try:
                if progress_callback:
                    progress_callback(f"Checking campaign {campaign_id}...")

                stats = await smart_connection_checker(
                    automation, campaign_id, progress_callback
                )

                iteration_stats["checked"] += stats["checked"]
                iteration_stats["newly_accepted"] += stats["newly_accepted"]

            except Exception as e:
                logger.error(f"Error checking campaign {campaign_id}: {e}")
                if progress_callback:
                    progress_callback(f"Error checking campaign {campaign_id}: {str(e)}")

        total_stats["iterations"] += 1
        total_stats["total_checked"] += iteration_stats["checked"]
        total_stats["total_newly_accepted"] += iteration_stats["newly_accepted"]

        if progress_callback:
            progress_callback(
                f"Iteration {iteration + 1} complete: "
                f"{iteration_stats['newly_accepted']} new acceptances found"
            )

        # Break if no pending connections left
        if iteration_stats["checked"] == 0:
            if progress_callback:
                progress_callback("No pending connections found, stopping monitoring")
            break

        # Wait before next iteration (unless it's the last one)
        if iteration < max_iterations - 1:
            wait_seconds = check_interval_minutes * 60
            if progress_callback:
                progress_callback(f"Waiting {check_interval_minutes} minutes until next check...")
            await automation.page.wait_for_timeout(wait_seconds * 1000)

    return total_stats