"""Check Connections screen (issue #24).

Pick a campaign with pending invites (or all), pick the smart or direct checker,
confirm, then reconcile acceptances — mirroring the classic ``connection_checker``.
Smart uses ``smart_connection_checker`` (the connections page); direct uses
``check_connection_status`` over the campaign's sent / possibly-sent contacts.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Select, Static

from .automation_run import AutomationRunScreen


class CheckConnectionsScreen(AutomationRunScreen):
    SCREEN_TITLE = "Check Connections"
    ACTION_LABEL = "connection check"

    def __init__(self, db_manager, settings) -> None:
        super().__init__(db_manager, settings)
        self._campaigns: dict = {}
        self._target_ids: list = []
        self._mode = "smart"

    def compose_selection(self) -> ComposeResult:
        yield Static("CAMPAIGN", classes="eyebrow")
        yield Select([], prompt="Loading campaigns…", id="run-campaign")
        yield Static("CHECKER", classes="eyebrow")
        yield Select(
            [
                ("Smart checker (scan the connections page)", "smart"),
                ("Direct checker (visit each profile)", "direct"),
            ],
            value="smart",
            allow_blank=False,
            id="run-mode",
        )

    def ready_hint(self) -> str:
        return "Pick a campaign and checker, then ctrl+r to start."

    # ── selection data ────────────────────────────────────────────────────

    def fetch_options(self):
        pairs = []
        for c in self._db_manager.get_campaigns(active_only=False):
            pending = len(self._db_manager.get_contacts_by_status(c.id, "sent")) + len(
                self._db_manager.get_contacts_by_status(c.id, "possibly_sent")
            )
            if pending > 0:
                pairs.append((c, pending))
        return pairs

    def apply_options(self, pairs) -> None:
        if not pairs:
            self._run_can_start = False
            self._set_status(
                "No campaigns with pending connections to check.", "warn"
            )
            return
        self._campaigns = {c.id: c for c, _ in pairs}
        options = [(f"{c.name}  ({pending} pending)", str(c.id)) for c, pending in pairs]
        options.append(("Check all campaigns", "all"))
        select = self.query_one("#run-campaign", Select)
        select.set_options(options)
        select.value = str(pairs[0][0].id)  # pre-select the first campaign
        self._set_status(self.ready_hint())

    def validate(self):
        value = self.query_one("#run-campaign", Select).value
        if not isinstance(value, str):
            self._set_status("Select a campaign (or 'all') to check.", "error")
            return None
        self._mode = self.query_one("#run-mode", Select).value
        if value == "all":
            self._target_ids = list(self._campaigns.keys())
            target = "all campaigns"
        else:
            cid = int(value)
            self._target_ids = [cid]
            target = f"'{self._campaigns[cid].name}'"
        return f"Check connections for {target} using the {self._mode} checker?"

    # ── automation ────────────────────────────────────────────────────────

    async def automate(self, automation) -> dict:
        total_checked = 0
        total_new = 0
        stopped = False
        for cid in self._target_ids:
            # Cooperative cancellation (issue #43): the campaign boundary is a
            # safe point too, and the checkers poll the same flag per profile.
            if self._stop_event is not None and self._stop_event.is_set():
                stopped = True
                break
            if self._mode == "smart":
                stats = await automation.smart_connection_checker(
                    cid, self.progress, stop_event=self._stop_event
                )
                total_checked += stats.get("checked", 0)
                total_new += stats.get("newly_accepted", 0)
                stopped = stopped or bool(stats.get("stopped"))
            else:
                contacts = self._db_manager.get_contacts_by_status(
                    cid, "sent"
                ) + self._db_manager.get_contacts_by_status(cid, "possibly_sent")
                newly = await automation.check_connection_status(
                    contacts, self.progress, stop_event=self._stop_event
                )
                total_checked += len(contacts)
                total_new += newly
        # The direct checker reports only a count, so a stop during the LAST
        # campaign's batch would otherwise read as a clean success — the flag
        # itself is the source of truth for "the user asked to stop".
        stopped = stopped or (
            self._stop_event is not None and self._stop_event.is_set()
        )
        return {
            "status": "cancelled" if stopped else "success",
            "checked": total_checked,
            "newly_accepted": total_new,
        }

    def render_result(self, result: dict) -> str:
        checked = result.get("checked", 0)
        accepted = result.get("newly_accepted", 0)
        rate = (accepted / checked * 100) if checked else 0.0
        headline = (
            "Connection check stopped at your request — partial results."
            if result.get("status") == "cancelled"
            else "Connection check complete."
        )
        return (
            f"{headline}\n"
            f"Contacts checked: {checked}\n"
            f"Newly accepted: {accepted}\n"
            f"Acceptance rate: {rate:.1f}%"
        )
