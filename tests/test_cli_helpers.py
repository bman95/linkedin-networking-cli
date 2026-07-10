"""
Tests for the pure CLI helpers.

These cover formatting/field-access/CSV helpers that don't require an
interactive terminal, a database, or a browser. The logic lives in
``cli.helpers``, shared by the TUI and the non-interactive ``linkedin-run``
entry point.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest

from cli.helpers import (
    campaign_get_field,
    contacts_csv_filename,
    csv_value,
    effective_daily_limit,
    mask_api_key,
    mask_email,
)


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


@pytest.mark.unit
class TestMaskApiKey:
    def test_set(self):
        assert mask_api_key("sk-abc123") == "Set"

    def test_empty_or_none(self):
        assert mask_api_key(None) == "Not set"
        assert mask_api_key("") == "Not set"


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


@pytest.mark.unit
class TestContactsCsvFilename:
    def test_normal_name_is_untruncated(self):
        filename = contacts_csv_filename("SF Engineers")
        assert filename.startswith("SF_Engineers_contacts_")

    def test_unbounded_name_is_truncated(self):
        # Campaign.name is unbounded; the sanitized name must be truncated
        # so the final filename stays well under filesystem limits (255
        # bytes on most POSIX filesystems).
        filename = contacts_csv_filename("x" * 1000)
        assert len(filename.encode("utf-8")) < 255
        safe_part = filename.split("_contacts_")[0]
        assert safe_part == "x" * 80


@pytest.mark.unit
class TestEffectiveDailyLimit:
    """The shared rule (issue #46): the campaign's own positive limit binds;
    anything else falls back to the env default."""

    def test_positive_campaign_limit_is_authoritative(self):
        assert effective_daily_limit(80, 20) == 80

    @pytest.mark.parametrize("invalid", [None, 0, -5, True, "10", 3.5])
    def test_invalid_values_fall_back(self, invalid):
        assert effective_daily_limit(invalid, 20) == 20

    @pytest.mark.parametrize("stored", [80, 0, None])
    def test_matches_automation_enforcement(self, stored):
        """Display surfaces and enforcement must share one rule — the
        automation's _effective_daily_limit delegates to this helper, on the
        binding case and on the fallback (invalid/unset) cases alike."""
        from automation.linkedin import LinkedInAutomation

        campaign = SimpleNamespace(daily_limit=stored)
        settings = {"daily_connection_limit": 20}
        assert LinkedInAutomation._effective_daily_limit(
            campaign, settings
        ) == effective_daily_limit(stored, 20)
