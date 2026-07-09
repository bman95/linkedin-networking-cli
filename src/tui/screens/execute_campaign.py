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
        self._used_today = None

    def compose_selection(self) -> ComposeResult:
        yield Static("ACTIVE CAMPAIGN", classes="eyebrow")
        yield Select([], prompt="Loading campaigns…", id="run-campaign")

    def ready_hint(self) -> str:
        hint = "Select a campaign, then ctrl+r to start sending invites."
        if self._used_today:
            hint += f"  {self._used_today} already sent today."
        return hint

    # ── selection data ────────────────────────────────────────────────────

    def fetch_options(self):
        campaigns = self._db_manager.get_campaigns(active_only=True)
        try:
            from datetime import date

            used_today = self._db_manager.get_daily_connection_count(
                date.today().isoformat()
            )
        except Exception:  # a quota hint must never block the run screen
            used_today = None
        return campaigns, used_today

    def apply_options(self, data) -> None:
        campaigns, self._used_today = data
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
        # A protective stop (inline CAPTCHA / challenge wall) must never be
        # reported as a clean run — checked before the empty-scan mapping so a
        # first-page CAPTCHA doesn't masquerade as "no profiles" (mirrors the
        # classic CLI's _run_campaign_automation).
        if results.get("stopped_reason"):
            results["status"] = "safety_stop"
            return results
        if results.get("scanned", 0) == 0:
            return {"status": "no_profiles"}
        results["status"] = "success"
        return results

    def render_result(self, result: dict) -> str:
        if result.get("status") == "no_profiles":
            return "No profiles matched the campaign's criteria — nothing sent."
        if result.get("status") == "safety_stop":
            lines = [
                "Automation stopped early: LinkedIn presented a "
                "CAPTCHA/challenge.",
                "Resolve it in the browser before running again.",
                f"Invites sent before the stop: {result.get('sent', 0)}",
            ]
            if result.get("possibly_sent", 0):
                lines.append(f"Possibly sent: {result.get('possibly_sent', 0)}")
            return "\n".join(lines)
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
