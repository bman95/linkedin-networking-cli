#!/usr/bin/env python3
"""LinkedIn Networking CLI - full-screen Textual TUI entry point.

The sole interactive UI (issue #24; the classic InquirerPy ``linkedin_cli.py``
was retired in the issue #47 cutover). Logging is initialized before importing
app modules. Imports resolve via the installed package (``uv sync`` / the built
wheel), so no manual ``sys.path`` juggling is needed.
"""

# Initialize logging system first
from utils.logging import LoggerSetup

LoggerSetup.setup()

from tui.app import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
