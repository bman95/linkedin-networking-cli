""""Describe your campaign in plain language" — the AI Assist panel.

A collapsible panel embedded at the top of :class:`CreateCampaignScreen`
(only — the Edit screen is untouched). The user types a free-text
description; a local (Ollama) or hosted (OpenAI-compatible) model parses it
into the campaign's fields, which then **prefill the same form widgets**
``campaign_form_widgets()`` already defines via
:func:`campaign_form.fill_form_from_extraction` — the AI pipeline never
writes to the database directly, and ``read_form``'s validation is never
bypassed.

Reuses :class:`ConfirmBar` and the threaded-worker race discipline
(:class:`WorkerGuardMixin`) from ``run_panel.py``, but not
``AutomationRunPanel`` itself — that class is coupled to automation-shaped
results and LinkedIn-specific error mapping, and has no concept of the
missing-model pull sub-flow this panel needs.
"""

from __future__ import annotations

import threading
from typing import Any

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, ProgressBar, RichLog, Select, Static, TextArea

from config.settings import DEFAULT_LLM_SETTINGS
from llm_assist import (
    RECOMMENDED_MODELS,
    ExtractionResult,
    LLMAssistCancelled,
    LLMAssistError,
    LLMAuthError,
    LLMClient,
    LLMConfig,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    ModelPullError,
    extract_campaign_fields,
    recommend_model,
)
from utils.logging import get_logger

from .base import render_status_line
from .run_panel import ConfirmBar
from .workers import WorkerGuardMixin

logger = get_logger(__name__)

_MIN_DESCRIPTION_CHARS = 8
_CUSTOM_MODEL = "Custom…"


def describe_llm_error(exc: Exception) -> str:
    """One friendly, specific line per failure mode — never a raw traceback."""
    if isinstance(exc, LLMUnavailableError):
        return f"Can't reach the model endpoint — is it running? ({exc})"
    if isinstance(exc, LLMTimeoutError):
        return f"The request timed out. ({exc})"
    if isinstance(exc, LLMAuthError):
        return f"The endpoint rejected the request — check your API key. ({exc})"
    if isinstance(exc, (ModelNotFoundError, LLMResponseError, ModelPullError)):
        return str(exc)
    if isinstance(exc, LLMAssistError):
        return str(exc)
    return f"Unexpected error: {exc}"


class CampaignAIAssistPanel(WorkerGuardMixin, Vertical):
    """Collapsible "describe your campaign" assistant, embedded in Create."""

    class Extracted(Message):
        """A description was successfully parsed; the host should prefill the form."""

        def __init__(self, panel: CampaignAIAssistPanel, result: ExtractionResult) -> None:
            super().__init__()
            self.panel = panel
            self.result = result

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._db_manager = None
        self._llm_settings: dict[str, Any] = dict(DEFAULT_LLM_SETTINGS)
        self._expanded = False
        self._busy = False
        self._pulling = False
        self._leave_confirming = False
        self._pending_description: str | None = None
        self._pending_model: str | None = None
        self._stop_event: threading.Event | None = None
        # Verified (base_url, model) -> availability results, so repeated
        # "Fill from description" presses don't re-hit /api/tags every time
        # (issue #65). Keyed by model too, so switching models never serves a
        # stale result; refreshed on a successful pull (see _pull_done).
        self._model_availability_cache: dict[tuple[str, str], bool] = {}
        # Positive-only cache of the hosted-consent setting, so repeat runs
        # skip the synchronous SQLite read in action_run (issue #65). Only a
        # confirmed True is ever cached — absence/False always re-reads the
        # DB, so the fail-closed gate (issue #63) is byte-identical.
        self._hosted_consent_ack = False

    # ── compose ───────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Button(
            "Describe your campaign in plain language",
            id="ai-assist-toggle",
            classes="flat-button",
        )
        with Vertical(id="ai-assist-body"):
            yield Static("", id="ai-assist-provider", markup=False)
            yield Label("Model", classes="field-label", id="ai-assist-model-label")
            yield Select(
                [(m, m) for m in RECOMMENDED_MODELS] + [(_CUSTOM_MODEL, _CUSTOM_MODEL)],
                id="ai-assist-model-select",
                allow_blank=False,
            )
            yield Input(placeholder="e.g. gemma3:8b", id="ai-assist-model-custom")
            yield Label("Describe who you want to connect with", classes="field-label")
            yield TextArea(id="ai-assist-input", placeholder=(
                "e.g. Software engineers in Mexico City, 15 a day, friendly tone"
            ))
            yield Static("", id="ai-assist-counter", classes="field-label")
            with Horizontal(id="ai-assist-controls"):
                yield Button("Fill from description", id="ai-assist-run", classes="flat-button")
                yield Button("Cancel", id="ai-assist-stop", classes="flat-button")
            yield RichLog(id="ai-assist-log", highlight=False, markup=False, wrap=True)
            yield ProgressBar(id="ai-assist-progress", show_eta=False)
            yield Static("", id="ai-assist-status", classes="status-line")
            with Vertical(id="ai-assist-missing-model"):
                yield Static("", id="missing-model-status", markup=False)
                with Horizontal(id="missing-model-controls"):
                    yield Button(
                        "Pull it for me", id="missing-model-pull-btn", classes="flat-button"
                    )
                    yield Button(
                        "Show me the command instead",
                        id="missing-model-manual-btn",
                        classes="flat-button",
                    )
                yield ConfirmBar("Pull now", id="ai-assist-pull-confirm")
                yield Static("", id="missing-model-command", markup=False)
            with Vertical(id="ai-assist-privacy-notice"):
                yield Static(
                    "Your description (and any targeting details in it) will be "
                    "sent to the configured hosted endpoint. This only happens "
                    "once you confirm below.",
                    id="privacy-notice-text",
                )
                yield ConfirmBar("I understand, continue", id="ai-assist-privacy-confirm")

    def on_mount(self) -> None:
        self._db_manager = getattr(self.app, "db_manager", None)
        settings = getattr(self.app, "settings", None)
        self._llm_settings = (
            settings.get_llm_settings() if settings else dict(DEFAULT_LLM_SETTINGS)
        )

        self.query_one("#ai-assist-body").display = False
        self.query_one("#ai-assist-model-custom", Input).display = False
        self.query_one("#ai-assist-log", RichLog).display = False
        self.query_one("#ai-assist-progress", ProgressBar).display = False
        self.query_one("#ai-assist-stop", Button).display = False
        self.query_one("#ai-assist-missing-model").display = False
        self.query_one("#missing-model-command", Static).display = False
        self.query_one("#ai-assist-privacy-notice").display = False

        cap = self._llm_settings.get(
            "max_input_chars", DEFAULT_LLM_SETTINGS["max_input_chars"]
        )
        self.query_one("#ai-assist-counter", Static).update(f"0 / {cap} characters")

        if self._llm_settings.get("mode") == "local":
            default_model = self._llm_settings.get("model") or recommend_model()
            options = [(m, m) for m in RECOMMENDED_MODELS]
            if default_model not in RECOMMENDED_MODELS:
                options.append((default_model, default_model))
            options.append((_CUSTOM_MODEL, _CUSTOM_MODEL))
            select = self.query_one("#ai-assist-model-select", Select)
            select.set_options(options)
            select.value = default_model
            self.query_one("#ai-assist-provider", Static).update(
                f"Provider: Local (Ollama) at {self._llm_settings['base_url']}"
            )
        else:
            self.query_one("#ai-assist-model-label", Label).display = False
            self.query_one("#ai-assist-model-select", Select).display = False
            model = self._llm_settings.get("model") or "(not set)"
            self.query_one("#ai-assist-provider", Static).update(
                f"Provider: Hosted at {self._llm_settings['base_url']} · model: {model}"
            )

    # ── seams (overridden in tests to avoid a real LLM/network) ─────────────

    def perform_extraction(
        self,
        description: str,
        llm_settings: dict[str, Any],
        model: str,
        progress,
        should_stop,
    ) -> ExtractionResult:
        client = self._build_client(llm_settings, model)
        return extract_campaign_fields(
            description,
            client,
            max_input_chars=llm_settings.get(
                "max_input_chars", DEFAULT_LLM_SETTINGS["max_input_chars"]
            ),
            progress=progress,
            should_stop=should_stop,
        )

    def check_model_available(self, llm_settings: dict[str, Any], model: str) -> bool:
        if llm_settings.get("mode") != "local":
            return True
        base_url = llm_settings.get("base_url", DEFAULT_LLM_SETTINGS["base_url"])
        cache_key = (base_url, model)
        if self._model_availability_cache.get(cache_key):
            return True
        client = self._build_client(llm_settings, model)
        available = client.is_model_available(model)
        if available:
            # Positive-only cache: a "not found" is never remembered, so a
            # model pulled OUTSIDE this panel (the "Show me the command
            # instead" path, or any external `ollama pull`) is picked up by
            # the next run's fresh probe instead of dead-ending on a stale
            # False for the panel's lifetime.
            self._model_availability_cache[cache_key] = True
        return available

    def perform_pull(
        self, llm_settings: dict[str, Any], model: str, on_progress, should_stop
    ) -> None:
        client = self._build_client(llm_settings, model)
        client.pull_model(model, on_progress, should_stop)

    @staticmethod
    def _build_client(llm_settings: dict[str, Any], model: str) -> LLMClient:
        return LLMClient(
            LLMConfig(
                base_url=llm_settings["base_url"],
                api_key=llm_settings.get("api_key"),
                model=model,
                timeout_s=llm_settings.get("timeout_s", DEFAULT_LLM_SETTINGS["timeout_s"]),
                pull_timeout_s=llm_settings.get(
                    "pull_timeout_s", DEFAULT_LLM_SETTINGS["pull_timeout_s"]
                ),
                max_tokens=llm_settings.get("max_tokens", DEFAULT_LLM_SETTINGS["max_tokens"]),
            )
        )

    # ── toggle / input events ────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "ai-assist-toggle":
            event.stop()
            self._toggle()
        elif bid == "ai-assist-run":
            event.stop()
            self.action_run()
        elif bid == "ai-assist-stop":
            event.stop()
            self.request_stop()
        elif bid == "missing-model-pull-btn":
            event.stop()
            if not self._busy:
                self.query_one("#ai-assist-pull-confirm", ConfirmBar).arm()
        elif bid == "missing-model-manual-btn":
            event.stop()
            self._show_manual_command()

    def collapse(self) -> None:
        """Fold the panel back to its toggle button (the description is kept).

        Called by the host screen once an extraction has been applied, so the
        freshly filled form — not the panel — is what the user is looking at;
        re-expanding via the toggle (description intact) allows a re-run.
        """
        self._expanded = False
        self.query_one("#ai-assist-body").display = False

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.query_one("#ai-assist-body").display = self._expanded
        if self._expanded:
            # The model select is the first interactive control in local mode
            # (Tab order otherwise skipped straight to the TextArea below it);
            # in hosted mode it's hidden, so the TextArea is first instead.
            model_select = self.query_one("#ai-assist-model-select", Select)
            if model_select.display:
                model_select.focus()
            else:
                self.query_one("#ai-assist-input", TextArea).focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "ai-assist-input":
            return
        cap = self._llm_settings.get(
            "max_input_chars", DEFAULT_LLM_SETTINGS["max_input_chars"]
        )
        self.query_one("#ai-assist-counter", Static).update(
            f"{len(event.text_area.text)} / {cap} characters"
        )

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "ai-assist-model-select":
            return
        custom = self.query_one("#ai-assist-model-custom", Input)
        custom.display = event.value == _CUSTOM_MODEL
        if custom.display:
            custom.focus()

    # ── run ───────────────────────────────────────────────────────────────

    def action_run(self) -> None:
        if self._busy:
            return
        text = self.query_one("#ai-assist-input", TextArea).text.strip()
        if len(text) < _MIN_DESCRIPTION_CHARS:
            self._set_status("Describe your campaign in a bit more detail.", "error")
            return
        self._dismiss_missing_model()

        mode = self._llm_settings.get("mode", "local")
        if mode == "hosted":
            if not self._llm_settings.get("api_key"):
                self._set_status(
                    "Hosted mode needs an API key — set LLM_API_KEY, or unset it "
                    "to use local Ollama instead.",
                    "error",
                )
                return
            if not self._llm_settings.get("model"):
                self._set_status("Hosted mode needs LLM_MODEL set.", "error")
                return
            if not self._consent_acknowledged():
                self._pending_description = text
                self.query_one("#ai-assist-privacy-notice").display = True
                self.query_one("#ai-assist-privacy-confirm", ConfirmBar).arm()
                self._set_status("Hosted AI assist needs your OK first.", "warn")
                return

        self._start_extraction(text)

    def _consent_acknowledged(self) -> bool:
        """Whether the hosted-consent gate is satisfied — fail-closed.

        No ``db_manager`` or no persisted ack means NOT consented (issue #63).
        A confirmed True is cached on the instance so repeat runs skip the
        synchronous SQLite read (issue #65); False is never cached, so the
        gate re-reads the DB until consent actually exists. Caveat: the True
        is sticky for this panel's lifetime — if an in-app "revoke consent"
        path is ever added, it must also clear ``_hosted_consent_ack`` (or
        this caching must go back to reading the DB every run).
        """
        if self._hosted_consent_ack:
            return True
        if self._db_manager is None:
            return False
        if self._db_manager.get_setting("llm_hosted_consent_ack", False):
            self._hosted_consent_ack = True
            return True
        return False

    def _selected_model(self) -> str:
        if self._llm_settings.get("mode") != "local":
            return self._llm_settings.get("model") or ""
        select = self.query_one("#ai-assist-model-select", Select)
        if select.value == _CUSTOM_MODEL:
            return self.query_one("#ai-assist-model-custom", Input).value.strip()
        return str(select.value) if select.value else ""

    def _start_extraction(self, text: str) -> None:
        model = self._selected_model()
        if self._llm_settings.get("mode") == "local" and not model:
            self._set_status("Choose or enter a model first.", "error")
            return
        self._busy = True
        self._begin_run_ui()
        self._set_status("Checking the model is available…")
        self._stop_event = threading.Event()
        self._run_extraction_worker(self.app, text, model)

    @work(thread=True, exclusive=True, group="ai-assist")
    def _run_extraction_worker(self, app: App, text: str, model: str) -> None:
        stop_event = self._stop_event
        should_stop = (lambda: stop_event.is_set()) if stop_event else (lambda: False)
        try:
            available = self.check_model_available(self._llm_settings, model)
        except Exception as exc:
            self.marshal(app, self._extract_done, None, exc)
            return
        if not available:
            self.marshal(app, self._model_missing, model)
            return
        try:
            result = self.perform_extraction(
                text,
                self._llm_settings,
                model,
                progress=lambda msg: self.marshal(app, self._append_log, msg),
                should_stop=should_stop,
            )
        except Exception as exc:
            self.marshal(app, self._extract_done, None, exc)
            return
        self.marshal(app, self._extract_done, result, None)

    def _model_missing(self, model: str) -> None:
        self._busy = False
        self._end_run_ui()
        self._pending_model = model
        self.query_one("#missing-model-status", Static).update(
            f"Model '{model}' isn't downloaded yet."
        )
        self.query_one("#ai-assist-missing-model").display = True
        self._set_status("Model not found locally.", "warn")
        self.query_one("#missing-model-pull-btn", Button).focus()

    def _dismiss_missing_model(self) -> None:
        self.query_one("#ai-assist-missing-model").display = False
        self.query_one("#missing-model-command", Static).display = False
        confirm = self.query_one("#ai-assist-pull-confirm", ConfirmBar)
        if confirm.armed:
            confirm.disarm()

    def _show_manual_command(self) -> None:
        command = self.query_one("#missing-model-command", Static)
        command.update(Text(f"ollama pull {self._pending_model or ''}"))
        command.display = True

    def _extract_done(self, result: ExtractionResult | None, exc: Exception | None) -> None:
        self._busy = False
        self._end_run_ui()
        if exc is not None:
            if isinstance(exc, LLMAssistCancelled):
                self._set_status("Cancelled.")
            else:
                self._set_status(describe_llm_error(exc), "error")
            return
        assert result is not None
        self._set_status("Done.", "good")
        self.post_message(self.Extracted(self, result))

    # ── model pull ───────────────────────────────────────────────────────

    def on_confirm_bar_confirmed(self, event: ConfirmBar.Confirmed) -> None:
        event.stop()
        if event.bar.id == "ai-assist-pull-confirm":
            self._start_pull()
        elif event.bar.id == "ai-assist-privacy-confirm":
            self._confirm_privacy_and_run()

    def on_confirm_bar_cancelled(self, event: ConfirmBar.Cancelled) -> None:
        event.stop()
        if event.bar.id == "ai-assist-pull-confirm":
            self._set_status("Pull cancelled.")
        elif event.bar.id == "ai-assist-privacy-confirm":
            self.query_one("#ai-assist-privacy-notice").display = False
            self._pending_description = None
            self._set_status("Hosted AI assist needs your OK first — cancelled.", "warn")

    def _confirm_privacy_and_run(self) -> None:
        if self._db_manager is not None:
            self._db_manager.set_setting("llm_hosted_consent_ack", True)
            self._hosted_consent_ack = True  # mirror the persisted ack
        self.query_one("#ai-assist-privacy-notice").display = False
        text, self._pending_description = self._pending_description, None
        if text:
            self._start_extraction(text)

    def _start_pull(self) -> None:
        model = self._pending_model
        if not model or self._busy:
            return
        self._busy = True
        self._pulling = True
        self._begin_run_ui()
        progress = self.query_one("#ai-assist-progress", ProgressBar)
        progress.display = True
        progress.update(total=100, progress=0)
        self._set_status(f"Pulling {model}…")
        self._stop_event = threading.Event()
        self._run_pull_worker(self.app, model)

    @work(thread=True, exclusive=True, group="ai-assist")
    def _run_pull_worker(self, app: App, model: str) -> None:
        stop_event = self._stop_event
        should_stop = (lambda: stop_event.is_set()) if stop_event else (lambda: False)
        try:
            self.perform_pull(
                self._llm_settings,
                model,
                on_progress=lambda event: self.marshal(app, self._pull_progress, event),
                should_stop=should_stop,
            )
        except Exception as exc:
            self.marshal(app, self._pull_done, model, exc, False)
            return
        self.marshal(app, self._pull_done, model, None, should_stop())

    def _pull_progress(self, event: dict[str, Any]) -> None:
        completed, total = event.get("completed"), event.get("total")
        if isinstance(completed, int | float) and isinstance(total, int | float) and total > 0:
            self.query_one("#ai-assist-progress", ProgressBar).update(
                total=100, progress=completed / total * 100
            )
        self._append_log(str(event.get("status") or "…"))

    def _pull_done(self, model: str, exc: Exception | None, cancelled: bool) -> None:
        self._busy = False
        self._pulling = False
        self._end_run_ui()
        self.query_one("#ai-assist-progress", ProgressBar).display = False
        if exc is not None:
            self._set_status(describe_llm_error(exc), "error")
            return  # missing-model panel stays offered, both options intact
        if cancelled:
            self._set_status("Pull cancelled — no model was installed.")
            return
        # The model is now verified available; refresh the cache entry so a
        # later run doesn't serve the stale pre-pull "not found" result.
        base_url = self._llm_settings.get("base_url", DEFAULT_LLM_SETTINGS["base_url"])
        self._model_availability_cache[(base_url, model)] = True
        self._dismiss_missing_model()
        self._set_status(f"'{model}' downloaded — filling in the form…")
        text = self.query_one("#ai-assist-input", TextArea).text.strip()
        if text:
            self._start_extraction(text)

    # ── stop / escape ────────────────────────────────────────────────────

    def request_stop(self) -> None:
        if not self._busy or self._stop_event is None:
            return
        self._stop_event.set()
        self._append_log("Stop requested…")
        if self._pulling:
            self._set_status("Stopping the pull…", "warn")
        else:
            self._set_status("Cancelling — waiting for the model to respond…", "warn")

    def handle_escape(self) -> bool:
        """``esc``, as the host screen delegates it before its own guard."""
        pull_confirm = self.query_one("#ai-assist-pull-confirm", ConfirmBar)
        if pull_confirm.armed:
            pull_confirm.disarm()
            self._set_status("Pull cancelled.")
            return True
        privacy_confirm = self.query_one("#ai-assist-privacy-confirm", ConfirmBar)
        if privacy_confirm.armed:
            privacy_confirm.disarm()
            self.query_one("#ai-assist-privacy-notice").display = False
            self._pending_description = None
            self._set_status("Hosted AI assist needs your OK first — cancelled.", "warn")
            return True
        if self._busy and not self._leave_confirming:
            self._leave_confirming = True
            self._set_status(
                "AI request in progress — leaving won't cancel it; use Cancel. "
                "Press esc again to leave anyway.",
                "warn",
            )
            return True
        return False

    def lock_panel(self) -> None:
        """Disable every control once the host campaign was created.

        Named ``lock_panel`` (not ``lock``): Textual's own ``Widget`` already
        owns a ``.lock`` instance attribute (an internal ``RLock``), which
        would silently shadow a same-named method here.

        Called by the host screen's ``_done()`` alongside locking the form
        fields: a late extraction landing after a successful create must not
        write into the locked form (the host also guards its own handler),
        and disabling the panel's own controls additionally blocks a new run
        from ever starting.
        """
        for widget in self.query("Button, Input, Select, TextArea"):
            widget.disabled = True

    # ── helpers ───────────────────────────────────────────────────────────

    def _begin_run_ui(self) -> None:
        self._leave_confirming = False
        log = self.query_one("#ai-assist-log", RichLog)
        log.display = True
        log.clear()
        self.query_one("#ai-assist-run", Button).disabled = True
        stop = self.query_one("#ai-assist-stop", Button)
        stop.display = True
        stop.focus()

    def _end_run_ui(self) -> None:
        self._leave_confirming = False
        self.query_one("#ai-assist-run", Button).disabled = False
        self.query_one("#ai-assist-stop", Button).display = False

    def _append_log(self, message: str) -> None:
        self.query_one("#ai-assist-log", RichLog).write(message)

    def _set_status(self, message: str, kind: str = "") -> None:
        render_status_line(self.query_one("#ai-assist-status", Static), message, kind)
