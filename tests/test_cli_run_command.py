"""Tests for the non-interactive ``linkedin-run`` entry point.

These cover the CLI dispatch/plumbing only — arg parsing, campaign resolution,
credential validation, and that the shared automation core is invoked with the
right campaign + cap. The automation boundary is mocked, so no browser or
network is exercised (the live send is validated manually by the owner).
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

import linkedin_run
from cli import runner as runner_module
from cli.runner import CampaignRunner


def _bare_runner():
    """Build a runner instance without touching the real DB/browser/settings.

    ``__init__`` initializes live components, so we bypass it and wire only the
    attributes the non-interactive path needs.
    """
    runner = object.__new__(CampaignRunner)
    runner.db_manager = None
    runner.settings = None
    return runner


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
# CampaignRunner.__init__ — degrade gracefully, but stay visible on stderr
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCampaignRunnerInit:
    def test_init_failure_degrades_and_prints_to_stderr(self, monkeypatch, capsys):
        """A scheduled run has no interactive console — a construction failure
        (bad settings/DB) must be visible on stderr, not just logged, since
        stdout/stderr is the operator's primary diagnostic surface."""

        def _boom(*a, **k):
            raise RuntimeError("disk unavailable")

        monkeypatch.setattr(runner_module, "AppSettings", _boom)

        runner = CampaignRunner()

        assert runner.settings is None
        assert runner.db_manager is None
        err = capsys.readouterr().err
        assert "Error initializing components" in err
        assert "disk unavailable" in err


# ---------------------------------------------------------------------------
# Argument parsing / dispatch in main()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainDispatch:
    def test_dispatches_to_run_noninteractive(self):
        instance = MagicMock()
        instance.run_noninteractive.return_value = 0
        with patch.object(linkedin_run, "CampaignRunner", return_value=instance):
            rc = linkedin_run.main(["--campaign", "Tech Leads"])
        instance.run_noninteractive.assert_called_once_with("Tech Leads", None)
        assert rc == 0

    def test_passes_max(self):
        instance = MagicMock()
        instance.run_noninteractive.return_value = 0
        with patch.object(linkedin_run, "CampaignRunner", return_value=instance):
            linkedin_run.main(["--campaign", "5", "--max", "3"])
        instance.run_noninteractive.assert_called_once_with("5", 3)

    def test_without_campaign_errors(self):
        """--campaign is required; argparse exits non-zero."""
        with pytest.raises(SystemExit) as excinfo:
            linkedin_run.main([])
        assert excinfo.value.code != 0

    @pytest.mark.parametrize("bad_max", ["0", "-3", "two"])
    def test_rejects_non_positive_max(self, bad_max):
        """--max must be a positive integer; argparse exits non-zero."""
        with pytest.raises(SystemExit) as excinfo:
            linkedin_run.main(["--campaign", "1", "--max", bad_max])
        assert excinfo.value.code != 0

    def test_help_exits_zero(self):
        with pytest.raises(SystemExit) as excinfo:
            linkedin_run.main(["--help"])
        assert excinfo.value.code == 0

    def test_unexpected_exception_prints_one_liner_and_exits_nonzero(self, capsys):
        """A non-ValueError raised anywhere in run_noninteractive (e.g. a
        locked/corrupt SQLite database, which DatabaseManager logs and
        re-raises rather than swallows) must not leak a raw traceback — it
        should hit the same one-line ``Error: ...`` contract as every other
        failure path."""
        instance = MagicMock()
        instance.run_noninteractive.side_effect = RuntimeError("database is locked")
        with patch.object(linkedin_run, "CampaignRunner", return_value=instance):
            rc = linkedin_run.main(["--campaign", "Tech Leads"])
        assert rc != 0
        assert rc != 130
        assert "Error: database is locked" in capsys.readouterr().err

    def test_keyboard_interrupt_exits_130(self, capsys):
        """Ctrl-C during a scheduled run should exit with the conventional
        130, not an interpreter-default traceback."""
        instance = MagicMock()
        instance.run_noninteractive.side_effect = KeyboardInterrupt()
        with patch.object(linkedin_run, "CampaignRunner", return_value=instance):
            rc = linkedin_run.main(["--campaign", "Tech Leads"])
        assert rc == 130
        assert "Interrupted" in capsys.readouterr().err

    def test_exception_with_broken_str_still_exits_cleanly(self, capsys):
        """The guard must survive hostile exceptions too: one whose __str__
        raises must still yield a one-line ``Error: ...`` (falling back to the
        class name) and exit 1 — never a raw traceback from the guard itself."""

        class BrokenStrError(Exception):
            def __str__(self):
                raise RuntimeError("str is broken")

        instance = MagicMock()
        instance.run_noninteractive.side_effect = BrokenStrError()
        with patch.object(linkedin_run, "CampaignRunner", return_value=instance):
            rc = linkedin_run.main(["--campaign", "Tech Leads"])
        assert rc == 1
        captured = capsys.readouterr()
        assert "Error: BrokenStrError" in captured.err
        assert "Traceback" not in captured.err
        assert "Logging error" not in captured.err

    def test_keyboard_interrupt_during_construction_exits_130(self, capsys):
        """The guard wraps CampaignRunner() construction too — __init__ only
        catches Exception, so a Ctrl-C during it must reach main()'s guard."""
        with patch.object(
            linkedin_run, "CampaignRunner", side_effect=KeyboardInterrupt()
        ):
            rc = linkedin_run.main(["--campaign", "Tech Leads"])
        assert rc == 130
        assert "Interrupted" in capsys.readouterr().err

    def test_db_error_through_real_runner_hits_the_guard(self, capsys, caplog):
        """End-to-end through the real CampaignRunner: a non-ValueError from
        the db layer during campaign resolution (issue #60's scenario — e.g. a
        locked SQLite file, which DatabaseManager re-raises) must surface as
        the one-line ``Error: ...`` contract with exit 1, no traceback on
        either stream, and the full traceback captured by the logger. The
        exception text is multi-line on purpose — SQLAlchemy's
        OperationalError str() spans several lines (statement + docs link) and
        the stderr contract must stay one line regardless."""
        multiline = (
            "(sqlite3.OperationalError) database is locked\n"
            "[SQL: SELECT campaign.id FROM campaign]\n"
            "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
        )

        class _LockedDB:
            def get_campaign(self, campaign_id):
                raise RuntimeError(multiline)

            def get_campaigns(self, active_only=True):
                raise RuntimeError(multiline)

        def _real_runner():
            runner = object.__new__(CampaignRunner)
            runner.db_manager = _LockedDB()
            runner.settings = _settings(valid=True)
            return runner

        with caplog.at_level(logging.INFO, logger="linkedin_run"):
            with patch.object(
                linkedin_run, "CampaignRunner", side_effect=_real_runner
            ):
                rc = linkedin_run.main(["--campaign", "1"])

        assert rc == 1
        captured = capsys.readouterr()
        error_lines = [
            line for line in captured.err.splitlines() if line.startswith("Error:")
        ]
        assert error_lines == [
            "Error: (sqlite3.OperationalError) database is locked"
        ]
        assert "[SQL:" not in captured.err
        assert "Traceback" not in captured.err
        assert "Traceback" not in captured.out
        # The "log" half of the contract: the traceback lands in the logger.
        assert any(r.exc_info for r in caplog.records)


# ---------------------------------------------------------------------------
# Campaign resolution — by id and by name
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolveCampaign:
    def _runner_with_campaigns(self, campaigns):
        runner = _bare_runner()
        runner.db_manager = _FakeDB(campaigns)
        return runner

    def test_resolves_by_numeric_id(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Beta", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        assert runner._resolve_campaign("2") is c2

    def test_resolves_by_exact_name(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Beta", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        assert runner._resolve_campaign("Beta") is c2

    def test_resolves_by_case_insensitive_name(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        runner = self._runner_with_campaigns([c1])
        assert runner._resolve_campaign("alpha") is c1

    def test_unknown_reference_returns_none(self):
        c1 = SimpleNamespace(id=1, name="Alpha", daily_limit=20)
        runner = self._runner_with_campaigns([c1])
        assert runner._resolve_campaign("nope") is None

    def test_duplicate_names_raise_instead_of_picking_first(self):
        """Campaign.name is not unique: an ambiguous name must never silently
        run whichever row came back first (wrong audience from a scheduler)."""
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Growth", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        with pytest.raises(ValueError, match="ambiguous"):
            runner._resolve_campaign("Growth")

    def test_duplicate_case_insensitive_names_raise(self):
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="GROWTH", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        with pytest.raises(ValueError, match="ambiguous"):
            runner._resolve_campaign("growth")

    def test_exact_match_wins_over_case_insensitive_duplicates(self):
        """One exact match is unambiguous even if other casings exist."""
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="GROWTH", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        assert runner._resolve_campaign("Growth") is c1

    def test_id_resolution_unaffected_by_duplicate_names(self):
        c1 = SimpleNamespace(id=1, name="Growth", daily_limit=20)
        c2 = SimpleNamespace(id=2, name="Growth", daily_limit=15)
        runner = self._runner_with_campaigns([c1, c2])
        assert runner._resolve_campaign("2") is c2


# ---------------------------------------------------------------------------
# run_noninteractive — credential validation and automation invocation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRunNoninteractive:
    def test_missing_db_or_settings_exits_nonzero(self):
        runner = _bare_runner()  # db_manager / settings are None
        rc = runner.run_noninteractive("1")
        assert rc != 0

    def test_missing_credentials_warns_but_reuses_saved_session(self, capsys):
        """No credentials is a warning, not a hard stop: login() can resume a
        saved session (session.json / persistent profile) without them, which
        is the log-in-once-then-schedule workflow. A dead session still fails
        the run through the login step (bounded, non-zero)."""
        runner = _bare_runner()
        runner.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        runner.settings = _settings(valid=False)
        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = runner.run_noninteractive("1")

        assert rc == 0
        runner._run_campaign_automation.assert_called_once()
        assert "saved LinkedIn session" in capsys.readouterr().err

    def test_missing_credentials_and_failed_login_exits_nonzero(self):
        runner = _bare_runner()
        runner.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        runner.settings = _settings(valid=False)
        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio,
            "run",
            side_effect=lambda coro: {"status": "login_failed"},
        ):
            rc = runner.run_noninteractive("1")

        assert rc != 0

    def test_unknown_campaign_exits_nonzero(self):
        runner = _bare_runner()
        runner.db_manager = _FakeDB([SimpleNamespace(id=1, name="Alpha", daily_limit=20)])
        runner.settings = _settings(valid=True)
        rc = runner.run_noninteractive("ghost")
        assert rc != 0

    def test_inactive_campaign_exits_nonzero(self, capsys):
        """A deactivated campaign must not keep sending from cron — the
        interactive/TUI flows only ever offer active campaigns."""
        campaign = SimpleNamespace(
            id=1, name="Paused", daily_limit=20, active=False
        )
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True)
        runner._run_campaign_automation = MagicMock(name="core")

        rc = runner.run_noninteractive("1")

        assert rc != 0
        assert "inactive" in capsys.readouterr().err
        runner._run_campaign_automation.assert_not_called()

    def test_ambiguous_name_exits_nonzero(self, capsys):
        campaigns = [
            SimpleNamespace(id=1, name="Growth", daily_limit=20, active=True),
            SimpleNamespace(id=2, name="Growth", daily_limit=15, active=True),
        ]
        runner = _bare_runner()
        runner.db_manager = _FakeDB(campaigns)
        runner.settings = _settings(valid=True)
        runner._run_campaign_automation = MagicMock(name="core")

        rc = runner.run_noninteractive("Growth")

        assert rc != 0
        assert "ambiguous" in capsys.readouterr().err
        runner._run_campaign_automation.assert_not_called()

    def test_invokes_core_with_campaign_and_explicit_max(self):
        """--max caps invitations SENT (max_sends); the scan budget stays the
        automation search_limit setting, so repeat scheduled runs can skip past
        already-contacted results without burning the cap on them."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=20)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True, search_limit=100)

        runner._run_campaign_automation = MagicMock(name="core")

        def fake_run(coro):
            return {"status": "success", "sent": 2, "profiles": 5}

        with patch.object(runner_module.asyncio, "run", side_effect=fake_run):
            rc = runner.run_noninteractive("Growth", max_invites=3)

        assert rc == 0
        runner._run_campaign_automation.assert_called_once_with(
            campaign, 100, ANY, max_sends=3
        )

    def test_defaults_max_to_campaign_daily_limit(self):
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True, search_limit=50)

        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = runner.run_noninteractive("7")  # no --max

        assert rc == 0
        runner._run_campaign_automation.assert_called_once_with(
            campaign, 50, ANY, max_sends=12
        )

    def test_defaults_max_through_shared_rule_when_limit_invalid(self):
        """An invalid campaign daily_limit falls back to the env default via
        the shared effective_daily_limit rule (issue #46) — never a 0/None cap
        that would silently send nothing."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=0)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = SimpleNamespace(
            validate_credentials=lambda: True,
            get_automation_settings=lambda: {
                "search_limit": 50,
                "daily_connection_limit": 25,
            },
        )

        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio, "run", side_effect=lambda coro: {"status": "success"}
        ):
            rc = runner.run_noninteractive("7")  # no --max

        assert rc == 0
        runner._run_campaign_automation.assert_called_once_with(
            campaign, 50, ANY, max_sends=25
        )

    def test_safety_stop_exits_nonzero(self, capsys):
        """A protective CAPTCHA/challenge stop must fail the scheduled run —
        never exit 0 as if it were a clean 'no profiles' outcome."""
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True)
        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio,
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
            rc = runner.run_noninteractive("7")

        assert rc != 0
        err = capsys.readouterr().err
        assert "CAPTCHA" in err

    def test_login_failure_exits_nonzero(self):
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True)
        runner._run_campaign_automation = MagicMock(name="core")

        with patch.object(
            runner_module.asyncio,
            "run",
            side_effect=lambda coro: {"status": "login_failed"},
        ):
            rc = runner.run_noninteractive("7")
        assert rc != 0

    def test_typed_automation_error_exits_nonzero(self, monkeypatch):
        from exceptions import CaptchaDetectedException

        monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", "/tmp/run-cmd-artifacts")
        campaign = SimpleNamespace(id=7, name="Growth", daily_limit=12)
        runner = _bare_runner()
        runner.db_manager = _FakeDB([campaign])
        runner.settings = _settings(valid=True)

        # Real coroutine created then closed by the patched asyncio.run.
        def fake_run(coro):
            coro.close()
            raise CaptchaDetectedException("blocked")

        with patch.object(runner_module.asyncio, "run", side_effect=fake_run):
            rc = runner.run_noninteractive("7")
        assert rc != 0
