"""Minimal OpenAI-compatible chat-completions client (stdlib HTTP only).

Serves both local Ollama (which speaks this same protocol at
``{base_url}/v1/chat/completions``, plus its own ``/api/tags`` and
``/api/pull``) and any hosted OpenAI-compatible endpoint — one client, two
configurations, no provider SDK dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .errors import (
    LLMAssistError,
    LLMAuthError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    ModelPullError,
)


class _BadRequestError(LLMResponseError):
    """Internal: HTTP 400 specifically, so ``chat_json`` can retry once

    without the schema hint. A subclass of the public ``LLMResponseError``,
    so if it ever escaped uncaught it would still be semantically correct.
    """


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    api_key: str | None
    model: str
    timeout_s: int
    pull_timeout_s: int
    max_tokens: int


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    # ── chat / extraction ────────────────────────────────────────────────

    def chat_json(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any],
        *,
        temperature: float = 0.0,
    ) -> str:
        """Return the assistant's raw message content (not yet parsed).

        Tries schema-constrained decoding first (Ollama's ``format`` /
        OpenAI-style ``response_format``); if the server 400s on that
        parameter, retries once without it.
        """
        try:
            return self._chat_call(self._chat_payload(messages, temperature, json_schema))
        except _BadRequestError:
            try:
                return self._chat_call(self._chat_payload(messages, temperature, None))
            except _BadRequestError as exc:
                raise LLMResponseError(
                    "The endpoint rejected the request even without a schema hint."
                ) from exc

    def _chat_payload(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": self._config.max_tokens,
        }
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extracted_campaign",
                    "schema": json_schema,
                    "strict": True,
                },
            }
        return payload

    def _chat_call(self, payload: dict[str, Any]) -> str:
        body = self._post("/v1/chat/completions", payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMResponseError(
                "The endpoint's response was missing the expected fields."
            ) from exc
        # A provider can return "content": null (or a non-string value) —
        # present, but not usable. Treat that the same as a missing field
        # rather than letting a bad type reach json.loads() downstream.
        if not isinstance(content, str):
            raise LLMResponseError(
                "The endpoint's response was missing the expected fields."
            )
        return content

    # ── model listing (local/Ollama only) ───────────────────────────────

    def list_models(self) -> list[str]:
        """Installed Ollama model tags."""
        body = self._get("/api/tags")
        try:
            return [entry["name"] for entry in body.get("models", [])]
        except (KeyError, TypeError) as exc:
            raise LLMResponseError("Couldn't read the model list from the endpoint.") from exc

    def is_model_available(self, model: str) -> bool:
        """Whether ``model`` is installed, tolerating Ollama's ``name:tag`` form.

        ``/api/tags`` always returns fully qualified entries (e.g.
        ``gemma3:latest``), but callers commonly configure an untagged model
        name (``LLM_MODEL=gemma3``, or the *Custom…* input). Without this,
        an untagged config reads as "not installed" and arms a multi-gigabyte
        re-pull of a model Ollama would already resolve and serve.

        Mirrors Ollama's own resolution rule exactly: an untagged name
        resolves strictly to ``:latest`` — an installed sibling tag (e.g.
        ``gemma3:4b``) does *not* make untagged ``gemma3`` servable, so it
        must not count as available here (it would skip the pull flow and
        dead-end in a 404 at chat time).
        """
        available = self.list_models()
        if model in available:
            return True
        if ":" in model:
            return False
        return f"{model}:latest" in available

    # ── model pull (local/Ollama only) ──────────────────────────────────

    def pull_model(
        self,
        model: str,
        on_progress: Callable[[dict[str, Any]], None],
        should_stop: Callable[[], bool],
    ) -> None:
        """Stream an ``ollama pull``; cooperative cancel via ``should_stop()``.

        Each NDJSON line is handed to ``on_progress``. A line carrying an
        ``"error"`` key (e.g. disk space exhausted) raises
        :class:`ModelPullError` — Ollama reports pull failures as an HTTP 200
        stream with an error line, not an HTTP error status.
        """
        url = f"{self._config.base_url}/api/pull"
        data = json.dumps({"model": model, "stream": True}).encode("utf-8")
        request = urllib.request.Request(
            url, data=data, headers=self._headers(), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._config.pull_timeout_s) as response:
                for raw_line in response:
                    if should_stop():
                        return
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in event:
                        raise ModelPullError(str(event["error"]))
                    on_progress(event)
        except ModelPullError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise LLMAuthError(
                    f"The endpoint rejected the pull as unauthorized ({exc.code})."
                ) from exc
            raise ModelPullError(f"Pull failed ({exc.code}: {exc.reason}).") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMTimeoutError("The pull request timed out.") from exc
            raise LLMUnavailableError(f"Could not reach {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise LLMTimeoutError("The pull request timed out.") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"Could not reach {url}: {exc}") from exc

    # ── HTTP plumbing ────────────────────────────────────────────────────

    def _get(self, path: str) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self._config.base_url}{path}", headers=self._headers()
        )
        return self._send(request)

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self._config.base_url}{path}", data=data, headers=self._headers(), method="POST"
        )
        return self._send(request)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config.api_key:
            headers["Authorization"] = f"Bearer {self._config.api_key}"
        return headers

    def _send(self, request: urllib.request.Request) -> dict[str, Any]:
        try:
            with urllib.request.urlopen(request, timeout=self._config.timeout_s) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise self._map_http_error(exc.code, exc.reason) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise LLMTimeoutError(f"The request to {request.full_url} timed out.") from exc
            raise LLMUnavailableError(
                f"Could not reach {request.full_url}: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise LLMTimeoutError(f"The request to {request.full_url} timed out.") from exc
        except OSError as exc:
            raise LLMUnavailableError(f"Could not reach {request.full_url}: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise LLMResponseError("The endpoint didn't return a valid JSON response.") from exc

    @staticmethod
    def _map_http_error(code: int, reason: str) -> LLMAssistError:
        if code in (401, 403):
            return LLMAuthError(f"The endpoint rejected the request as unauthorized ({code}).")
        if code == 404:
            return ModelNotFoundError("The configured model was not found on the endpoint.")
        if code == 400:
            return _BadRequestError(f"The endpoint rejected the request ({code}: {reason}).")
        return LLMUnavailableError(f"The endpoint returned an error ({code}: {reason}).")
