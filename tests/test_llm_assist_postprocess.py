"""Unit tests for the deterministic post-processing fixups."""

import pytest

from llm_assist.postprocess import (
    clamp_daily_limit,
    clean_keywords,
    has_foreign_placeholder,
    repair_name_placeholder,
)


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
class TestHasForeignPlaceholder:
    def test_none_is_clean(self):
        assert has_foreign_placeholder(None) is False

    def test_name_only_is_clean(self):
        assert has_foreign_placeholder("Hi {name}, let's connect!") is False

    def test_plain_text_is_clean(self):
        assert has_foreign_placeholder("Hi there, let's connect!") is False

    def test_invented_brace_placeholder_is_flagged(self):
        assert has_foreign_placeholder("Hi {name}, saw your work at {company}!") is True

    def test_invented_bracket_placeholder_is_flagged(self):
        assert has_foreign_placeholder("Hi {name}, love [startup name]!") is True


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


@pytest.mark.unit
class TestCleanKeywords:
    def test_none_is_untouched(self):
        assert clean_keywords(None) == (None, False)

    def test_clean_input_is_unflagged(self):
        text = "data engineers, python, kubernetes"
        assert clean_keywords(text) == (text, False)

    def test_drops_empty_entries(self):
        cleaned, flagged = clean_keywords("data engineers, , python,")
        assert cleaned == "data engineers, python"
        assert flagged is True

    def test_trims_whitespace_around_terms(self):
        cleaned, flagged = clean_keywords("  data engineers ,  python  ")
        assert cleaned == "data engineers, python"
        assert flagged is True

    def test_dedupes_case_insensitively_preserving_first_occurrence(self):
        cleaned, flagged = clean_keywords("Berlin, python, berlin, PYTHON")
        assert cleaned == "Berlin, python"
        assert flagged is True

    def test_drops_terms_that_duplicate_location_text_tokens(self):
        # Regression case from issue #68: the model leaked "Berlin, Germany"
        # into keywords despite the prompt saying keywords aren't locations.
        cleaned, flagged = clean_keywords(
            "data engineers, Berlin, Germany, technology, data, engineers, "
            "skills, technical, network, connections, Berlin, Germany",
            location_text="Berlin",
        )
        assert cleaned == (
            "data engineers, Germany, technology, data, engineers, skills, "
            "technical, network, connections"
        )
        assert flagged is True

    def test_no_location_text_leaves_location_like_terms_alone(self):
        text = "data engineers, Berlin"
        assert clean_keywords(text, location_text=None) == (text, False)

    def test_all_terms_dropped_yields_none(self):
        cleaned, flagged = clean_keywords("Berlin, Germany", location_text="Berlin, Germany")
        assert cleaned is None
        assert flagged is True

    def test_multi_word_terms_are_not_dropped_by_single_word_location_tokens(self):
        text = "data engineers, python"
        cleaned, flagged = clean_keywords(text, location_text="Berlin")
        assert cleaned == text
        assert flagged is False
