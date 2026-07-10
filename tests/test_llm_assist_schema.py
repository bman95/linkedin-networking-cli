"""Unit tests for the ExtractedCampaign extraction schema."""

import pytest
from pydantic import ValidationError

from llm_assist.schema import EXTRACTION_JSON_SCHEMA, ExtractedCampaign


@pytest.mark.unit
class TestExtractedCampaign:
    def test_parses_a_full_payload(self):
        payload = {
            "name": "SF Engineers",
            "description": "Backend folks in the Bay Area",
            "keywords": "software engineer",
            "location_text": "San Francisco",
            "industry_text": "software",
            "network_text": "1st and 2nd",
            "daily_limit": 15,
            "message_template": "Hi {name}, let's connect!",
        }
        parsed = ExtractedCampaign.model_validate(payload)
        assert parsed.name == "SF Engineers"
        assert parsed.daily_limit == 15

    def test_parses_an_all_null_payload(self):
        payload = {
            "name": None,
            "description": None,
            "keywords": None,
            "location_text": None,
            "industry_text": None,
            "network_text": None,
            "daily_limit": None,
            "message_template": None,
        }
        parsed = ExtractedCampaign.model_validate(payload)
        assert parsed.name is None
        assert parsed.daily_limit is None

    def test_missing_fields_default_to_none(self):
        parsed = ExtractedCampaign.model_validate({})
        assert parsed.name is None
        assert parsed.message_template is None

    def test_rejects_unexpected_extra_field(self):
        payload = {"name": "X", "unexpected_field": "surprise"}
        with pytest.raises(ValidationError):
            ExtractedCampaign.model_validate(payload)

    def test_rejects_wrong_type(self):
        with pytest.raises(ValidationError):
            ExtractedCampaign.model_validate({"daily_limit": "not a number"})


@pytest.mark.unit
class TestExtractionJsonSchema:
    def test_contains_all_field_names(self):
        properties = EXTRACTION_JSON_SCHEMA["properties"]
        expected = {
            "name",
            "description",
            "keywords",
            "location_text",
            "industry_text",
            "network_text",
            "daily_limit",
            "message_template",
        }
        assert expected <= properties.keys()

    def test_forbids_additional_properties(self):
        assert EXTRACTION_JSON_SCHEMA["additionalProperties"] is False

    def test_required_lists_every_property(self):
        # OpenAI strict mode requires every property to appear in
        # "required" — optionality is expressed via each property's own
        # nullable type instead.
        assert EXTRACTION_JSON_SCHEMA["required"] == sorted(
            EXTRACTION_JSON_SCHEMA["properties"].keys()
        )
