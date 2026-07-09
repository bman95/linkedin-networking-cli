"""Tests for the classic CLI's rate-limiting Settings copy (issue #46).

The enforced daily cap is per-campaign; the ``DAILY_CONNECTION_LIMIT`` env
value is only the fallback, and the weekly invitation budget is LinkedIn's
actually-binding constraint. These pin the ``show_limits_settings`` panel so
the env fallback can never silently reappear as "the" daily limit.
"""

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from linkedin_cli import LinkedInCLI

_AUTOMATION_SETTINGS = {
    "connection_delay_min": 2,
    "connection_delay_max": 5,
    "daily_connection_limit": 20,
    "connection_cooldown": 0,
    "search_limit": 100,
}


def _bare_cli(db_manager):
    """CLI instance without real DB/browser/settings (bypasses __init__)."""
    cli = object.__new__(LinkedInCLI)
    cli.console = Console(file=StringIO(), force_terminal=False, width=200)
    cli.settings = SimpleNamespace(
        get_automation_settings=lambda: dict(_AUTOMATION_SETTINGS),
        weekly_invitation_limit=100,
    )
    cli.db_manager = db_manager
    return cli


def _output(cli) -> str:
    return cli.console.file.getvalue()


@pytest.mark.unit
class TestShowLimitsSettings:
    def test_labels_env_value_as_fallback_and_shows_weekly_budget(self):
        db = SimpleNamespace(
            get_daily_connection_count=lambda date_str: 7,
            get_weekly_connection_count=lambda: 34,
        )
        cli = _bare_cli(db)
        cli.show_limits_settings()
        out = _output(cli)

        assert "Default Daily Limit (fallback when a campaign sets none): 20" in out
        assert "Weekly Invitation Limit: 100" in out
        assert "Used Today: 7" in out
        assert "Used This Week: 34/100" in out
        # Never the pre-#46 copy: the env value presented as "the" limit or as
        # the denominator of today's usage.
        assert "Daily Connection Limit:" not in out
        assert "Used Today: 7/" not in out

    def test_usage_lines_hidden_without_db(self):
        cli = _bare_cli(db_manager=None)
        cli.show_limits_settings()
        out = _output(cli)

        assert "Used Today:" not in out
        assert "Used This Week:" not in out
        # Configured limits still render — they come from settings, not the DB.
        assert "Default Daily Limit (fallback when a campaign sets none): 20" in out
        assert "Weekly Invitation Limit: 100" in out
