"""Campaign detail screen (issue #24) — the campaign's action hub (issue #42).

Opened by activating a row on the Campaigns list or the Dashboard. Shows one
campaign's full configuration and performance in the left column, and — since
issue #42 folded the standalone Execute/Check screens in here — a focusable
**ACTIONS** list plus the embedded automation run panel in the right column:

- **Run now** executes ``search_and_connect`` for this campaign, streaming the
  log into the right half of the screen.
- **Check acceptances** runs the smart connection checker for this campaign,
  on the same log surface.
- Edit / Activate–Deactivate / Export CSV / Delete are the classic manage
  actions, now visible list items.

Interaction design (owner rule, 2026-07-09; no accelerators, 2026-07-10):
every action is reachable with arrows + Enter alone over the focusable ACTIONS
list — there are no letter-key shortcuts. Confirmations are focused inline
confirms (Enter confirms, esc cancels), not "press the same chord twice"
patterns — including Delete's own confirm, which no longer has an
accelerator-repeat shortcut either.

The read is blocking SQLite, so it runs in the same threaded-worker discipline
as the other data screens; the mutating actions (toggle/delete) likewise run
off the UI thread. The automation bodies (``run_now_body`` / ``check_body``)
are the browser-free test seams, mirroring ``AutomationRunScreen.run_body``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Label, ListItem, ListView, Static

from cli.helpers import acceptance_rate, contacts_csv_filename, write_contacts_csv
from config.settings import AppSettings
from database.operations import DatabaseManager
from utils.logging import get_logger

from .base import BaseScreen
from .run_panel import AutomationRunPanel, ConfirmBar, RunSpec, run_with_linkedin

logger = get_logger(__name__)

# Claude Code's focused-row pointer, matching the home nav idiom.
POINTER = "❯"


def export_contacts_csv(campaign_name: str, contacts) -> Path:
    """Write a campaign's contacts to a timestamped CSV under the exports dir.

    The field list and writing logic are shared with the classic CLI
    (``cli.helpers``); only the destination policy (a fixed exports directory,
    since the TUI has no path prompt) lives here.
    """
    export_dir = Path.home() / ".linkedin-networking-cli" / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / contacts_csv_filename(campaign_name)
    write_contacts_csv(path, contacts)
    return path


def map_connect_results(results: dict) -> dict:
    """Map ``search_and_connect`` results onto the run panel's status contract.

    Mirrors the classic CLI's ``_run_campaign_automation``: a user-requested
    stop (issue #43) is a normal partial completion — checked first so it is
    never dressed up as a CAPTCHA safety stop; a protective stop (inline
    CAPTCHA / challenge wall) must never be reported as a clean run — checked
    before the empty-scan mapping so a first-page CAPTCHA doesn't masquerade as
    "no profiles".
    """
    if results.get("stopped_reason") == "cancelled":
        return {**results, "status": "cancelled"}
    if results.get("stopped_reason"):
        return {**results, "status": "safety_stop"}
    if results.get("scanned", 0) == 0:
        return {"status": "no_profiles"}
    return {**results, "status": "success"}


def render_connect_result(result: dict) -> str:
    """Summary text for a connect run (moved from the old Execute screen)."""
    if result.get("status") == "no_profiles":
        return "No profiles matched the campaign's criteria — nothing sent."
    if result.get("status") == "safety_stop":
        lines = [
            "Automation stopped early: LinkedIn presented a CAPTCHA/challenge.",
            "Resolve it in the browser before running again.",
            f"Invites sent before the stop: {result.get('sent', 0)}",
        ]
        if result.get("possibly_sent", 0):
            lines.append(f"Possibly sent: {result.get('possibly_sent', 0)}")
        return "\n".join(lines)
    headline = (
        "Run stopped at your request — partial results."
        if result.get("status") == "cancelled"
        else "Run complete."
    )
    lines = [
        headline,
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


def map_check_stats(stats: dict) -> dict:
    """Map smart-checker stats onto the run panel's status contract."""
    return {
        "status": "cancelled" if stats.get("stopped") else "success",
        "checked": stats.get("checked", 0),
        "newly_accepted": stats.get("newly_accepted", 0),
    }


def render_check_result(result: dict) -> str:
    """Summary text for a connection check (moved from the old Check screen)."""
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


@dataclass(frozen=True)
class CampaignDetail:
    """Display-ready snapshot of one campaign, computed off the UI thread."""

    id: int
    name: str
    active: bool
    pending: int
    overview: str
    targeting: str
    message: str
    performance: str


# The right-column actions: (item id suffix, title).
ACTIONS: tuple[tuple[str, str], ...] = (
    ("run", "Run now"),
    ("check", "Check acceptances"),
    ("edit", "Edit"),
    ("toggle", "Toggle active"),
    ("export", "Export CSV"),
    ("delete", "Delete"),
)


class CampaignDetailScreen(BaseScreen):
    """Full view of a single campaign, with manage and automation actions."""

    BINDINGS = [
        ("escape", "back", "Back"),
    ]

    SCREEN_TITLE = "Campaign"

    HINTS = (
        ("↑↓", "actions"),
        ("enter", "select"),
        ("esc", "back"),
    )

    def __init__(
        self,
        db_manager: DatabaseManager | None,
        campaign_id: int,
        settings: AppSettings | None = None,
    ) -> None:
        super().__init__()
        self._db_manager = db_manager
        self._campaign_id = campaign_id
        # Automation needs AppSettings; when not injected (the list/dashboard
        # only carry the db manager), it is resolved from the app at action
        # time — see _resolve_settings.
        self._settings = settings
        # Cached snapshot fields so the actions know the current state without
        # re-reading; refreshed on every load.
        self._name: str | None = None
        self._active: bool | None = None
        self._pending: int | None = None
        self._busy = False  # a mutating action is in flight
        self._panel: AutomationRunPanel | None = None
        # Settings snapshot for the automation bodies, captured on the UI
        # thread when a run is requested (the body runs in a worker thread).
        self._automation_settings: AppSettings | None = None

    def compose_body(self) -> ComposeResult:
        with Horizontal(id="detail-columns"):
            with VerticalScroll(id="detail-body"):
                for section_id, title in (
                    ("overview", "Overview"),
                    ("targeting", "Targeting"),
                    ("message", "Message Template"),
                    ("performance", "Performance"),
                ):
                    with Container(classes="settings-section", id=f"detail-section-{section_id}"):
                        yield Static(title, classes="settings-section-title")
                        # markup=False: campaign name / message template are
                        # user-controlled and may contain Rich markup characters.
                        yield Static("", id=f"detail-body-{section_id}", markup=False)
            with Vertical(id="detail-side"):
                yield Static("ACTIONS", classes="eyebrow", id="detail-actions-eyebrow")
                yield ListView(
                    *(
                        ListItem(
                            Horizontal(
                                Label(POINTER, classes="nav-caret"),
                                Label(title, classes="nav-title"),
                                classes="nav-row",
                            ),
                            id=f"action-{key}",
                            classes="action-item",
                        )
                        for key, title in ACTIONS
                    ),
                    id="detail-actions",
                )
                yield ConfirmBar("Delete", id="detail-delete-confirm")
                yield Static("RUN OUTPUT", classes="eyebrow", id="detail-run-eyebrow")
                yield AutomationRunPanel(
                    idle_hint="Run output appears here.", id="detail-run-panel"
                )
        yield Static("Loading campaign…", id="detail-status", classes="status-line")

    def on_mount(self) -> None:
        # Cached on the UI thread: the worker-thread automation bodies must not
        # run a DOM query per progress call.
        self._panel = self.query_one("#detail-run-panel", AutomationRunPanel)
        self.query_one("#detail-actions", ListView).focus()
        self.load_detail()

    @property
    def panel(self) -> AutomationRunPanel:
        assert self._panel is not None  # set in on_mount
        return self._panel

    def on_screen_resume(self) -> None:
        # Returning from the edit screen: re-read so edits are reflected.
        if self.is_mounted:
            self.load_detail()

    # ── load ──────────────────────────────────────────────────────────────

    def load_detail(self) -> None:
        self._run_load(*self.begin_load())

    @work(thread=True, exclusive=True)
    def _run_load(self, app: App, generation: int) -> None:
        if self._db_manager is None:
            self.marshal_load(app, generation, self._populate, None, "Database unavailable.")
            return
        try:
            detail = self._gather()
        except Exception as exc:
            self.marshal_load(
                app, generation, self._populate, None, f"Error loading campaign: {exc}"
            )
            return
        if detail is None:
            self.marshal_load(app, generation, self._populate, None, "Campaign not found.")
            return
        self.marshal_load(app, generation, self._populate, detail, None)

    def _gather(self) -> CampaignDetail | None:
        assert self._db_manager is not None  # guarded in _run_load
        c = self._db_manager.get_campaign(self._campaign_id)
        if c is None:
            return None
        sent, accepted, pending = c.total_sent, c.total_accepted, c.total_pending
        rate = acceptance_rate(sent, accepted)
        # The check gate mirrors the old Check screen's worklist: contacts in
        # the sent / possibly_sent states (not the campaign's pending counter).
        checkable = len(
            self._db_manager.get_contacts_by_status(self._campaign_id, "sent")
        ) + len(
            self._db_manager.get_contacts_by_status(self._campaign_id, "possibly_sent")
        )
        overview = (
            f"Name: {c.name}\n"
            f"Status: {'Active' if c.active else 'Inactive'}\n"
            f"Daily Limit: {c.daily_limit}\n"
            f"Description: {c.description or 'None'}"
        )
        targeting = (
            f"Keywords: {c.keywords or 'Any'}\n"
            f"Location: {c.location_display or 'Any'}\n"
            f"Connection Degree: {c.network_display or 'Any'}\n"
            f"Industry: {c.industry_display or 'Any'}"
        )
        performance = (
            f"Sent: {sent}\n"
            f"Accepted: {accepted}\n"
            f"Pending: {pending}\n"
            f"Acceptance Rate: {rate:.1f}%"
        )
        return CampaignDetail(
            id=self._campaign_id,
            name=c.name,
            active=c.active,
            pending=checkable,
            overview=overview,
            targeting=targeting,
            message=c.message_template or "None",
            performance=performance,
        )

    def _populate(self, detail: CampaignDetail | None, error: str | None) -> None:
        if error is not None:
            self._name = None
            self._active = None
            self._pending = None
            self._set_status(error, "error")
            return
        assert detail is not None
        self._name = detail.name
        self._active = detail.active
        self._pending = detail.pending
        self.query_one("#detail-body-overview", Static).update(detail.overview)
        self.query_one("#detail-body-targeting", Static).update(detail.targeting)
        self.query_one("#detail-body-message", Static).update(detail.message)
        self.query_one("#detail-body-performance", Static).update(detail.performance)
        # The toggle item names the transition, not the flag.
        self.query_one("#action-toggle .nav-title", Label).update(
            "Deactivate" if detail.active else "Activate"
        )
        self._set_status(f"'{detail.name}' — select an action.")

    # ── action dispatch (arrows + Enter over the ACTIONS list) ────────────

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id != "detail-actions":
            return
        key = (event.item.id or "").removeprefix("action-")
        dispatch = {
            "run": self.action_run_now,
            "check": self.action_check,
            "edit": self.action_edit,
            "toggle": self.action_toggle_active,
            "export": self.action_export,
            "delete": self.action_delete,
        }.get(key)
        if dispatch is not None:
            dispatch()

    # ── esc ───────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        """``esc``: cancel an armed confirmation first; only then leave.

        Confirm prompts promise "esc to cancel", so esc while confirming must
        cancel the confirmation — not pop the whole screen. Mid-run the panel
        warns once (leaving does not stop the run) before a second esc leaves.
        """
        if self.query_one("#detail-delete-confirm", ConfirmBar).armed:
            self._cancel_delete_confirm()
            return
        if self.panel.handle_escape():
            return
        self.app.pop_screen()

    # ── automation actions (issue #42) ────────────────────────────────────

    def _resolve_settings(self) -> AppSettings | None:
        return self._settings if self._settings is not None else getattr(
            self.app, "settings", None
        )

    def _cancel_armed_confirms(self) -> None:
        """Disarm every armed confirmation before a different action proceeds.

        An armed confirm captured its gates (e.g. "campaign is active") at
        request time; any mutating or superseding action invalidates them, so
        no armed Enter may survive it.
        """
        self._cancel_delete_confirm()
        self.panel.dismiss_confirm()

    def _automation_ready(self) -> bool:
        """Shared gate for the run/check actions; reports into the panel."""
        self._cancel_delete_confirm()
        if self.panel.run_active or self._busy:
            return False
        if self._db_manager is None or self._resolve_settings() is None:
            self.panel.set_status(
                "Automation unavailable: a database and app settings are required.",
                "error",
            )
            return False
        if self._name is None:
            # Not loaded (yet) — the status line already explains why.
            return False
        return True

    def action_run_now(self) -> None:
        if not self._automation_ready():
            return
        if not self._active:
            self.panel.set_status(
                "Campaign is inactive — activate it (Toggle active) before running.",
                "error",
            )
            return
        self._automation_settings = self._resolve_settings()
        self.panel.request(
            RunSpec(
                action_label="campaign execution",
                confirm=(
                    f"Start automation for '{self._name}'? "
                    "This opens LinkedIn and sends connection requests."
                ),
                body=self.run_now_body,
                render=render_connect_result,
            )
        )

    def action_check(self) -> None:
        if not self._automation_ready():
            return
        if not self._pending:
            self.panel.set_status(
                "No pending invites to check for this campaign.", "warn"
            )
            return
        self._automation_settings = self._resolve_settings()
        self.panel.request(
            RunSpec(
                action_label="connection check",
                confirm=(
                    f"Check connections for '{self._name}' using the smart checker?"
                ),
                body=self.check_body,
                render=render_check_result,
            )
        )

    async def run_now_body(self) -> dict:
        """Execute this campaign (browser). Tests override this seam."""
        return await run_with_linkedin(
            self._db_manager, self._automation_settings, self.panel, self._connect_automate
        )

    async def _connect_automate(self, automation) -> dict:
        assert self._db_manager is not None and self._automation_settings is not None
        campaign = self._db_manager.get_campaign(self._campaign_id)
        if campaign is None:
            raise RuntimeError("Campaign no longer exists.")
        if not campaign.active:
            # Defense in depth behind the action-time gate: the campaign may
            # have been deactivated between arming the confirm and this read.
            raise RuntimeError(
                "Campaign is inactive — activate it before running."
            )
        limit = self._automation_settings.get_automation_settings().get("search_limit", 100)
        self.panel.progress(f"Searching for up to {limit} targeted profiles…")
        results = await automation.search_and_connect(
            campaign,
            limit=limit,
            progress_callback=self.panel.progress,
            stop_event=self.panel.stop_event,
        )
        return map_connect_results(results)

    async def check_body(self) -> dict:
        """Check acceptances for this campaign (browser). Tests override this seam."""
        return await run_with_linkedin(
            self._db_manager, self._automation_settings, self.panel, self._check_automate
        )

    async def _check_automate(self, automation) -> dict:
        stats = await automation.smart_connection_checker(
            self._campaign_id, self.panel.progress, stop_event=self.panel.stop_event
        )
        return map_check_stats(stats)

    def on_automation_run_panel_finished(self, event: AutomationRunPanel.Finished) -> None:
        # A run/check changes the campaign's counters — refresh the left column
        # and hand focus back to the actions list.
        self.load_detail()
        self.query_one("#detail-actions", ListView).focus()

    def on_automation_run_panel_confirm_dismissed(
        self, event: AutomationRunPanel.ConfirmDismissed
    ) -> None:
        # The dismissed bar held focus; hand it back to the actions list so
        # arrows/Enter keep working. This message arrives one pump AFTER the
        # dismissal, so only refocus if focus is still stranded on the hidden
        # bar — an action that dismissed-then-armed the delete confirm in the
        # same tick (action_delete) has already placed focus deliberately, and
        # yanking it to the list would point the promised Enter at "Run now".
        focused = self.focused
        bar = event.panel.query_one("#run-confirm", ConfirmBar)
        if focused is not None and bar not in focused.ancestors_with_self:
            return
        self.query_one("#detail-actions", ListView).focus()

    # ── manage actions ────────────────────────────────────────────────────

    def _blocked_by_active_run(self, what: str) -> bool:
        """Refuse a campaign mutation while its automation run is active.

        The engine works from its own campaign snapshot, so a mid-run edit or
        toggle would not corrupt the run — but it would make the screen lie
        (an "Inactive"/edited campaign shown while invites keep sending).
        Mutations wait for the stop control; the read-only export does not.
        """
        if self.panel.run_active:
            self._set_status(
                f"A run is in progress — stop it before {what}.", "warn"
            )
            return True
        return False

    def action_edit(self) -> None:
        self._cancel_armed_confirms()
        if self._db_manager is None or self._busy:
            return
        if self._blocked_by_active_run("editing the campaign"):
            return
        from .campaign_edit import CampaignEditScreen

        self.app.push_screen(CampaignEditScreen(self._db_manager, self._campaign_id))

    def action_toggle_active(self) -> None:
        self._cancel_armed_confirms()
        if self._db_manager is None or self._busy or self._active is None:
            return
        if self._blocked_by_active_run("changing its active state"):
            return
        self._busy = True
        new_state = not self._active
        self._set_status("Activating…" if new_state else "Deactivating…")
        self._run_update(self.app, {"active": new_state})

    def action_export(self) -> None:
        self._cancel_armed_confirms()
        if self._db_manager is None or self._busy:
            return
        self._busy = True
        self._set_status("Exporting contacts…")
        self._run_export(self.app)

    def action_delete(self) -> None:
        # An armed run confirm must not coexist with the delete confirm (two
        # live "Enter to confirm" prompts); the delete bar arms below.
        self.panel.dismiss_confirm()
        if self._db_manager is None or self._busy:
            return
        if self._blocked_by_active_run("deleting the campaign"):
            return
        bar = self.query_one("#detail-delete-confirm", ConfirmBar)
        if bar.armed:
            return  # already armed; the focused confirm button is the only path
        # A destructive action never fires on a single activation: arm the
        # focused inline confirm (Enter confirms, esc cancels).
        self._set_status(
            "Delete this campaign and all its contacts? "
            "Enter to confirm, esc to cancel.",
            "warn",
        )
        bar.arm()

    def on_confirm_bar_confirmed(self, event: ConfirmBar.Confirmed) -> None:
        # The run panel's own bar never bubbles this far; only delete's does.
        if event.bar.id == "detail-delete-confirm":
            event.stop()
            self._do_delete()

    def on_confirm_bar_cancelled(self, event: ConfirmBar.Cancelled) -> None:
        if event.bar.id == "detail-delete-confirm":
            event.stop()
            self._set_status("Delete cancelled.")
            self.query_one("#detail-actions", ListView).focus()

    def _cancel_delete_confirm(self) -> None:
        bar = self.query_one("#detail-delete-confirm", ConfirmBar)
        if bar.armed:
            bar.disarm()
            # Retire the delete prompt: its "Enter to confirm" promise is dead,
            # and a superseding action may be arming the run confirm — two live
            # contradictory prompts must never share the screen.
            self._set_status("Delete cancelled.")
            # The bar held focus while armed; hand it back so arrows/Enter
            # keep working (a hidden widget does not release focus itself).
            self.query_one("#detail-actions", ListView).focus()

    def _do_delete(self) -> None:
        if self._db_manager is None or self._busy:
            return
        self._busy = True
        self._set_status("Deleting…", "warn")
        self._run_delete(self.app)

    # Own worker group: `exclusive=True` in the *default* group would let a
    # reload scheduled in the same tick (screen resume, run-finished refresh)
    # cancel a queued mutation before it ever ran — silently losing the write
    # and leaving `_busy` stuck True (its marshal would never fire).
    @work(thread=True, exclusive=True, group="mutate")
    def _run_update(self, app: App, updates: dict) -> None:
        assert self._db_manager is not None  # guarded in action_toggle_active
        try:
            self._db_manager.update_campaign(self._campaign_id, updates)
        except Exception as exc:
            self.marshal(app, self._after_action, f"Error updating campaign: {exc}", False)
            return
        self.marshal(app, self._after_action, None, True)

    @work(thread=True, exclusive=True, group="export")
    def _run_export(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_export
        try:
            campaign = self._db_manager.get_campaign(self._campaign_id)
            contacts = self._db_manager.get_contacts(self._campaign_id)
        except Exception as exc:
            self.marshal(app, self._after_export, f"Error loading contacts: {exc}", "error")
            return
        if campaign is None:
            self.marshal(app, self._after_export, "Campaign not found.", "error")
            return
        if not contacts:
            self.marshal(app, self._after_export, "No contacts to export yet.", "warn")
            return
        try:
            path = export_contacts_csv(campaign.name, contacts)
        except Exception as exc:
            self.marshal(app, self._after_export, f"Error writing CSV: {exc}", "error")
            return
        self.marshal(
            app, self._after_export, f"✓ Exported {len(contacts)} contacts to {path}", "good"
        )

    def _after_export(self, message: str, kind: str) -> None:
        self._busy = False
        self._set_status(message, kind)

    @work(thread=True, exclusive=True, group="mutate")
    def _run_delete(self, app: App) -> None:
        assert self._db_manager is not None  # guarded in action_delete
        try:
            ok = self._db_manager.delete_campaign(self._campaign_id)
        except Exception as exc:
            self.marshal(app, self._after_action, f"Error deleting campaign: {exc}", False)
            return
        self.marshal(app, self._after_action, "__deleted__" if ok else "Campaign not found.", False)

    def _after_action(self, message: str | None, reload: bool) -> None:
        self._busy = False
        if message == "__deleted__":
            # Pop back to the (refreshed) campaigns list.
            self.app.pop_screen()
            return
        if message is not None:
            self._set_status(message, "error")
            # A failed delete arrived with focus on the (now hidden) confirm
            # bar's button; hand it back so the keyboard keeps working.
            self.query_one("#detail-actions", ListView).focus()
            return
        if reload:
            self.load_detail()

    # ── helpers ───────────────────────────────────────────────────────────

    def _set_status(self, message: str, kind: str = "") -> None:
        status = self.query_one("#detail-status", Static)
        status.set_classes(f"status-line {('-' + kind) if kind else ''}".strip())
        # Text() renders literally: messages carry campaign names and raw
        # exception text, whose brackets must not be parsed as markup.
        status.update(Text(message))
