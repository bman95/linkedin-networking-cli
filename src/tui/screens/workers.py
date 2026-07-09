"""Threaded-worker race discipline, shared by every TUI screen (issue #24).

``DatabaseManager`` reads, ``AppSettings()`` construction and the automation
flows are all blocking, so screens run them in ``@work(thread=True)`` workers.
A thread worker cannot be interrupted mid-call, which creates three races every
screen must guard against (docs/tui-migration.md §6):

1. **Deferred ``self.app`` resolution.** ``@work(thread=True)`` defers the
   worker body to a worker thread; resolving ``self.app`` there would raise if
   the user popped/quit the screen first. The app must be captured on the UI
   thread at schedule time and handed to the worker as an argument.
2. **Late callbacks after quit/unmount.** A worker may finish after the app
   exited (``call_from_thread`` raises ``RuntimeError`` once the loop is torn
   down) or after the screen was popped (its widgets gone). Both must be
   silent no-ops, or the worker thread errors and can hang shutdown.
3. **Stale results.** A superseded (slower, older) load must not overwrite the
   widgets with an outdated snapshot. A monotonic generation token identifies
   the most recent load; results are applied only while their token matches.

``WorkerGuardMixin`` centralizes all three. The worker bodies themselves stay
on the screens (they differ per screen); the mixin owns the schedule-time
capture (:meth:`begin_load`), the guarded hand-back (:meth:`marshal`), and the
generation check (:meth:`marshal_load`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.app import App


class WorkerGuardMixin:
    """Guards for handing threaded-worker results back to the UI thread.

    Mixed into a Textual ``Screen`` (relies on ``self.app`` / ``self.is_mounted``).
    """

    # Provided by the Screen this is mixed into; declared for type-checkers.
    app: App
    is_mounted: bool

    # Monotonic token identifying the most recent load; bumped by begin_load.
    _load_generation: int = 0

    def begin_load(self) -> tuple[App, int]:
        """Start a fresh load: bump the generation and capture the app.

        Call on the UI thread at schedule time and pass the result straight to
        the worker (``self._run_load(*self.begin_load())``), so the worker never
        resolves ``self.app`` itself and stale in-flight loads are invalidated.
        """
        self._load_generation += 1
        return self.app, self._load_generation

    def marshal(self, app: App | None, callback: Callable[..., None], *args: Any) -> None:
        """From a worker thread: run ``callback(*args)`` on the UI thread.

        A silent no-op once the app has stopped or the screen was unmounted, so
        a worker that outlives the screen exits cleanly instead of erroring.
        """
        if app is None or not app.is_running:
            return
        try:
            app.call_from_thread(self._apply_if_mounted, callback, *args)
        except RuntimeError:
            # App stopped between the is_running check and the call; ignore.
            return

    def marshal_load(
        self, app: App | None, generation: int, callback: Callable[..., None], *args: Any
    ) -> None:
        """Like :meth:`marshal`, but also drop results from a superseded load."""
        self.marshal(app, self._apply_if_current, generation, callback, *args)

    def _apply_if_mounted(self, callback: Callable[..., None], *args: Any) -> None:
        if not self.is_mounted:
            return
        callback(*args)

    def _apply_if_current(
        self, generation: int, callback: Callable[..., None], *args: Any
    ) -> None:
        if generation != self._load_generation:
            return
        callback(*args)
