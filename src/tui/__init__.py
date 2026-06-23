"""Full-screen Textual TUI for the LinkedIn networking CLI.

This is the new presentation layer (issue #24), built as a vertical slice that
coexists with the existing InquirerPy ``linkedin_cli.py``. It reuses the
UI-agnostic business logic under ``src/`` (database, config) as-is.
"""

from .app import LinkedInTUI

__all__ = ["LinkedInTUI"]
