"""
Connection status monitoring and smart checking for LinkedIn automation.
"""

import logging
import random
from datetime import datetime, timezone
from typing import List, Optional, Callable, Dict, Any

from .interactions import random_wait, scroll_down
from .scraping import get_contact_info

logger = logging.getLogger(__name__)


async def smart_connection_checker(
    automation,  # LinkedInAutomation instance
    campaign_id: int,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, int]:
    """
    Smart checker that monitors LinkedIn connections page to find newly accepted connections.

    This is the main function that should be called from your CLI.

    Returns:
        Dict with counts: {"checked": int, "newly_accepted": int, "updated": int}
    """
    if not automation.is_authenticated:
        raise Exception("Not authenticated. Please login first.")

    if progress_callback:
        progress_callback("Starting smart connection checker...")

    # Get pending contacts for this campaign
    pending_contacts = automation.db_manager.get_contacts_by_status(campaign_id, "sent")

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
            progress_callback
        )

        if progress_callback:
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
    pending_contacts: List,
    limit_url: Optional[str],
    progress_callback: Optional[Callable] = None,
) -> Dict[str, int]:
    """Check the connections page and update database for newly accepted connections."""

    stats = {"checked": 0, "newly_accepted": 0, "updated": 0}
    finish_process = False

    # Create a lookup dict for faster searching
    pending_lookup = {contact.profile_url: contact for contact in pending_contacts}

    while not finish_process:
        # Scroll down to load more connections
        for _ in range(random.randint(4, 6)):
            if progress_callback:
                progress_callback("Scrolling to load more connections...")

            for _ in range(random.randint(18, 24)):
                await automation.page.keyboard.press("ArrowDown")
                await automation.page.wait_for_timeout(random.randint(20, 40))

            await automation.page.wait_for_timeout(random.randint(2000, 4000))

        # Get connection elements
        connections = await automation.page.query_selector_all('[data-view-name="connections-list"]')

        if not connections:
            if progress_callback:
                progress_callback("No connections found on page, stopping...")
            break

        for connection in connections:
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

        if not finish_process and len(connections) < 10:
            # If we got fewer than 10 connections, we might be at the end
            if progress_callback:
                progress_callback("Reached end of connections list")
            break

    return stats


async def _update_accepted_connection(
    automation,
    contact,
    progress_callback: Optional[Callable] = None,
) -> None:
    """Update contact in database as accepted and collect additional info."""

    try:
        # Open the contact's profile to get additional info
        new_page = await automation.context.new_page()
        await new_page.goto(contact.profile_url, timeout=30000)
        await new_page.wait_for_timeout(random.randint(5000, 8000))

        # Get updated contact info
        contact_info = get_contact_info(new_page)

        # Update the contact in database
        update_data = {
            "status": "accepted",
            "connection_accepted_at": datetime.now(timezone.utc),
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
    contact_ids: List[int],
    progress_callback: Optional[Callable] = None,
) -> Dict[str, int]:
    """
    Check specific contacts by visiting their profiles directly.

    This is an alternative to the smart checker for checking specific contacts.
    """
    if not automation.is_authenticated:
        raise Exception("Not authenticated. Please login first.")

    stats = {"checked": 0, "newly_accepted": 0, "failed": 0}

    for contact_id in contact_ids:
        try:
            # Get contact from database
            contact = automation.db_manager.get_contact(contact_id)
            if not contact or contact.status != "sent":
                continue

            if progress_callback:
                progress_callback(f"Checking {contact.name}...")

            # Navigate to profile
            await automation.page.goto(contact.profile_url, timeout=30000)
            await automation.page.wait_for_timeout(random.randint(3000, 5000))

            # Check connection status
            is_connected = await automation.page.is_visible(
                "span:has-text('Connected'), "
                ".pv-top-card__distance-badge:has-text('1st'), "
                "button:has-text('Message')",
                timeout=5000
            )

            stats["checked"] += 1

            if is_connected:
                # Update as accepted
                update_data = {
                    "status": "accepted",
                    "connection_accepted_at": datetime.now(timezone.utc)
                }

                # Try to get contact info
                try:
                    contact_info = get_contact_info(automation.page)
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

            # Random delay between checks
            await automation.page.wait_for_timeout(random.randint(2000, 4000))

        except Exception as e:
            logger.error(f"Error checking contact {contact_id}: {e}")
            stats["failed"] += 1
            continue

    return stats


async def monitor_pending_connections(
    automation,
    campaign_ids: List[int],
    check_interval_minutes: int = 60,
    max_iterations: int = 24,  # 24 hours if checking every hour
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
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