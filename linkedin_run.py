#!/usr/bin/env python3
"""LinkedIn Networking CLI - non-interactive campaign runner.

Headless entry point for scheduled execution (cron / systemd-timer): runs one
campaign's search-and-connect pass without any prompts, then exits with a
process exit code a scheduler can alert on. All rate-limit, daily-cap and
session logic is respected — see :class:`cli.runner.CampaignRunner`.

Mirrors ``linkedin_tui.py``'s bootstrap: logging is initialized before
importing app modules.
"""

import argparse
import sys

# Initialize logging system first
from utils.logging import LoggerSetup, get_logger

LoggerSetup.setup()

from cli.runner import CampaignRunner

logger = get_logger(__name__)


def _positive_int(value):
    """argparse type for ``--max``: an integer >= 1."""
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def _build_parser():
    """Build the argument parser for the headless run entry point."""
    parser = argparse.ArgumentParser(
        prog="linkedin-run",
        description=(
            "Run a LinkedIn networking campaign non-interactively, sending a "
            "small batch of invitations (for cron/systemd-timer scheduling). "
            "All rate-limit, daily-cap and session logic is respected. Exits "
            "0 on success, non-zero on failure."
        ),
    )
    parser.add_argument(
        "--campaign",
        required=True,
        metavar="ID-OR-NAME",
        help="Campaign to run, given as its numeric id or its name.",
    )
    parser.add_argument(
        "--max",
        type=_positive_int,
        default=None,
        metavar="N",
        help=(
            "Cap on invitations sent this run (default: the campaign's "
            "daily_limit, falling back to DAILY_CONNECTION_LIMIT when the "
            "campaign has no valid positive limit)."
        ),
    )
    return parser


def main(argv=None):
    """Parse args and run the campaign non-interactively.

    Returns a process exit code (0 on success, non-zero on failure).

    Argument parsing (``--help``, missing/invalid args) is left to raise its
    own ``SystemExit`` uncaught — that is argparse's normal, intentional exit
    path. Everything after parsing is guarded: an unexpected exception (e.g. a
    locked/corrupt SQLite database, which ``DatabaseManager`` re-raises rather
    than swallows) would otherwise print a raw Python traceback instead of the
    one-line ``Error: ...`` contract the rest of the runner uses, and a Ctrl-C
    during a scheduled run would otherwise exit with an interpreter-default
    code instead of the conventional 130.
    """
    args = _build_parser().parse_args(argv)
    try:
        return CampaignRunner().run_noninteractive(args.campaign, args.max)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # A last-line-of-defense guard must survive hostile exceptions too: a
        # third-party exception whose __str__ raises would crash the guard
        # itself, so compute a safe detail string once and hand only that to
        # the logger's %s slot (exc_info formatting is already safe — the
        # traceback module guards str() failures internally).
        try:
            detail = str(exc).strip() or exc.__class__.__name__
        except Exception:
            detail = exc.__class__.__name__
        # Some exception texts span several lines (SQLAlchemy appends the
        # statement and a docs link) — the console-facing surfaces (stderr and
        # the ERROR record below, which the console handler also prints) stay
        # one line; the full detail is kept in the file logs.
        first_line = detail.splitlines()[0]
        # Two records on purpose, both kept off the console (stdout carries
        # progress only; the failure goes to stderr via the print below). The
        # one-line ERROR (no exc_info) reaches the dedicated errors.log so
        # monitoring of unattended runs keeps its failure signal — it opts out
        # of the console handler, which would otherwise echo it to stdout.
        # The traceback rides an INFO record into the main log only — an
        # ERROR with exc_info would dump the very traceback this guard exists
        # to suppress (same reasoning as cli.runner's automation-phase guard).
        logger.error(
            "linkedin-run failed: %s", first_line, extra={"console": False}
        )
        logger.info("Unhandled error in linkedin-run: %s", detail, exc_info=True)
        print(f"Error: {first_line}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
