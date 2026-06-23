"""Allow ``python -m tui`` to launch the Textual app.

Intended for the installed package (the ``linkedin-tui`` console script, or
``uv run python -m tui``), where ``tui`` is importable. From a bare interpreter
in a source checkout the ``tui`` package is under ``src/`` and isn't on
``sys.path``, so ``python -m tui`` can't even locate this module (a ``-m``
launch resolves the package *before* running this file, so it can't bootstrap
its own path) — use ``python linkedin_tui.py`` for that no-install case.

Mirrors ``linkedin_tui.py``: initialize logging *before* importing the app
modules, so the app's module-scope ``get_logger`` doesn't trigger
``LoggerSetup.setup()`` with defaults at import time.
"""

from utils.logging import LoggerSetup

LoggerSetup.setup()

from .app import run

if __name__ == "__main__":
    run()
