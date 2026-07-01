"""Allow ``python -m tui`` to launch the Textual app.

Intended for the installed package (the ``linkedin-tui`` console script, or
``uv run python -m tui``), where ``tui`` is importable. Imports resolve via the
installed package (``uv sync`` / the built wheel), so run ``uv sync`` first in a
source checkout.

Mirrors ``linkedin_tui.py``: initialize logging *before* importing the app
modules, so the app's module-scope ``get_logger`` doesn't trigger
``LoggerSetup.setup()`` with defaults at import time.
"""

from utils.logging import LoggerSetup

LoggerSetup.setup()

from .app import run

if __name__ == "__main__":
    run()
