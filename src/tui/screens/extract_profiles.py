"""Extract Profile Data screen (issue #24).

Extract detailed public data from a campaign's contacts or from a single manual
profile URL — mirroring the classic ``extract_profile_data``. Read-only: like the
classic flow, results are summarised in the log, not persisted to the database.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Input, Select, Static

from .automation_run import AutomationRunScreen


class ExtractProfilesScreen(AutomationRunScreen):
    SCREEN_TITLE = "Extract Profile Data"
    ACTION_LABEL = "profile extraction"

    def __init__(self, db_manager, settings) -> None:
        super().__init__(db_manager, settings)
        self._campaigns: dict = {}
        self._mode = "campaign"
        self._selected_id = None
        self._manual_url = ""

    def compose_selection(self) -> ComposeResult:
        yield Static("MODE", classes="eyebrow")
        yield Select(
            [("Campaign contacts", "campaign"), ("Manual profile URL", "manual")],
            value="campaign",
            allow_blank=False,
            id="run-mode",
        )
        yield Static("CAMPAIGN", classes="eyebrow")
        yield Select([], prompt="Loading campaigns…", id="run-campaign")
        yield Static("PROFILE URL", classes="eyebrow")
        yield Input(placeholder="https://www.linkedin.com/in/…", id="run-url")

    def ready_hint(self) -> str:
        return "Choose a source, then ctrl+r to start extracting."

    # ── selection data ────────────────────────────────────────────────────

    def fetch_options(self):
        pairs = []
        for c in self._db_manager.get_campaigns(active_only=False):
            count = len(self._db_manager.get_contacts(c.id))
            if count > 0:
                pairs.append((c, count))
        return pairs

    def apply_options(self, pairs) -> None:
        # Manual mode works with no campaigns, so never disable start here.
        if pairs:
            self._campaigns = {c.id: c for c, _ in pairs}
            select = self.query_one("#run-campaign", Select)
            select.set_options(
                [(f"{c.name}  ({count} contacts)", str(c.id)) for c, count in pairs]
            )
            select.value = str(pairs[0][0].id)  # pre-select the first campaign
        self._set_status(self.ready_hint())

    def validate(self):
        self._mode = self.query_one("#run-mode", Select).value
        if self._mode == "manual":
            url = self.query_one("#run-url", Input).value.strip()
            if "linkedin.com/in/" not in url:
                self._set_status(
                    "Enter a valid LinkedIn profile URL (linkedin.com/in/…).", "error"
                )
                return None
            self._manual_url = url
            return "Extract data from this profile?"

        value = self.query_one("#run-campaign", Select).value
        if not isinstance(value, str):
            self._set_status(
                "Select a campaign with contacts, or switch to manual mode.", "error"
            )
            return None
        self._selected_id = int(value)
        name = self._campaigns[self._selected_id].name
        return f"Extract profile data from the contacts of '{name}'?"

    # ── automation ────────────────────────────────────────────────────────

    async def automate(self, automation) -> dict:
        if self._mode == "manual":
            urls = [self._manual_url]
        else:
            contacts = self._db_manager.get_contacts(self._selected_id)
            urls = [c.profile_url for c in contacts if c.profile_url]
        if not urls:
            return {"status": "no_profiles"}

        extracted = 0
        failed = 0
        total = len(urls)
        for index, url in enumerate(urls):
            self.progress(f"Extracting profile {index + 1}/{total}…")
            try:
                data = await automation.extract_detailed_profile(url, self.progress)
            except Exception as exc:  # per-URL, mirroring the classic loop
                self.progress(f"  failed: {exc}")
                failed += 1
                continue
            if data:
                extracted += 1
            else:
                failed += 1
        return {"status": "success", "extracted": extracted, "failed": failed}

    def render_result(self, result: dict) -> str:
        if result.get("status") == "no_profiles":
            return "No profile URLs to extract."
        extracted = result.get("extracted", 0)
        failed = result.get("failed", 0)
        total = extracted + failed
        rate = (extracted / total * 100) if total else 0.0
        return (
            "Extraction complete.\n"
            f"Profiles extracted: {extracted}\n"
            f"Failed: {failed}\n"
            f"Success rate: {rate:.1f}%"
        )
