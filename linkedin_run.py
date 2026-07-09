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
from utils.logging import LoggerSetup

LoggerSetup.setup()

from cli.runner import CampaignRunner


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
    """
    args = _build_parser().parse_args(argv)
    return CampaignRunner().run_noninteractive(args.campaign, args.max)


if __name__ == "__main__":
    sys.exit(main())
