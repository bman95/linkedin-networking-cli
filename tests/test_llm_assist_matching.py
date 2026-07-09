"""Unit tests for fuzzy-matching free-text LLM mentions against the app's

curated, LinkedIn-real vocabularies.
"""

import pytest

from llm_assist.matching import match_industry, match_location, match_network


@pytest.mark.unit
class TestMatchLocation:
    def test_exact_match(self):
        result = match_location("San Francisco Bay Area")
        assert result.matched_display_name == "San Francisco Bay Area"
        assert result.raw_text == "San Francisco Bay Area"
        assert result.needs_review is False

    def test_close_match(self):
        result = match_location("San Francisco")
        assert result.matched_display_name == "San Francisco Bay Area"
        assert result.needs_review is False

    def test_case_insensitive_match(self):
        result = match_location("SAN FRANCISCO BAY AREA")
        assert result.matched_display_name == "San Francisco Bay Area"
        assert result.needs_review is False

    def test_no_match_needs_review(self):
        result = match_location("A made-up place that doesn't exist anywhere")
        assert result.matched_display_name is None
        assert result.raw_text == "A made-up place that doesn't exist anywhere"
        assert result.needs_review is True

    def test_none_input_is_not_a_review_case(self):
        result = match_location(None)
        assert result.matched_display_name is None
        assert result.raw_text is None
        assert result.needs_review is False

    def test_empty_input_is_not_a_review_case(self):
        result = match_location("   ")
        assert result.matched_display_name is None
        assert result.raw_text is None
        assert result.needs_review is False

    def test_any_sentinel_is_not_offered_as_a_candidate(self):
        result = match_location("Any")
        # "Any" itself should not spuriously "match" the sentinel entry —
        # it's excluded from the candidate list entirely.
        assert result.matched_display_name != "Any"

    def test_short_name_matches_longer_curated_name_via_word_containment(self):
        # Regression: found via live testing against a real local model.
        # difflib's length-sensitive ratio alone scores "mexico city" vs
        # "mexico city metropolitan area" at 0.55 — just under the 0.6
        # cutoff — even though it's a perfect word-boundary match.
        result = match_location("Mexico City")
        assert result.matched_display_name == "Mexico City Metropolitan Area"
        assert result.needs_review is False

    def test_short_name_matches_greater_x_area_pattern(self):
        result = match_location("Boston")
        assert result.matched_display_name == "Greater Boston Area"
        assert result.needs_review is False

    def test_ambiguous_word_across_many_candidates_falls_back_to_review(self):
        # "Area" is a whole word in most "Greater X Area" candidates — an
        # ambiguous containment must never guess, so it falls through to
        # ratio matching (which also finds no confident single match here).
        result = match_location("Area")
        assert result.matched_display_name is None
        assert result.needs_review is True

    def test_word_containment_never_matches_inside_a_word(self):
        # "us" must not match "United States" as a substring of some other
        # token, nor match unrelated candidates via character overlap alone.
        result = match_location("us")
        assert result.matched_display_name != "Austin, Texas Area"


@pytest.mark.unit
class TestMatchIndustry:
    def test_close_match_despite_casing(self):
        result = match_industry("software")
        assert result.matched_display_name == "Computer Software"
        assert result.needs_review is False

    def test_no_match_needs_review(self):
        result = match_industry("underwater basket weaving")
        assert result.matched_display_name is None
        assert result.needs_review is True


@pytest.mark.unit
class TestMatchNetwork:
    def test_close_match(self):
        result = match_network("first degree only")
        assert result.matched_display_name == "1st degree connections only"
        assert result.needs_review is False

    def test_no_match_needs_review(self):
        result = match_network("aliens from outer space")
        assert result.matched_display_name is None
        assert result.needs_review is True

    def test_none_input(self):
        result = match_network(None)
        assert result.needs_review is False
        assert result.matched_display_name is None
