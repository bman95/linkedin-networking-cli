"""Shared campaign-form building blocks for the Create and Edit screens (#24).

Both the Create and Edit flows gather the exact same campaign fields, with the
same validation and the same display-name → stored-value mapping the classic
InquirerPy CLI uses (`linkedin_cli.py` create_campaign / edit_campaign). To avoid
two copies, the field widgets, the read+validate step, and the prefill step live
here as plain helpers; each screen keeps its own (different) persistence worker
— create calls ``create_campaign``, edit calls ``update_campaign``.

Deliberately browser-free: the classic "search location online" option (and its
custom-geoUrn fallback) is omitted — both drive Playwright + a login and belong
to the automation slice.
"""

from __future__ import annotations

from typing import Optional, Tuple

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Input, Label, Select, Static

from automation.linkedin_mappings import (
    get_industry_display_names,
    get_industry_id,
    get_location_display_names,
    get_location_urn,
    get_network_display_names,
    get_network_value,
)

DEFAULT_NETWORK = "1st + 2nd degree connections"
DEFAULT_MESSAGE = "Hi {name}, I'd like to connect with you!"
ANY = "Any"


def campaign_form_widgets() -> ComposeResult:
    """Yield the campaign form fields, in order, inside a scroll container.

    Used via ``yield from`` from a screen's ``compose_body`` so the field ids are
    identical across Create and Edit.
    """
    with VerticalScroll(id="form-body"):
        yield Static("CAMPAIGN", classes="eyebrow")
        yield Label("Name", classes="field-label")
        yield Input(placeholder="e.g. Senior Backend Engineers — SF", id="field-name")
        yield Label("Description", classes="field-label")
        yield Input(placeholder="Optional", id="field-description")
        yield Label("Keywords", classes="field-label")
        yield Input(placeholder="e.g. software engineer (optional)", id="field-keywords")

        yield Static("TARGETING", classes="eyebrow")
        yield Label("Location", classes="field-label")
        yield Select(
            [(n, n) for n in get_location_display_names()],
            value=ANY,
            allow_blank=False,
            id="field-location",
        )
        yield Label("Connection degree", classes="field-label")
        yield Select(
            [(n, n) for n in get_network_display_names()],
            value=DEFAULT_NETWORK,
            allow_blank=False,
            id="field-network",
        )
        yield Label("Industry", classes="field-label")
        yield Select(
            [(n, n) for n in get_industry_display_names()],
            value=ANY,
            allow_blank=False,
            id="field-industry",
        )

        yield Static("LIMITS & MESSAGE", classes="eyebrow")
        yield Label("Daily connection limit", classes="field-label")
        yield Input(value="20", type="integer", id="field-daily")
        yield Label("Connection message template", classes="field-label")
        yield Input(value=DEFAULT_MESSAGE, id="field-message")


def read_form(screen) -> Tuple[Optional[dict], Optional[Tuple[str, str]]]:
    """Validate the form and build the campaign-data dict.

    Returns ``(campaign_data, None)`` on success, or ``(None, (message, field_id))``
    on the first validation failure so the caller can show the message and focus
    the offending field. Mapping mirrors the classic CLI exactly: ``Any``
    location/industry persist as ``None``.
    """
    name = screen.query_one("#field-name", Input).value.strip()
    if not name:
        return None, ("Campaign name cannot be empty.", "#field-name")

    daily_raw = screen.query_one("#field-daily", Input).value.strip()
    try:
        daily_limit = int(daily_raw)
    except ValueError:
        daily_limit = 0
    if not 1 <= daily_limit <= 100:
        return None, ("Daily limit must be a number between 1 and 100.", "#field-daily")

    message_template = screen.query_one("#field-message", Input).value
    if "{name}" not in message_template:
        return None, ("Message must contain the {name} placeholder.", "#field-message")

    description = screen.query_one("#field-description", Input).value.strip()
    keywords = screen.query_one("#field-keywords", Input).value.strip()
    location_display = screen.query_one("#field-location", Select).value
    network_display = screen.query_one("#field-network", Select).value
    industry_display = screen.query_one("#field-industry", Select).value

    geo_urn = get_location_urn(location_display) if location_display != ANY else None
    industry_id = get_industry_id(industry_display) if industry_display != ANY else None

    data = {
        "name": name,
        "description": description or None,
        "keywords": keywords or None,
        "geo_urn": geo_urn or None,
        "location_display": location_display if location_display != ANY else None,
        "network": get_network_value(network_display),
        "network_display": network_display,
        "industry_ids": industry_id or None,
        "industry_display": industry_display if industry_display != ANY else None,
        "daily_limit": daily_limit,
        "message_template": message_template,
    }
    return data, None


def fill_form(screen, campaign) -> None:
    """Prefill the form fields from an existing ``Campaign`` (for editing)."""
    screen.query_one("#field-name", Input).value = campaign.name or ""
    screen.query_one("#field-description", Input).value = campaign.description or ""
    screen.query_one("#field-keywords", Input).value = campaign.keywords or ""
    screen.query_one("#field-daily", Input).value = str(campaign.daily_limit)
    screen.query_one("#field-message", Input).value = campaign.message_template or DEFAULT_MESSAGE

    # Only set a Select to a stored display value if it's a valid option;
    # a custom location (e.g. from the classic online search) wouldn't be in the
    # static list, so fall back to the neutral default rather than crash.
    _set_select(screen, "#field-location", campaign.location_display, ANY,
                get_location_display_names())
    _set_select(screen, "#field-network", campaign.network_display, DEFAULT_NETWORK,
                get_network_display_names())
    _set_select(screen, "#field-industry", campaign.industry_display, ANY,
                get_industry_display_names())


def _set_select(screen, selector: str, value: Optional[str], default: str, options) -> None:
    screen.query_one(selector, Select).value = value if value in options else default
