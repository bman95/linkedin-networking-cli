"""Unit tests for the deterministic post-processing fixups."""

import pytest

from llm_assist.postprocess import clamp_daily_limit, repair_name_placeholder


@pytest.mark.unit
class TestRepairNamePlaceholder:
    def test_none_is_untouched(self):
        assert repair_name_placeholder(None) == (None, False)

    def test_already_correct_is_unflagged(self):
        text = "Hi {name}, let's connect!"
        assert repair_name_placeholder(text) == (text, False)

    def test_repairs_bracket_name(self):
        repaired, flagged = repair_name_placeholder("Hi [name], let's connect!")
        assert repaired == "Hi {name}, let's connect!"
        assert flagged is True

    def test_repairs_brace_first_name(self):
        repaired, flagged = repair_name_placeholder("Hi {first_name}, welcome!")
        assert repaired == "Hi {name}, welcome!"
        assert flagged is True

    def test_repairs_bracket_first_name_with_space(self):
        repaired, flagged = repair_name_placeholder("Hi [First Name], welcome!")
        assert repaired == "Hi {name}, welcome!"
        assert flagged is True

    def test_repairs_firstname_no_separator(self):
        repaired, flagged = repair_name_placeholder("Hi {firstname}!")
        assert repaired == "Hi {name}!"
        assert flagged is True

    def test_no_recognizable_placeholder_is_left_unchanged(self):
        text = "Hi there, let's connect!"
        assert repair_name_placeholder(text) == (text, False)

    def test_only_first_match_is_replaced(self):
        repaired, flagged = repair_name_placeholder("[name] met [name] again")
        assert repaired == "{name} met [name] again"
        assert flagged is True


@pytest.mark.unit
class TestClampDailyLimit:
    def test_none_is_untouched(self):
        assert clamp_daily_limit(None) == (None, False)

    def test_in_range_is_unflagged(self):
        assert clamp_daily_limit(15) == (15, False)

    def test_boundary_values_are_unflagged(self):
        assert clamp_daily_limit(1) == (1, False)
        assert clamp_daily_limit(100) == (100, False)

    def test_too_high_is_clamped_and_flagged(self):
        assert clamp_daily_limit(500) == (100, True)

    def test_too_low_is_clamped_and_flagged(self):
        assert clamp_daily_limit(0) == (1, True)

    def test_negative_is_clamped_and_flagged(self):
        assert clamp_daily_limit(-10) == (1, True)
