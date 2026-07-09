"""Typed errors for the AI-assisted campaign creation feature.

Every LLM/Ollama failure the TUI can hit maps to exactly one of these, so the
screen layer can show one specific, friendly message instead of a raw
traceback (see ``src/tui/screens/campaign_ai_assist.py``).
"""


class LLMAssistError(Exception):
    """Base exception for the llm_assist package."""


class LLMUnavailableError(LLMAssistError):
    """The endpoint could not be reached at all (connection refused, DNS, host down)."""


class LLMTimeoutError(LLMAssistError):
    """The request exceeded its configured timeout."""


class LLMAuthError(LLMAssistError):
    """The endpoint rejected the request as unauthenticated/unauthorized (401/403)."""


class ModelNotFoundError(LLMAssistError):
    """The configured model is not available on the endpoint (local: not pulled)."""


class LLMResponseError(LLMAssistError):
    """The endpoint's response could not be parsed into valid, schema-matching JSON."""


class ModelPullError(LLMAssistError):
    """A local model pull failed or was rejected by the server mid-stream."""


class LLMAssistCancelled(Exception):
    """A ``should_stop()`` callback fired — a user-requested cancel, not a failure.

    Deliberately NOT an :class:`LLMAssistError` subclass: callers that catch
    the error hierarchy to show a red "failed" status must not accidentally
    swallow a cancel, which the UI shows as a neutral message instead.
    """
