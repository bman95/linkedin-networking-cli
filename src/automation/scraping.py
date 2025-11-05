"""
Profile data extraction and contact information scraping from LinkedIn.
"""

import logging
import re
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


def get_contact_info(page) -> Dict[str, Optional[str]]:
    """Extract contact information from LinkedIn profile."""
    contact_info = {
        "email": None,
        "phone": None,
        "address": None,
        "connection_accepted_date": None,
    }

    try:
        # Click on "Contact info" section if available
        contact_button = page.query_selector(
            "a[data-test-link-to-profile-contact-info], "
            "button[aria-label*='Contact info'], "
            "a:has-text('Contact info')"
        )

        if contact_button and contact_button.is_visible():
            contact_button.click()
            page.wait_for_timeout(2000)

            # Wait for contact info modal/section to load
            contact_section = page.query_selector(
                "#pv-contact-info, "
                "[data-test-modal-id='contact-info'], "
                ".pv-contact-info__contact-type"
            )

            if contact_section:
                # Extract email
                email_selectors = [
                    "a[href^='mailto:']",
                    ".pv-contact-info__contact-type:has-text('Email') a",
                    ".ci-email a",
                ]

                for selector in email_selectors:
                    email_element = page.query_selector(selector)
                    if email_element:
                        email_text = email_element.get_attribute("href") or email_element.inner_text()
                        if email_text:
                            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', email_text)
                            if email_match:
                                contact_info["email"] = email_match.group()
                                break

                # Extract phone
                phone_selectors = [
                    "a[href^='tel:']",
                    ".pv-contact-info__contact-type:has-text('Phone') span",
                    ".ci-phone span",
                ]

                for selector in phone_selectors:
                    phone_element = page.query_selector(selector)
                    if phone_element:
                        phone_text = phone_element.get_attribute("href") or phone_element.inner_text()
                        if phone_text:
                            # Clean phone number
                            phone_clean = re.sub(r'[^\d+\-\(\)\s]', '', phone_text)
                            if phone_clean and len(phone_clean) > 5:
                                contact_info["phone"] = phone_clean.strip()
                                break

                # Extract address
                address_selectors = [
                    ".pv-contact-info__contact-type:has-text('Address') span",
                    ".ci-address span",
                    ".pv-contact-info__address",
                ]

                for selector in address_selectors:
                    address_element = page.query_selector(selector)
                    if address_element:
                        address_text = address_element.inner_text()
                        if address_text and address_text.strip():
                            contact_info["address"] = address_text.strip()
                            break

            # Close contact info modal if it's a modal
            close_button = page.query_selector(
                "button[aria-label='Dismiss'], "
                ".artdeco-modal__dismiss, "
                "button[data-test-modal-close-btn]"
            )
            if close_button and close_button.is_visible():
                close_button.click()
                page.wait_for_timeout(1000)

        # Check if already connected (connection accepted date)
        connected_indicators = [
            "span:has-text('Connected')",
            ".pv-top-card__distance-badge:has-text('1st')",
            "time[datetime]",  # Connection date might be in time element
        ]

        for selector in connected_indicators:
            element = page.query_selector(selector)
            if element and element.is_visible():
                # Try to extract connection date
                datetime_attr = element.get_attribute("datetime")
                if datetime_attr:
                    try:
                        contact_info["connection_accepted_date"] = datetime.fromisoformat(datetime_attr)
                    except:
                        contact_info["connection_accepted_date"] = datetime.now()
                else:
                    contact_info["connection_accepted_date"] = datetime.now()
                break

    except Exception as e:
        logger.warning(f"Error extracting contact info: {e}")

    return contact_info


def get_profession(page) -> Optional[str]:
    """Extract profession/headline from LinkedIn profile."""
    try:
        profession_selectors = [
            ".text-body-medium.break-words",  # Main headline
            ".pv-text-details__left-panel h1 + div",
            ".top-card-layout__headline",
            ".pv-top-card--experience-list-item .pv-entity__summary-info h3",
        ]

        for selector in profession_selectors:
            element = page.query_selector(selector)
            if element and element.is_visible():
                profession = element.inner_text().strip()
                if profession and len(profession) > 3:
                    return profession

    except Exception as e:
        logger.warning(f"Error extracting profession: {e}")

    return None


def get_location(page) -> Optional[str]:
    """Extract location from LinkedIn profile."""
    try:
        location_selectors = [
            ".text-body-small.inline.t-black--light.break-words",  # New LinkedIn layout
            ".pv-text-details__left-panel .text-body-small",
            ".top-card-layout__headline + div",
            ".pv-top-card__location",
        ]

        for selector in location_selectors:
            element = page.query_selector(selector)
            if element and element.is_visible():
                location = element.inner_text().strip()
                if location and len(location) > 2:
                    return location

    except Exception as e:
        logger.warning(f"Error extracting location: {e}")

    return None


def get_experience(page) -> List[Dict[str, Optional[str]]]:
    """Extract work experience from LinkedIn profile."""
    experience = []

    try:
        # Scroll to experience section
        experience_section = page.query_selector(
            "#experience, "
            "[data-test-id='experience-section'], "
            ".pv-profile-section.experience-section"
        )

        if experience_section:
            experience_section.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)

            # Extract experience items
            experience_items = page.query_selector_all(
                ".pv-entity__summary-info, "
                ".pvs-entity, "
                ".experience-item, "
                ".pv-profile-section__list-item"
            )

            for item in experience_items[:10]:  # Limit to first 10 experiences
                try:
                    exp_data = {
                        "title": None,
                        "company": None,
                        "date_range": None,
                        "location": None,
                    }

                    # Extract job title
                    title_selectors = [
                        ".t-16.t-black.t-bold",
                        ".pv-entity__summary-info h3",
                        ".pvs-entity__caption-wrapper .t-14.t-black.t-bold",
                    ]

                    for selector in title_selectors:
                        title_element = item.query_selector(selector)
                        if title_element:
                            exp_data["title"] = title_element.inner_text().strip()
                            break

                    # Extract company name
                    company_selectors = [
                        ".t-14.t-black.t-normal span[aria-hidden='true']",
                        ".pv-entity__secondary-title",
                        ".pvs-entity__caption-wrapper .t-14.t-black--light",
                    ]

                    for selector in company_selectors:
                        company_element = item.query_selector(selector)
                        if company_element:
                            company_text = company_element.inner_text().strip()
                            # Clean company name (remove "at" prefix if present)
                            if company_text.lower().startswith("at "):
                                company_text = company_text[3:]
                            exp_data["company"] = company_text
                            break

                    # Extract date range
                    date_selectors = [
                        ".t-14.t-black--light.t-normal span[aria-hidden='true']",
                        ".pv-entity__date-range",
                        ".pvs-entity__caption-wrapper .t-14.t-black--light span",
                    ]

                    for selector in date_selectors:
                        date_element = item.query_selector(selector)
                        if date_element:
                            date_text = date_element.inner_text().strip()
                            if any(keyword in date_text.lower() for keyword in ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "present", "now", "current"]):
                                exp_data["date_range"] = date_text
                                break

                    # Extract location
                    location_selectors = [
                        ".t-14.t-black--light.t-normal.pb1 span",
                        ".pv-entity__location",
                    ]

                    for selector in location_selectors:
                        location_element = item.query_selector(selector)
                        if location_element:
                            location_text = location_element.inner_text().strip()
                            if location_text and len(location_text) > 2:
                                exp_data["location"] = location_text
                                break

                    # Only add if we have meaningful data
                    if exp_data["title"] or exp_data["company"]:
                        experience.append(exp_data)

                except Exception as item_error:
                    logger.debug(f"Error extracting individual experience item: {item_error}")
                    continue

    except Exception as e:
        logger.warning(f"Error extracting experience: {e}")

    return experience


def get_education(page) -> List[Dict[str, Optional[str]]]:
    """Extract education from LinkedIn profile."""
    education = []

    try:
        # Scroll to education section
        education_section = page.query_selector(
            "#education, "
            "[data-test-id='education-section'], "
            ".pv-profile-section.education-section"
        )

        if education_section:
            education_section.scroll_into_view_if_needed()
            page.wait_for_timeout(1000)

            # Extract education items
            education_items = page.query_selector_all(
                ".pv-entity__summary-info, "
                ".pvs-entity, "
                ".education-item, "
                ".pv-profile-section__list-item"
            )

            for item in education_items[:10]:  # Limit to first 10 education entries
                try:
                    edu_data = {
                        "institution": None,
                        "degree": None,
                        "date_range": None,
                    }

                    # Extract institution name
                    institution_selectors = [
                        ".t-16.t-black.t-bold",
                        ".pv-entity__school-name",
                        ".pvs-entity__caption-wrapper .t-14.t-black.t-bold",
                    ]

                    for selector in institution_selectors:
                        institution_element = item.query_selector(selector)
                        if institution_element:
                            edu_data["institution"] = institution_element.inner_text().strip()
                            break

                    # Extract degree
                    degree_selectors = [
                        ".t-14.t-black.t-normal span[aria-hidden='true']",
                        ".pv-entity__degree-name",
                        ".pvs-entity__caption-wrapper .t-14.t-black--light",
                    ]

                    for selector in degree_selectors:
                        degree_element = item.query_selector(selector)
                        if degree_element:
                            degree_text = degree_element.inner_text().strip()
                            if degree_text and "·" not in degree_text:  # Avoid date ranges
                                edu_data["degree"] = degree_text
                                break

                    # Extract date range
                    date_selectors = [
                        ".t-14.t-black--light.t-normal span",
                        ".pv-entity__dates",
                        ".pvs-entity__caption-wrapper .t-14.t-black--light",
                    ]

                    for selector in date_selectors:
                        date_element = item.query_selector(selector)
                        if date_element:
                            date_text = date_element.inner_text().strip()
                            # Check if it looks like a date range
                            if any(char in date_text for char in ["20", "19", "-", "–"]):
                                edu_data["date_range"] = date_text
                                break

                    # Only add if we have meaningful data
                    if edu_data["institution"]:
                        education.append(edu_data)

                except Exception as item_error:
                    logger.debug(f"Error extracting individual education item: {item_error}")
                    continue

    except Exception as e:
        logger.warning(f"Error extracting education: {e}")

    return education


def get_open_to_work_status(page) -> bool:
    """Check if candidate has 'Open to Work' badge."""
    try:
        open_to_work_indicators = [
            ".open-to-work-badge",
            "[data-test-id='open-to-work-badge']",
            ".pv-open-to-work-badge",
            "span:has-text('Open to work')",
            ".artdeco-badge:has-text('Open to work')",
        ]

        for selector in open_to_work_indicators:
            element = page.query_selector(selector)
            if element and element.is_visible():
                return True

        # Also check in profile text
        page_content = page.content().lower()
        if "open to work" in page_content or "opentowork" in page_content:
            return True

        return False

    except Exception as e:
        logger.warning(f"Error checking open to work status: {e}")
        return False


def collect_public_information(page) -> Tuple[Optional[str], Optional[str], List[Dict], List[Dict]]:
    """
    Collect comprehensive public profile information.

    Returns:
        Tuple of (profession, location, experience_list, education_list)
    """
    try:
        # Wait for page to load
        page.wait_for_timeout(2000)

        # Get basic info
        profession = get_profession(page)
        location = get_location(page)

        # Get detailed info (requires scrolling)
        experience = get_experience(page)
        education = get_education(page)

        logger.info(f"Collected profile data: profession={profession}, location={location}, "
                   f"experience_items={len(experience)}, education_items={len(education)}")

        return profession, location, experience, education

    except Exception as e:
        logger.error(f"Error collecting public information: {e}")
        return None, None, [], []


def open_public_profile(page):
    """
    Navigate from LinkedIn Recruiter page to public profile.
    Returns the new page with public profile, or None if failed.
    """
    try:
        # Look for public profile trigger button
        trigger_selector = "button[data-test-public-profile-trigger]"
        trigger_button = page.query_selector(trigger_selector)

        if trigger_button and trigger_button.is_visible():
            logger.info("Clicking 'public profile' button")
            trigger_button.click()
            page.wait_for_timeout(2000)

            # Get the public profile link
            link_element = page.query_selector("a[data-test-public-profile-link]")
            if link_element:
                profile_url = link_element.get_attribute("href")
                if profile_url:
                    # Ensure URL ends with /
                    if not profile_url.endswith("/"):
                        profile_url += "/"

                    # Open public profile in new page
                    context = page.context
                    new_page = context.new_page()
                    new_page.goto(profile_url, timeout=30000)
                    new_page.wait_for_timeout(2000)

                    logger.info(f"Opened public profile: {profile_url}")
                    return new_page

        logger.warning("Could not find or click public profile button")
        return None

    except Exception as e:
        logger.error(f"Error opening public profile: {e}")
        return None