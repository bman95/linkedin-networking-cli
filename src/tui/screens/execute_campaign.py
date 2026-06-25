"""Execute Campaign screen (issue #24).

Pick an active campaign, confirm, then run the modern single-pass
``search_and_connect`` (issue #25) and stream its progress into the log. Mirrors
the classic CLI's ``execute_campaign``: same active-only selection, same
confirmation, same ``search_limit`` from automation settings, same summary keys.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Select, Static

from .automation_run import AutomationRunScreen


class ExecuteCampaignScreen(AutomationRunScreen):
    SCREEN_TITLE = "Execute Campaign"
    ACTION_LABEL = "campaign execution"

    def __init__(self, db_manager, settings) -> None:
        super().__init__(db_manager, settings)
        self._campaigns: dict = {}
        self._selected = None

    def compose_selection(self) -> ComposeResult:
        yield Static("ACTIVE CAMPAIGN", classes="eyebrow")
        yield Select([], prompt="Loading campaigns…", id="run-campaign")

    def ready_hint(self) -> str:
        return "Select a campaign, then ctrl+r to start sending invites."

    # ── selection data ────────────────────────────────────────────────────

    def fetch_options(self):
        return self._db_manager.get_campaigns(active_only=True)

    def apply_options(self, campaigns) -> None:
        if not campaigns:
            self._run_can_start = False
            self._set_status(
                "No active campaigns to run. Create or activate one first.", "warn"
            )
            return
        self._campaigns = {c.id: c for c in campaigns}
        select = self.query_one("#run-campaign", Select)
        select.set_options(
            [(f"{c.name}  (daily limit {c.daily_limit})", str(c.id)) for c in campaigns]
        )
        select.value = str(campaigns[0].id)  # pre-select the first for a fast start
        self._set_status(self.ready_hint())

    def validate(self):
        value = self.query_one("#run-campaign", Select).value
        if not isinstance(value, str):
            self._set_status("Select a campaign to run.", "error")
            return None
        self._selected = self._campaigns.get(int(value))
        if self._selected is None:
            self._set_status("Select a campaign to run.", "error")
            return None
        return (
            f"Start automation for '{self._selected.name}'? "
            "This opens LinkedIn and sends connection requests."
        )

    # ── automation ────────────────────────────────────────────────────────

    async def automate(self, automation) -> dict:
        limit = self._settings.get_automation_settings().get("search_limit", 100)
        self.progress(f"Searching for up to {limit} targeted profiles…")
        results = await automation.search_and_connect(
            self._selected, limit=limit, progress_callback=self.progress
        )
        if results.get("scanned", 0) == 0:
            return {"status": "no_profiles"}
        results["status"] = "success"
        return results

    def render_result(self, result: dict) -> str:
        if result.get("status") == "no_profiles":
            return "No profiles matched the campaign's criteria — nothing sent."
        lines = [
            "Run complete.",
            f"Profiles scanned: {result.get('scanned', 0)}",
            f"Invites sent: {result.get('sent', 0)}",
        ]
        if result.get("possibly_sent", 0):
            lines.append(f"Possibly sent: {result.get('possibly_sent', 0)}")
        lines += [
            f"Already connected / pending: {result.get('existing', 0)}",
            f"Failed: {result.get('failed', 0)}",
            f"Total processed: {result.get('total_processed', 0)}",
        ]
        return "\n".join(lines)
