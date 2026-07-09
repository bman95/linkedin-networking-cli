"""Unit tests for the advisory-only, RAM-based local-model recommendation."""

from unittest.mock import MagicMock, patch

import pytest

from llm_assist.hardware import (
    _HIGH_RAM_THRESHOLD_BYTES,
    RECOMMENDED_MODELS,
    recommend_model,
)


@pytest.mark.unit
class TestRecommendModel:
    def test_below_threshold_recommends_low_tier(self):
        with patch(
            "psutil.virtual_memory",
            return_value=MagicMock(total=_HIGH_RAM_THRESHOLD_BYTES - 1),
        ):
            assert recommend_model() == RECOMMENDED_MODELS[0]

    def test_at_threshold_recommends_high_tier(self):
        with patch(
            "psutil.virtual_memory", return_value=MagicMock(total=_HIGH_RAM_THRESHOLD_BYTES)
        ):
            assert recommend_model() == RECOMMENDED_MODELS[1]

    def test_well_above_threshold_recommends_high_tier(self):
        with patch(
            "psutil.virtual_memory",
            return_value=MagicMock(total=_HIGH_RAM_THRESHOLD_BYTES * 4),
        ):
            assert recommend_model() == RECOMMENDED_MODELS[1]

    def test_psutil_failure_falls_back_to_low_tier(self):
        with patch("psutil.virtual_memory", side_effect=RuntimeError("boom")):
            assert recommend_model() == RECOMMENDED_MODELS[0]
