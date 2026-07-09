"""Tests for the non-interactive ``linkedin-cli run`` subcommand.

These cover the CLI dispatch/plumbing only — arg parsing, campaign resolution,
credential validation, and that the shared automation core is invoked with the
right campaign + cap. The automation boundary is mocked, so no browser or
network is exercised (the live send is validated manually by the owner).
"""

import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest
from rich.console import Console

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import linkedin_cli
from linkedin_cli import LinkedInCLI


def _bare_cli():
    """Build a CLI instance without touching the real DB/browser/settings.

    ``__init__`` initializes live components, so we bypass it and wire only the
    attributes the non-interactive path needs.
    """
    cli = object.__new__(LinkedInCLI)
    cli.console = Console(file=StringIO(), force_terminal=False, width=200)
    cli.db_manager = None
    cli.settings = None
    return cli


class _FakeDB:
    """Minimal db_manager stub for campaign resolution."""

    def __init__(self, campaigns):
        self._campaigns = campaigns

    def get_campaign(self, campaign_id):
        for c in self._campaigns:
            if c.id == campaign_id:
                return c
        return None

    def get_campaigns(self, active_only=True):
        return list(self._campaigns)


def _settings(valid=True, search_limit=100):
    return SimpleNamespace(
        validate_credentials=lambda: valid,
        get_automation_settings=lambda: {"search_limit": search_limit},
    )


# ---------------------------------------------------------------------------
# Argument parsing / dispatch in main()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainDispatch:
    def test_no_args_chooses_interactive_path(self):
        """No subcommand → the interactive menu, not the run path."""
        instance = MagicMock()
        with patch.object(linkedin_cli, "LinkedInCLI", return_value=instance):
            rc = linkedin_cli.main([])
        instance.display_welcome.assert_called_once()
        instance.main_menu.assert_called_once()
        instance.run_noninteractive.assert_not_called()
        # Interactive path returns no explicit exit code.
        assert rc is None

    def test_run_subcommand_chooses_noninteractive_path(self):
        """`run --campaign X` → run_noninteractive, not the interactive menu."""
        instance = MagicMock()
        instance.run_noninteractive.return_value = 0
        with patch.object(linkedin_cli, "LinkedInCLI", return_value=instance):
            rc = linkedin_cli.main(["run", "--campaign", "Tech Leads"])
        instance.run_noninteractive.assert_called_once_with("Tech Leads", None)
        instance.main_menu.assert_not_called()
        assert rc == 0

    def test_run_subcommand_passes_max(self):
        instance = MagicMock()
        instance.run_noninteractive.return_value = 0
        with patch.object(linkedin_cli, "LinkedInCLI", return_value=instance):
            linkedin_cli.main(["run", "--campaign", "5", "--max", "3"])
        instance.run_noninteractive.assert_called_once_with("5", 3)

    def test_run_without_campaign_errors(self):
        """--campaign is required; argparse exits non-zero."""
        with pytest.raises(SystemExit) as excinfo:
            linkedin_cli.main(["run"])
        assert excinfo.value.code != 0

    @pytest.mark.parametrize("bad_max", ["0", "-3", "two"])
    def test_run_rejects_non_positive_max(self, bad_max):
        """--max must be a positive integer; argparse exits non-zero."""
        with pytest.raises(SystemExit) as excinfo:
            linkedin_cli.main(["run", "--campaign", "1", "--max", bad_max])
        assert excinfo.value.code != 0

    def test_run_help_exits_zero(self):
        with pytest.raises(SystemExit) as excinfo:
            linkedin_cli.main(["run", "--help"])
        assert excinfo.value.code == 0


# ---------------------------------------------------------------------------
# Campaign resolution — by id and by name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveCampaign:
    def _cli_with_campaigns(self, campaigns):
        cli = _bare_cli()
        cli.db_manager = _FakeDB(campaigns)
        return cli

    def test_resolves_by_numeric_id(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Beta", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        assert cli._resolve_campaign("2") is c2

    def test_resolves_by_exact_name(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Beta", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        assert cli._resolve_campaign("Beta") is c2

    def test_resolves_by_case_insensitive_name(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        cli = self._cli_with_campaigns([c1])
        assert cli._resolve_campaign("alpha") is c1

    def test_unknown_reference_returns_none(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        cli = self._cli_with_campaigns([c1])
        assert cli._resolve_campaign("nope") is None

    def test_duplicate_names_raise_instead_of_picking_first(self):
        """Campaign.name is not unique: an ambiguous name must never silently
        run whichever row came back first (wrong audience from a scheduler)."""
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Growth", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        with pytest.raises(ValueError, match="ambiguous"):
            cli._resolve_campaign("Growth")

    def test_duplicate_case_insensitive_names_raise(self):
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="GROWTH", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        with pytest.raises(ValueError, match="ambiguous"):
            cli._resolve_campaign("growth")

    def test_exact_match_wins_over_case_insensitive_duplicates(self):
        """One exact match is unambiguous even if other casings exist."""
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="GROWTH", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        assert cli._resolve_campaign("Growth") is c1

    def test_id_resolution_unaffected_by_duplicate_names(self):
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Growth", daily_limit=15)
        cli = self._cli_with_campaigns([c1, c2])
        assert cli._resolve_campaign("2") is c2


# ---------------------------------------------------------------------------
# run_noninteractive — credential validation and automation invocation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunNoninteractive:
    def test_missing_db_or_settings_exits_nonzero(self):
        cli = _bare_cli()  # db_manager / settings are None
        rc = cli.run_noninteractive("1")
        assert rc != 0

    def test_missing_credentials_warns_but_reuses_saved_session(self, capsys):
        """No credentials is a warning, not a hard stop: login() can resume a
        saved session (session.json / persistent profile) without them, which
        is the log-in-once-then-schedule workflow. A dead session still fails
        the run through the login step (bounded, non-zero)."""
        cli = _bare_cli()
        cli.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        cli.settings = _settings(valid=False)
        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = cli.run_noninteractive("1")

        assert rc == 0
        cli._run_campaign_automation.assert_called_once()
        assert "saved LinkedIn session" in capsys.readouterr().err

    def test_missing_credentials_and_failed_login_exits_nonzero(self):
        cli = _bare_cli()
        cli.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        cli.settings = _settings(valid=False)
        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio,
            "run",
            side_effect=lambda coro: {"status": "login_failed"},
        ):
            rc = cli.run_noninteractive("1")

        assert rc != 0

    def test_unknown_campaign_exits_nonzero(self):
        cli = _bare_cli()
        cli.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        cli.settings = _settings(valid=True)
        rc = cli.run_noninteractive("ghost")
        assert rc != 0

    def test_inactive_campaign_exits_nonzero(self, capsys):
        """A deactivated campaign must not keep sending from cron — the
        interactive/TUI flows only ever offer active campaigns."""
        campaign = SimpleNamespace(
            id=1, name="Paused", daily_limit=20, active=False
        )
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True)
        cli._run_campaign_automation = MagicMock(name="core")

        rc = cli.run_noninteractive("1")

        assert rc != 0
        assert "inactive" in capsys.readouterr().err
        cli._run_campaign_automation.assert_not_called()

    def test_ambiguous_name_exits_nonzero(self, capsys):
        campaigns = [
            SimpleNamespace(id=1, name="Growth", daily_limit=20, active=True),
            SimpleNamespace(id=2, name="Growth", daily_limit=15, active=True),
        ]
        cli = _bare_cli()
        cli.db_manager = _FakeDB(campaigns)
        cli.settings = _settings(valid=True)
        cli._run_campaign_automation = MagicMock(name="core")

        rc = cli.run_noninteractive("Growth")

        assert rc != 0
        assert "ambiguous" in capsys.readouterr().err
        cli._run_campaign_automation.assert_not_called()

    def test_invokes_core_with_campaign_and_explicit_max(self):
        """--max caps invitations SENT (max_sends); the scan budget stays the
        automation search_limit setting, so repeat scheduled runs can skip past
        already-contacted results without burning the cap on them."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=20)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True, search_limit=100)

        cli._run_campaign_automation = MagicMock(name="core")

        def fake_run(coro):
            return {"status": "success", "sent": 2, "profiles": 5}

        with patch.object(linkedin_cli.asyncio, "run", side_effect=fake_run):
            rc = cli.run_noninteractive("Growth", max_invites=3)

        assert rc == 0
        cli._run_campaign_automation.assert_called_once_with(
            campaign, 100, ANY, max_sends=3
        )

    def test_defaults_max_to_campaign_daily_limit(self):
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True, search_limit=50)

        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = cli.run_noninteractive("7")  # no --max

        assert rc == 0
        cli._run_campaign_automation.assert_called_once_with(
            campaign, 50, ANY, max_sends=12
        )

    def test_defaults_max_through_shared_rule_when_limit_invalid(self):
        """An invalid campaign daily_limit falls back to the env default via
        the shared effective_daily_limit rule (issue #46) — never a 0/None cap
        that would silently send nothing."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=0)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = SimpleNamespace(
            validate_credentials=lambda: True,
            get_automation_settings=lambda: {
                "search_limit": 50,
                "daily_connection_limit": 25,
            },
        )

        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = cli.run_noninteractive("7")  # no --max

        assert rc == 0
        cli._run_campaign_automation.assert_called_once_with(
            campaign, 50, ANY, max_sends=25
        )

    def test_safety_stop_exits_nonzero(self, capsys):
        """A protective CAPTCHA/challenge stop must fail the scheduled run —
        never exit 0 as if it were a clean 'no profiles' outcome."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True)
        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio,
            "run",
            side_effect=lambda coro: {
                "status": "safety_stop",
                "stopped_reason": "captcha",
                "sent": 1,
                "possibly_sent": 0,
                "scanned": 4,
                "profiles": 4,
            },
        ):
            rc = cli.run_noninteractive("7")

        assert rc != 0
        err = capsys.readouterr().err
        assert "CAPTCHA" in err

    def test_login_failure_exits_nonzero(self):
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True)
        cli._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            linkedin_cli.asyncio,
            "run",
            side_effect=lambda coro: {"status": "login_failed"},
        ):
            rc = cli.run_noninteractive("7")
        assert rc != 0

    def test_typed_automation_error_exits_nonzero(self, monkeypatch):
        from exceptions import CaptchaDetectedException

        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/run-cmd-artifacts")
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        cli = _bare_cli()
        cli.db_manager = _FakeDB([campaign])
        cli.settings = _settings(valid=True)

        # Real coroutine created then closed by the patched asyncio.run.
        def fake_run(coro):
            coro.close()
            raise CaptchaDetectedException("blocked")

        with patch.object(linkedin_cli.asyncio, "run", side_effect=fake_run):
            rc = cli.run_noninteractive("7")
        assert rc != 0
