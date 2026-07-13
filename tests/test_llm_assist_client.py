"""Unit tests for the stdlib-only OpenAI-compatible LLM client.

Every network call goes through ``urllib.request.urlopen``, mocked here so
these tests need neither a real Ollama daemon nor network access.
"""

import json
import urllib.error
from unittest.mock import patch

import pytest

from llm_assist.client import LLMClient, LLMConfig
from llm_assist.errors import (
    LLMAuthError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    ModelPullError,
)


class _FakeResponse:
    """A minimal stand-in for the context manager ``urlopen`` returns."""

    def __init__(self, body: dict):
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeStream:
    """A minimal stand-in for a streaming NDJSON response, iterable by line."""

    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _config(**overrides) -> LLMConfig:
    defaults = dict(
        base_url="http://localhost:11434",
        api_key=None,
        model="gemma3:1b",
        timeout_s=5,
        pull_timeout_s=30,
        max_tokens=512,
    )
    defaults.update(overrides)
    return LLMConfig(**defaults)


@pytest.mark.unit
class TestChatJson:
    def test_happy_path_returns_raw_content(self):
        client = LLMClient(_config())
        body = {"choices": [{"message": {"content": '{"name": "ok"}'}}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            result = client.chat_json([{"role": "user", "content": "x"}], {})
        assert result == '{"name": "ok"}'

    def test_401_maps_to_auth_error(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 401, "unauthorized", {}, None),
        ):
            with pytest.raises(LLMAuthError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_403_maps_to_auth_error(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 403, "forbidden", {}, None),
        ):
            with pytest.raises(LLMAuthError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_404_maps_to_model_not_found(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "not found", {}, None),
        ):
            with pytest.raises(ModelNotFoundError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_connection_refused_maps_to_unavailable(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError()),
        ):
            with pytest.raises(LLMUnavailableError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_timeout_maps_to_timeout_error(self):
        client = LLMClient(_config())
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with pytest.raises(LLMTimeoutError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_400_then_200_retries_without_schema(self):
        client = LLMClient(_config())
        good_body = {"choices": [{"message": {"content": '{"name": "ok"}'}}]}
        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(json.loads(request.data))
            if len(calls) == 1:
                raise urllib.error.HTTPError("u", 400, "schema not supported", {}, None)
            return _FakeResponse(good_body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = client.chat_json([{"role": "user", "content": "x"}], {"type": "object"})

        assert result == '{"name": "ok"}'
        assert len(calls) == 2
        assert "response_format" in calls[0]
        assert "response_format" not in calls[1]

    def test_400_twice_raises_response_error(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 400, "bad request", {}, None),
        ):
            with pytest.raises(LLMResponseError):
                client.chat_json([{"role": "user", "content": "x"}], {"type": "object"})

    def test_non_json_body_raises_response_error(self):
        client = LLMClient(_config())

        class _BadResponse(_FakeResponse):
            def read(self):
                return b"<html>not json</html>"

        with patch(
            "urllib.request.urlopen", return_value=_BadResponse({"ignored": True})
        ):
            with pytest.raises(LLMResponseError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_missing_expected_fields_raises_response_error(self):
        client = LLMClient(_config())
        with patch("urllib.request.urlopen", return_value=_FakeResponse({"choices": []})):
            with pytest.raises(LLMResponseError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_null_content_raises_response_error(self):
        # A provider can return "content": null instead of omitting the
        # field entirely — must not reach json.loads(None) downstream.
        client = LLMClient(_config())
        body = {"choices": [{"message": {"content": None}}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            with pytest.raises(LLMResponseError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_non_string_content_raises_response_error(self):
        client = LLMClient(_config())
        body = {"choices": [{"message": {"content": {"unexpected": "object"}}}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            with pytest.raises(LLMResponseError):
                client.chat_json([{"role": "user", "content": "x"}], {})

    def test_api_key_sent_as_bearer_header(self):
        client = LLMClient(_config(api_key="sk-test"))
        body = {"choices": [{"message": {"content": "{}"}}]}
        captured = {}

        def fake_urlopen(request, timeout=None):
            captured["auth"] = request.get_header("Authorization")
            return _FakeResponse(body)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client.chat_json([{"role": "user", "content": "x"}], {})

        assert captured["auth"] == "Bearer sk-test"


@pytest.mark.unit
class TestListModels:
    def test_returns_model_names(self):
        client = LLMClient(_config())
        body = {"models": [{"name": "gemma3:1b"}, {"name": "gemma3:4b"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.list_models() == ["gemma3:1b", "gemma3:4b"]

    def test_unreachable_raises_unavailable(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionRefusedError()),
        ):
            with pytest.raises(LLMUnavailableError):
                client.list_models()


@pytest.mark.unit
class TestIsModelAvailable:
    def test_exact_match(self):
        client = LLMClient(_config())
        body = {"models": [{"name": "gemma3:latest"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.is_model_available("gemma3:latest") is True

    def test_untagged_config_matches_latest_tag(self):
        # LLM_MODEL=gemma3 while Ollama reports "gemma3:latest" — must not
        # read as "not installed" and arm a needless re-pull.
        client = LLMClient(_config())
        body = {"models": [{"name": "gemma3:latest"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.is_model_available("gemma3") is True

    def test_untagged_config_matches_any_installed_tag(self):
        client = LLMClient(_config())
        body = {"models": [{"name": "gemma3:4b"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.is_model_available("gemma3") is True

    def test_tagged_config_does_not_match_a_different_tag(self):
        client = LLMClient(_config())
        body = {"models": [{"name": "gemma3:1b"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.is_model_available("gemma3:4b") is False

    def test_not_installed_at_all(self):
        client = LLMClient(_config())
        body = {"models": [{"name": "llama3:latest"}]}
        with patch("urllib.request.urlopen", return_value=_FakeResponse(body)):
            assert client.is_model_available("gemma3") is False


@pytest.mark.unit
class TestPullModel:
    def test_streams_progress_events(self):
        client = LLMClient(_config())
        lines = [
            json.dumps({"status": "pulling manifest"}).encode(),
            json.dumps({"status": "downloading", "completed": 50, "total": 100}).encode(),
        ]
        events = []
        with patch("urllib.request.urlopen", return_value=_FakeStream(lines)):
            client.pull_model("gemma3:1b", on_progress=events.append, should_stop=lambda: False)
        assert events == [
            {"status": "pulling manifest"},
            {"status": "downloading", "completed": 50, "total": 100},
        ]

    def test_error_line_raises_model_pull_error(self):
        client = LLMClient(_config())
        lines = [json.dumps({"error": "no space left on device"}).encode()]
        with patch("urllib.request.urlopen", return_value=_FakeStream(lines)):
            with pytest.raises(ModelPullError, match="no space left"):
                client.pull_model("gemma3:1b", on_progress=lambda e: None, should_stop=lambda: False)

    def test_should_stop_ends_the_stream_early_without_raising(self):
        client = LLMClient(_config())
        lines = [json.dumps({"status": "a"}).encode(), json.dumps({"status": "b"}).encode()]
        events = []
        with patch("urllib.request.urlopen", return_value=_FakeStream(lines)):
            client.pull_model("gemma3:1b", on_progress=events.append, should_stop=lambda: True)
        assert events == []

    def test_blank_lines_are_skipped(self):
        client = LLMClient(_config())
        lines = [b"", json.dumps({"status": "ok"}).encode(), b"   "]
        events = []
        with patch("urllib.request.urlopen", return_value=_FakeStream(lines)):
            client.pull_model("gemma3:1b", on_progress=events.append, should_stop=lambda: False)
        assert events == [{"status": "ok"}]

    def test_unparseable_line_is_skipped_not_raised(self):
        client = LLMClient(_config())
        lines = [b"not json", json.dumps({"status": "ok"}).encode()]
        events = []
        with patch("urllib.request.urlopen", return_value=_FakeStream(lines)):
            client.pull_model("gemma3:1b", on_progress=events.append, should_stop=lambda: False)
        assert events == [{"status": "ok"}]

    def test_unexpected_404_maps_to_pull_error(self):
        # Ollama reports an unknown model as a body-level "error" NDJSON line
        # (see test_error_line_raises_model_pull_error), not an HTTP 404 — an
        # HTTP-level 404 on /api/pull itself means a misconfigured endpoint.
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 404, "not found", {}, None),
        ):
            with pytest.raises(ModelPullError):
                client.pull_model("gemma3:1b", on_progress=lambda e: None, should_stop=lambda: False)

    def test_disk_space_http_error_maps_to_pull_error(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.HTTPError("u", 500, "server error", {}, None),
        ):
            with pytest.raises(ModelPullError):
                client.pull_model("gemma3:1b", on_progress=lambda e: None, should_stop=lambda: False)

    def test_connection_drop_maps_to_unavailable(self):
        client = LLMClient(_config())
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError(ConnectionResetError()),
        ):
            with pytest.raises(LLMUnavailableError):
                client.pull_model("gemma3:1b", on_progress=lambda e: None, should_stop=lambda: False)
