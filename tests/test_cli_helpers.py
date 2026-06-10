"""
Tests for pure helper methods on the LinkedInCLI class.

These cover formatting helpers that don't require an interactive terminal,
a database, or a browser.
"""

import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from linkedin_cli import LinkedInCLI


@pytest.mark.unit
class TestMaskEmail:
    def test_masks_local_part(self):
        assert LinkedInCLI._mask_email("johndoe@example.com") == "joh***@example.com"

    def test_short_local_part(self):
        assert LinkedInCLI._mask_email("ab@example.com") == "ab***@example.com"

    def test_no_at_sign(self):
        assert LinkedInCLI._mask_email("plainstring") == "pla***"

    def test_empty_or_none(self):
        assert LinkedInCLI._mask_email(None) == "Not set"
        assert LinkedInCLI._mask_email("") == "Not set"


@pytest.mark.unit
class TestCsvValue:
    def test_none_becomes_empty(self):
        assert LinkedInCLI._csv_value(None) == ""

    def test_datetime_isoformat(self):
        dt = datetime(2025, 1, 15, 12, 30, 0)
        assert LinkedInCLI._csv_value(dt) == "2025-01-15T12:30:00"

    def test_other_values_stringified(self):
        assert LinkedInCLI._csv_value(42) == "42"
        assert LinkedInCLI._csv_value("sent") == "sent"


@pytest.mark.unit
class TestCampaignGetField:
    def test_reads_from_object(self):
        from types import SimpleNamespace

        obj = SimpleNamespace(name="Demo", daily_limit=20)
        assert LinkedInCLI._campaign_get_field(obj, "name") == "Demo"
        assert LinkedInCLI._campaign_get_field(obj, "missing", "fallback") == "fallback"

    def test_reads_from_dict(self):
        data = {"name": "Demo"}
        assert LinkedInCLI._campaign_get_field(data, "name") == "Demo"
        assert LinkedInCLI._campaign_get_field(data, "missing", 7) == 7
