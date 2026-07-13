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
    locked/corrupt SQLite database, which ``DatabaseManager`` logs and
    re-raises rather than swallows) would otherwise print a raw Python
    traceback instead of the one-line ``Error: ...`` contract the rest of the
    runner uses, and a Ctrl-C during a scheduled run would otherwise exit with
    an interpreter-default code instead of the conventional 130.
    """
    args = _build_parser().parse_args(argv)
    try:
        return CampaignRunner().run_noninteractive(args.campaign, args.max)
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        # INFO, not ERROR: the console handler logs WARNING+ — an ERROR record
        # with exc_info would dump the very traceback this guard exists to
        # suppress. Keep the traceback in the file logs; the console stays
        # clean (same convention as cli.runner's automation-phase guard).
        logger.info("Unhandled error in linkedin-run: %s", exc, exc_info=True)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
