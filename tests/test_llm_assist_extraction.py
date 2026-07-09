"""Unit tests for the extraction orchestration (extract_campaign_fields).

Driven entirely by a fake LLMClient double — no network, no real model.
"""

import json

import pytest

from llm_assist.errors import LLMAssistCancelled, LLMResponseError
from llm_assist.extraction import extract_campaign_fields


class _FakeClient:
    """Returns queued raw responses in order; records every call's messages."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat_json(self, messages, json_schema, *, temperature=0.0):
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("chat_json called more times than expected")
        return self._responses.pop(0)


def _payload(**overrides) -> str:
    base = {
        "name": None,
        "description": None,
        "keywords": None,
        "location_text": None,
        "industry_text": None,
        "network_text": None,
        "daily_limit": None,
        "message_template": None,
    }
    base.update(overrides)
    return json.dumps(base)


@pytest.mark.unit
class TestExtractCampaignFieldsHappyPath:
    def test_happy_path_no_flags(self):
        client = _FakeClient(
            [_payload(name="SF Engineers", location_text="San Francisco Bay Area",
                      daily_limit=15, message_template="Hi {name}!")]
        )
        result = extract_campaign_fields("Software engineers in SF", client)

        assert result.data.name == "SF Engineers"
        assert result.data.daily_limit == 15
        assert result.data.message_template == "Hi {name}!"
        assert result.location_match.matched_display_name == "San Francisco Bay Area"
        assert result.flagged_fields == frozenset()
        assert result.repaired is False
        assert len(client.calls) == 1

    def test_progress_callback_is_notified(self):
        client = _FakeClient([_payload(name="X")])
        messages = []
        extract_campaign_fields("desc", client, progress=messages.append)
        assert any("model" in m.lower() for m in messages)
        assert messages[-1] == "Done."


@pytest.mark.unit
class TestExtractCampaignFieldsFlagging:
    def test_unmatched_location_is_flagged(self):
        client = _FakeClient([_payload(location_text="Nowhereland")])
        result = extract_campaign_fields("desc", client)
        assert "location" in result.flagged_fields
        assert result.location_match.matched_display_name is None
        assert result.location_match.raw_text == "Nowhereland"

    def test_mangled_placeholder_is_repaired_and_flagged(self):
        client = _FakeClient([_payload(message_template="Hi [name], connect?")])
        result = extract_campaign_fields("desc", client)
        assert result.data.message_template == "Hi {name}, connect?"
        assert "message_template" in result.flagged_fields

    def test_out_of_range_daily_limit_is_clamped_and_flagged(self):
        client = _FakeClient([_payload(daily_limit=500)])
        result = extract_campaign_fields("desc", client)
        assert result.data.daily_limit == 100
        assert "daily_limit" in result.flagged_fields

    def test_unmentioned_fields_are_not_flagged(self):
        client = _FakeClient([_payload()])
        result = extract_campaign_fields("desc", client)
        assert result.flagged_fields == frozenset()


@pytest.mark.unit
class TestExtractCampaignFieldsRepair:
    def test_malformed_json_triggers_one_repair_retry_then_succeeds(self):
        client = _FakeClient(["not json at all", _payload(name="Fixed")])
        result = extract_campaign_fields("desc", client)
        assert result.data.name == "Fixed"
        assert result.repaired is True
        assert len(client.calls) == 2

    def test_repair_prompt_includes_the_validation_error(self):
        client = _FakeClient(["not json at all", _payload(name="Fixed")])
        extract_campaign_fields("desc", client)
        repair_prompt = client.calls[1]
        assert repair_prompt[-1]["role"] == "user"
        assert "not valid JSON" in repair_prompt[-1]["content"]

    def test_malformed_twice_raises_response_error(self):
        client = _FakeClient(["not json", "still not json"])
        with pytest.raises(LLMResponseError):
            extract_campaign_fields("desc", client)

    def test_schema_violation_triggers_repair(self):
        client = _FakeClient(
            [json.dumps({"daily_limit": "not a number"}), _payload(name="Fixed")]
        )
        result = extract_campaign_fields("desc", client)
        assert result.data.name == "Fixed"
        assert result.repaired is True

    def test_unknown_extra_field_triggers_repair(self):
        client = _FakeClient(
            [json.dumps({"unexpected": "surprise"}), _payload(name="Fixed")]
        )
        result = extract_campaign_fields("desc", client)
        assert result.data.name == "Fixed"


@pytest.mark.unit
class TestExtractCampaignFieldsTruncation:
    def test_overlong_input_is_truncated_before_sending(self):
        client = _FakeClient([_payload()])
        description = "x" * 5000
        messages = []
        extract_campaign_fields(
            description, client, max_input_chars=100, progress=messages.append
        )
        sent_content = client.calls[0][-1]["content"]
        assert len(sent_content) == 100
        assert any("first 100 characters" in m for m in messages)

    def test_short_input_is_not_truncated(self):
        client = _FakeClient([_payload()])
        extract_campaign_fields("short description", client, max_input_chars=4000)
        assert client.calls[0][-1]["content"] == "short description"


@pytest.mark.unit
class TestExtractCampaignFieldsCancellation:
    def test_should_stop_true_before_call_short_circuits(self):
        client = _FakeClient([_payload()])
        with pytest.raises(LLMAssistCancelled):
            extract_campaign_fields("desc", client, should_stop=lambda: True)
        assert client.calls == []

    def test_should_stop_true_before_repair_short_circuits(self):
        client = _FakeClient(["not json"])
        stop_calls = {"n": 0}

        def should_stop():
            stop_calls["n"] += 1
            # False on the pre-call check, True on the pre-repair check.
            return stop_calls["n"] > 1

        with pytest.raises(LLMAssistCancelled):
            extract_campaign_fields("desc", client, should_stop=should_stop)
        assert len(client.calls) == 1  # the repair call was never made
