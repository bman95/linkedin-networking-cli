"""CLI support package.

Houses pure, importable helpers (formatting, field access, CSV normalization,
typed-error mapping) and the non-interactive campaign runner (``runner.py``),
shared by the TUI (``src/tui/``) and the ``linkedin-run`` entry point. Originally
extracted from the classic InquirerPy ``linkedin_cli.py`` monolith, retired in
the issue #47 single-UI cutover.
"""
