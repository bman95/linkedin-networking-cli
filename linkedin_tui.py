#!/usr/bin/env python3
"""LinkedIn Networking CLI - full-screen Textual TUI entry point.

New presentation layer (issue #24), coexisting with the classic InquirerPy
``linkedin_cli.py``. Mirrors that module's bootstrap: ``src/`` is added to the
import path and logging is initialized before importing app modules.
"""

from pathlib import Path
import sys

# Add src directory to path for imports (same convention as linkedin_cli.py)
sys.path.append(str(Path(__file__).parent / "src"))

# Initialize logging system first
from utils.logging import LoggerSetup

LoggerSetup.setup()

from tui.app import run


def main() -> None:
    run()


if __name__ == "__main__":
    main()
