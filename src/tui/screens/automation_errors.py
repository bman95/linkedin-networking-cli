"""Typed-error → friendly message mapping for the automation screens (#24).

The mapping itself is UI-agnostic and shared with the classic CLI; it lives in
``cli.automation_errors``. This module re-exports it so existing TUI importers
(``automation_run`` and the tests) keep working. The TUI renders the returned
strings in a ``markup=False`` log, so no Rich escaping is needed.
"""

from __future__ import annotations

from cli.automation_errors import describe_automation_error, evidence_reference

__all__ = ["describe_automation_error", "evidence_reference"]
