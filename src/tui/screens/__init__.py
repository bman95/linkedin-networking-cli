"""Screen modules for the Textual TUI (issue #24).

Intentionally empty: importing this package must NOT eagerly import any screen
module. Screen modules call ``get_logger(__name__)`` at module scope, which (the
first time) runs ``LoggerSetup.setup()`` with production defaults — exactly the
import-time side effect the entry points work to avoid (see ``tui.__init__``).
Screens are imported lazily by ``tui.app`` (which is itself only loaded after
logging is bootstrapped). Keep this file free of imports.
"""
