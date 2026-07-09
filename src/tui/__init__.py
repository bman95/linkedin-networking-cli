"""Full-screen Textual TUI for the LinkedIn networking CLI.

This is the presentation layer (issue #24), the sole interactive UI since the
issue #47 cutover retired the classic InquirerPy ``linkedin_cli.py``. It
reuses the UI-agnostic business logic under ``src/`` (database, config) as-is.

``LinkedInTUI`` is exposed lazily (PEP 562): importing the package must NOT pull
in ``.app`` eagerly. Both entry points (``linkedin_tui.py`` and ``python -m
tui`` via ``__main__``) initialize logging *before* importing the app modules,
and importing ``.app`` triggers ``get_logger`` at module scope, which would
otherwise run ``LoggerSetup.setup()`` with defaults at package-import time —
defeating that bootstrap order and crashing in a read-only/sandboxed home.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .app import LinkedInTUI

__all__ = ["LinkedInTUI"]


def __getattr__(name: str):
    if name == "LinkedInTUI":
        from .app import LinkedInTUI

        return LinkedInTUI
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
