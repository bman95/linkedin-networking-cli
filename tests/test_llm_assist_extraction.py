"""Unit tests for the extraction orchestration (extract_campaign_fields).

Driven entirely by a fake LLMClient double — no network, no real model.
"""

import json

import pytest

from llm_assist import prompts
from llm_assist.errors import LLMAssistCancelled, LLMResponseError
from llm_assist.extraction import _parse, extract_campaign_fields


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
class TestPromptContract:
    def test_system_prompt_demands_a_complete_form_draft(self):
        """The model must draft name/description/keywords/message_template
        even when the description doesn't state them (the form has no useful
        defaults for those), and the template must carry the literal {name}
        placeholder read_form validates."""
        system = prompts.extraction_messages("desc")[0]["content"]
        assert "ALWAYS fill" in system
        for field in ("name:", "description:", "keywords:", "message_template:"):
            assert field in system
        assert "{name}" in system


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

    def test_foreign_placeholder_in_template_is_flagged(self):
        client = _FakeClient(
            [_payload(message_template="Hi {name}, saw your work at {company}!")]
        )
        result = extract_campaign_fields("desc", client)
        # {company} is never substituted at send time — flag for review, but
        # don't rewrite: only the user knows what they meant.
        assert "message_template" in result.flagged_fields
        assert result.data.message_template == "Hi {name}, saw your work at {company}!"

    def test_out_of_range_daily_limit_is_clamped_and_flagged(self):
        client = _FakeClient([_payload(daily_limit=500)])
        result = extract_campaign_fields("desc", client)
        assert result.data.daily_limit == 100
        assert "daily_limit" in result.flagged_fields

    def test_unmentioned_fields_are_not_flagged(self):
        client = _FakeClient([_payload()])
        result = extract_campaign_fields("desc", client)
        assert result.flagged_fields == frozenset()

    def test_noisy_keywords_are_cleaned_and_flagged(self):
        # Regression case from issue #68: a small local model duplicated
        # terms and leaked the location into keywords.
        client = _FakeClient(
            [_payload(keywords="data engineers, Berlin, Berlin", location_text="Berlin")]
        )
        result = extract_campaign_fields("desc", client)
        assert result.data.keywords == "data engineers"
        assert "keywords" in result.flagged_fields

    def test_clean_keywords_are_not_flagged(self):
        client = _FakeClient([_payload(keywords="data engineers, python")])
        result = extract_campaign_fields("desc", client)
        assert result.data.keywords == "data engineers, python"
        assert "keywords" not in result.flagged_fields

    def test_keywords_that_are_pure_noise_collapse_to_none_and_stay_flagged(self):
        client = _FakeClient(
            [_payload(keywords="Berlin, Germany", location_text="Berlin, Germany")]
        )
        result = extract_campaign_fields("desc", client)
        assert result.data.keywords is None
        assert "keywords" in result.flagged_fields


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

    def test_none_content_triggers_repair_then_succeeds(self):
        # A misbehaving client could hand back None instead of raising —
        # must not crash json.loads(None), just trigger the repair retry
        # like any other unparseable response.
        client = _FakeClient([None, _payload(name="Fixed")])
        result = extract_campaign_fields("desc", client)
        assert result.data.name == "Fixed"
        assert result.repaired is True

    def test_none_content_twice_raises_response_error(self):
        client = _FakeClient([None, None])
        with pytest.raises(LLMResponseError):
            extract_campaign_fields("desc", client)


@pytest.mark.unit
class TestParse:
    """Direct coverage of the ``_parse`` helper's input guards."""

    def test_none_raises_value_error(self):
        with pytest.raises(ValueError, match="expected a string response"):
            _parse(None)

    def test_non_string_raises_value_error(self):
        with pytest.raises(ValueError, match="expected a string response"):
            _parse({"already": "a dict"})


@pytest.mark.unit
class TestParseCodeFenceTolerance:
    def test_plain_json_still_parses(self):
        result = _parse(_payload(name="Plain"))
        assert result.name == "Plain"

    def test_json_fenced_with_json_tag(self):
        raw = f"```json\n{_payload(name='Fenced')}\n```"
        result = _parse(raw)
        assert result.name == "Fenced"

    def test_json_fenced_without_tag(self):
        raw = f"```\n{_payload(name='Fenced')}\n```"
        result = _parse(raw)
        assert result.name == "Fenced"

    def test_prose_wrapped_json(self):
        raw = f"Here's what I extracted:\n\n{_payload(name='Prose')}\n\nLet me know!"
        result = _parse(raw)
        assert result.name == "Prose"

    def test_genuinely_broken_input_still_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse("this is not json at all, sorry")

    def test_broken_input_with_unbalanced_braces_still_raises(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _parse("{not actually valid json")


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
