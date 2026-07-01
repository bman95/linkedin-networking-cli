#!/usr/bin/env python3
"""LinkedIn Networking CLI - full-screen Textual TUI entry point.

New presentation layer (issue #24), coexisting with the classic InquirerPy
``linkedin_cli.py``. Mirrors that module's bootstrap: logging is initialized
before importing app modules. Imports resolve via the installed package
(``uv sync`` / the built wheel), so no manual ``sys.path`` juggling is needed.
"""

# Initialize logging system first
from utils.logging import LoggerSetup

LoggerSetup.setup()

from tui.app import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
