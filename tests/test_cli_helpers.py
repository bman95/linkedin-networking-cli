"""
Tests for the pure CLI helpers.

These cover formatting/field-access/CSV helpers that don't require an
interactive terminal, a database, or a browser. The logic now lives in
``cli.helpers`` (extracted from the ``linkedin_cli.py`` monolith); the
``LinkedInCLI`` static methods remain as thin delegators, so we also assert
those still return identical results (no behavior change).
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from cli.helpers import (
    campaign_get_field,
    csv_value,
    effective_daily_limit,
    mask_email,
)
from linkedin_cli import LinkedInCLI


@pytest.mark.unit
class TestMaskEmail:
    def test_masks_local_part(self):
        assert mask_email("johndoe@example.com") == "joh***@example.com"

    def test_short_local_part(self):
        assert mask_email("ab@example.com") == "ab***@example.com"

    def test_no_at_sign(self):
        assert mask_email("plainstring") == "pla***"

    def test_empty_or_none(self):
        assert mask_email(None) == "Not set"
        assert mask_email("") == "Not set"

    def test_delegator_matches_helper(self):
        """The class static method delegates to the extracted helper."""
        assert LinkedInCLI._mask_email("johndoe@example.com") == mask_email(
            "johndoe@example.com"
        )
        assert LinkedInCLI._mask_email(None) == "Not set"


@pytest.mark.unit
class TestCsvValue:
    def test_none_becomes_empty(self):
        assert csv_value(None) == ""

    def test_datetime_isoformat(self):
        dt = datetime(2025, 1, 15, 12, 30, 0)
        assert csv_value(dt) == "2025-01-15T12:30:00"

    def test_other_values_stringified(self):
        assert csv_value(42) == "42"
        assert csv_value("sent") == "sent"

    def test_delegator_matches_helper(self):
        dt = datetime(2025, 1, 15, 12, 30, 0)
        assert LinkedInCLI._csv_value(dt) == csv_value(dt)
        assert LinkedInCLI._csv_value(None) == ""


@pytest.mark.unit
class TestCampaignGetField:
    def test_reads_from_object(self):
        obj = SimpleNamespace(name="Demo", daily_limit=20)
        assert campaign_get_field(obj, "name") == "Demo"
        assert campaign_get_field(obj, "missing", "fallback") == "fallback"

    def test_reads_from_dict(self):
        data = {"name": "Demo"}
        assert campaign_get_field(data, "name") == "Demo"
        assert campaign_get_field(data, "missing", 7) == 7

    def test_delegator_matches_helper(self):
        data = {"name": "Demo"}
        assert LinkedInCLI._campaign_get_field(data, "name") == campaign_get_field(
            data, "name"
        )


@pytest.mark.unit
class TestEffectiveDailyLimit:
    """The shared rule (issue #46): the campaign's own positive limit binds;
    anything else falls back to the env default."""

    def test_positive_campaign_limit_is_authoritative(self):
        assert effective_daily_limit(80, 20) == 80

    @pytest.mark.parametrize("invalid", [None, 0, -5, True, "10", 3.5])
    def test_invalid_values_fall_back(self, invalid):
        assert effective_daily_limit(invalid, 20) == 20

    def test_matches_automation_enforcement(self):
        """Display surfaces and enforcement must share one rule — the
        automation's _effective_daily_limit delegates to this helper."""
        from automation.linkedin import LinkedInAutomation

        campaign = SimpleNamespace(daily_limit=80)
        settings = {"daily_connection_limit": 20}
        assert LinkedInAutomation._effective_daily_limit(
            campaign, settings
        ) == effective_daily_limit(80, 20)
