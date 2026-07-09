"""Shared campaign-form building blocks for the Create and Edit screens (#24).

Both the Create and Edit flows gather the exact same campaign fields, with the
same validation and the same display-name → stored-value mapping the classic
InquirerPy CLI uses (`linkedin_cli.py` create_campaign / edit_campaign). To avoid
two copies, the field widgets, the read+validate step, and the prefill step live
here; each screen keeps its own (different) persistence worker — create calls
``create_campaign``, edit calls ``update_campaign``.

``CampaignFormScreen`` is the shared base class. Besides the status-line helper
it owns the **location** flows the classic CLI exposes beyond the curated list:

- **Search location online** (``SEARCH_ONLINE``): reveals a query input;
  submitting it drives Playwright + a LinkedIn login in a thread worker (the
  same discipline as ``AutomationRunScreen``; ``perform_location_search`` is the
  seam tests override to avoid a browser) and offers the results in a picker.
  A picked result becomes a real option on the Location select, backed by a
  display-name → geoUrn override map.
- **Custom geoUrn** (``CUSTOM_GEO``): reveals two inputs for a hand-entered
  geoUrn code and its display name, mirroring the classic
  "Other (enter custom geoUrn)" option.

The override map also lets ``fill_form`` preserve a stored non-curated location
when editing (the classic Edit adds the current location to its choices; the
TUI previously reset it to "Any").
"""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
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
from utils.logging import get_logger

from .automation_errors import describe_automation_error
from .base import BaseScreen

logger = get_logger(__name__)

DEFAULT_NETWORK = "1st + 2nd degree connections"
DEFAULT_MESSAGE = "Hi {name}, I'd like to connect with you!"
ANY = "Any"

# Sentinel options on the Location select, mirroring the classic CLI's
# "🔎 Search location online (requires login)" / "Other (enter custom geoUrn)".
SEARCH_ONLINE = "Search location online…"
CUSTOM_GEO = "Other (custom geoUrn)…"

# Widgets revealed only while their location mode is active.
_QUERY_IDS = ("#label-location-query", "#field-location-query")
_RESULT_IDS = ("#label-location-results", "#field-location-results")
_CUSTOM_IDS = (
    "#label-location-geourn",
    "#field-location-geourn",
    "#label-location-name",
    "#field-location-name",
)


def campaign_form_widgets() -> ComposeResult:
    """Yield the campaign form fields, in order, inside a scroll container.

    Used via ``yield from`` from a screen's ``compose_body`` so the field ids are
    identical across Create and Edit. The location query/results/custom widgets
    start hidden (``.loc-conditional``) and are revealed by the Location select.
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
            [(n, n) for n in _location_options()],
            value=ANY,
            allow_blank=False,
            id="field-location",
        )
        yield Label("Search query", classes="field-label loc-conditional",
                    id="label-location-query")
        yield Input(placeholder="e.g. Madrid — press enter to search (logs in)",
                    id="field-location-query", classes="loc-conditional")
        yield Label("Search results", classes="field-label loc-conditional",
                    id="label-location-results")
        yield Select([], prompt="Pick a location", id="field-location-results",
                     classes="loc-conditional")
        yield Label("geoUrn code (LinkedIn URL: geoUrn=[\"CODE\"])",
                    classes="field-label loc-conditional", id="label-location-geourn")
        yield Input(placeholder="e.g. 90000084", id="field-location-geourn",
                    classes="loc-conditional")
        yield Label("Location name (for display)",
                    classes="field-label loc-conditional", id="label-location-name")
        yield Input(placeholder="Defaults to 'Custom Location (<geoUrn>)'",
                    id="field-location-name", classes="loc-conditional")
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


def _location_options(overrides: tuple[str, ...] = ()) -> list[str]:
    """Location select options: curated list + overrides + the two sentinels."""
    names = get_location_display_names()
    names.extend(n for n in overrides if n not in names)
    names.extend((SEARCH_ONLINE, CUSTOM_GEO))
    return names


class CampaignFormScreen(BaseScreen):
    """Shared base for the Create/Edit campaign forms.

    Owns the status-line helper and the two non-curated location flows (online
    search, custom geoUrn). Subclasses set ``STATUS_ID`` and keep their own
    persistence workers.
    """

    # The status Static's selector; subclasses override.
    STATUS_ID = "#form-status"

    def __init__(self) -> None:
        super().__init__()
        # Display name → geoUrn for locations outside the curated list (picked
        # from an online search, or carried over from the edited campaign).
        self._location_overrides: dict[str, str] = {}
        self._search_in_flight = False
        # Captured on the UI thread when a search starts, for progress marshaling.
        self._search_app_ref: App | None = None
        # Snapshot of the pristine field values (set via mark_clean once the
        # form is ready); esc on a dirty form warns before discarding.
        self._baseline: tuple | None = None
        self._discard_confirming = False

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one(self.STATUS_ID, Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        # Text() renders literally: messages carry raw exception text and user
        # input, whose square brackets must not be parsed as markup (a selector
        # like locator([role='option']) would otherwise be silently swallowed).
        status.update(Text(message))

    # ── dirty-form guard ──────────────────────────────────────────────────

    _SNAPSHOT_FIELDS = ("#field-name", "#field-description", "#field-keywords",
                        "#field-daily", "#field-message",
                        # Conditional location inputs: a typed custom geoUrn or
                        # search query is typed work too — esc must warn first.
                        "#field-location-query", "#field-location-geourn",
                        "#field-location-name")
    _SNAPSHOT_SELECTS = ("#field-location", "#field-network", "#field-industry")

    def _snapshot(self) -> tuple:
        values = [self.query_one(f, Input).value for f in self._SNAPSHOT_FIELDS]
        values.extend(str(self.query_one(s, Select).value) for s in self._SNAPSHOT_SELECTS)
        return tuple(values)

    def mark_clean(self) -> None:
        """Record the current field values as the pristine state."""
        self._baseline = self._snapshot()

    def action_back(self) -> None:
        """``esc``: warn once before discarding unsaved edits, else leave.

        A locked form (already created/saved) and a pristine one pop straight
        back; a dirty one asks for a second esc so one keystroke can't silently
        throw away typed work.
        """
        locked = getattr(self, "_created", False) or getattr(self, "_saved", False)
        dirty = (
            not locked
            and self._baseline is not None
            and self._snapshot() != self._baseline
        )
        if dirty and not self._discard_confirming:
            self._discard_confirming = True
            self._set_status(
                "Unsaved changes — press esc again to discard them.", "warn"
            )
            return
        self.app.pop_screen()

    def on_input_changed(self, event: Input.Changed) -> None:
        # Typing again withdraws a pending discard confirmation.
        self._discard_confirming = False

    # ── location select modes ─────────────────────────────────────────────

    def on_select_changed(self, event: Select.Changed) -> None:
        # Changing a select withdraws a pending discard confirmation.
        self._discard_confirming = False
        if event.select.id == "field-location":
            self._location_mode_changed(event.value)
        elif event.select.id == "field-location-results":
            self._location_result_picked(event.value)

    def _show(self, selectors: tuple[str, ...], visible: bool) -> None:
        for selector in selectors:
            self.query_one(selector).display = visible

    def _location_mode_changed(self, value: object) -> None:
        searching = value == SEARCH_ONLINE
        self._show(_QUERY_IDS, searching)
        if not searching:
            self._show(_RESULT_IDS, False)
        self._show(_CUSTOM_IDS, value == CUSTOM_GEO)
        if searching:
            self.query_one("#field-location-query", Input).focus()
            self._set_status(
                "Type a location and press enter to search LinkedIn "
                "(opens a browser and logs in)."
            )
        elif value == CUSTOM_GEO:
            self.query_one("#field-location-geourn", Input).focus()
            self._set_status(
                "Enter a geoUrn code (find it in a LinkedIn search URL) and, "
                "optionally, a display name."
            )

    def _refresh_location_options(self, selected: str) -> None:
        """Rebuild the Location select (curated + overrides + sentinels)."""
        select = self.query_one("#field-location", Select)
        select.set_options(
            [(n, n) for n in _location_options(tuple(self._location_overrides))]
        )
        select.value = selected

    # ── online search ─────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "field-location-query":
            self._start_location_search()

    def _start_location_search(self) -> None:
        if self._search_in_flight:
            return
        query = self.query_one("#field-location-query", Input).value.strip()
        if not query:
            self._set_status("Enter a location to search for.", "error")
            return
        app = self.app
        db = getattr(app, "db_manager", None)
        settings = getattr(app, "settings", None)
        if db is None or settings is None:
            self._set_status(
                "Online search requires database access and app settings.", "error"
            )
            return
        self._search_in_flight = True
        self._search_app_ref = app
        self.query_one("#field-location-query", Input).disabled = True
        self._show(_RESULT_IDS, False)
        self._set_status(f"Searching LinkedIn locations for '{query}'…")
        self._run_location_search(app, db, settings, query)

    def perform_location_search(self, db, settings, query: str) -> list | None:
        """Login and query LinkedIn's location typeahead (worker thread).

        Returns ``{"name", "geoUrn"}`` dicts, ``[]`` when nothing matched, or
        ``None`` when authentication failed — the classic CLI's contract
        (``_run_location_search``). The seam tests override to avoid a browser.
        """
        from automation.linkedin import LinkedInAutomation

        async def lookup():
            async with LinkedInAutomation(db, settings) as automation:
                ok = await automation.login(self._search_progress)
                if not ok:
                    return None
                return await automation.search_location(query)

        return asyncio.run(lookup())

    @work(thread=True, exclusive=True, group="location-search")
    def _run_location_search(self, app: App, db, settings, query: str) -> None:
        try:
            results = self.perform_location_search(db, settings, query)
        except Exception as exc:  # hard-stop with evidence, in the status line
            logger.debug("Location search failed", exc_info=True)
            headline, _ = describe_automation_error(exc, "location search")
            self.marshal(app, self._search_done, query, None, headline)
            return
        self.marshal(app, self._search_done, query, results, None)

    def _search_progress(self, message: object) -> None:
        """Login/progress sink (worker thread) → status line."""
        self.marshal(self._search_app_ref, self._set_status, str(message))

    def _search_done(
        self, query: str, results: list | None, error: str | None
    ) -> None:
        self._search_in_flight = False
        self.query_one("#field-location-query", Input).disabled = False
        if error is not None:
            self._set_status(error, "error")
            return
        if results is None:
            self._set_status("Could not authenticate with LinkedIn.", "error")
            return
        if not results:
            self._set_status(f"No locations found for '{query}'.", "warn")
            return
        picker = self.query_one("#field-location-results", Select)
        picker.set_options(
            [
                (f"{item.get('name', '?')} (geoUrn {item.get('geoUrn', '?')})",
                 (item.get("name"), item.get("geoUrn")))
                for item in results
            ]
        )
        self._show(_RESULT_IDS, True)
        picker.focus()
        noun = "location" if len(results) == 1 else "locations"
        self._set_status(f"{len(results)} {noun} found — pick one.")

    def _location_result_picked(self, value: object) -> None:
        if not isinstance(value, tuple):
            return  # Select.BLANK (prompt) — nothing picked yet
        name, geo_urn = value
        if not name or not geo_urn:
            return
        self._location_overrides[str(name)] = str(geo_urn)
        # Promote the pick to a first-class Location option and select it; the
        # resulting Select.Changed hides the search widgets again.
        self._refresh_location_options(selected=str(name))
        self._set_status(f"Location set to {name} (geoUrn {geo_urn}).")


def read_form(screen: CampaignFormScreen) -> tuple[dict | None, tuple[str, str] | None]:
    """Validate the form and build the campaign-data dict.

    Returns ``(campaign_data, None)`` on success, or ``(None, (message, field_id))``
    on the first validation failure so the caller can show the message and focus
    the offending field. Mapping mirrors the classic CLI exactly: ``Any``
    location/industry persist as ``None``; an online-search pick or a custom
    geoUrn provides the code directly, otherwise the curated list maps it.
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

    if location_display == SEARCH_ONLINE:
        return None, (
            "Search and pick a location first (or choose one from the list).",
            "#field-location-query",
        )
    if location_display == CUSTOM_GEO:
        geo_urn = screen.query_one("#field-location-geourn", Input).value.strip()
        if not geo_urn:
            return None, ("Enter a geoUrn code for the custom location.",
                          "#field-location-geourn")
        # Mirror the classic CLI's validator: a geoUrn is a numeric code.
        if not geo_urn.isdigit():
            return None, ("geoUrn must be a numeric code.",
                          "#field-location-geourn")
        custom_name = screen.query_one("#field-location-name", Input).value.strip()
        location_display = custom_name or f"Custom Location ({geo_urn})"
    elif location_display in screen._location_overrides:
        geo_urn = screen._location_overrides[location_display]
    else:
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


def fill_form(screen: CampaignFormScreen, campaign) -> None:
    """Prefill the form fields from an existing ``Campaign`` (for editing)."""
    screen.query_one("#field-name", Input).value = campaign.name or ""
    screen.query_one("#field-description", Input).value = campaign.description or ""
    screen.query_one("#field-keywords", Input).value = campaign.keywords or ""
    screen.query_one("#field-daily", Input).value = str(campaign.daily_limit)
    screen.query_one("#field-message", Input).value = campaign.message_template or DEFAULT_MESSAGE

    # A stored location outside the curated list (an earlier online search or
    # custom geoUrn) is preserved as an override option — the classic Edit adds
    # it to its choices the same way. Without its geoUrn it can't be kept, so
    # fall back to the neutral default rather than persist a dangling name.
    location_display = campaign.location_display
    geo_urn = getattr(campaign, "geo_urn", None)
    curated = get_location_display_names()
    if location_display and location_display not in curated and geo_urn:
        screen._location_overrides[location_display] = geo_urn
    selected = location_display if location_display in _location_options(
        tuple(screen._location_overrides)
    ) and location_display not in (SEARCH_ONLINE, CUSTOM_GEO) else ANY
    screen._refresh_location_options(selected=selected or ANY)

    _set_select(screen, "#field-network", campaign.network_display, DEFAULT_NETWORK,
                get_network_display_names())
    _set_select(screen, "#field-industry", campaign.industry_display, ANY,
                get_industry_display_names())


def _set_select(screen, selector: str, value: str | None, default: str, options) -> None:
    screen.query_one(selector, Select).value = value if value in options else default
