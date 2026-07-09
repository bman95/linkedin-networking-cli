"""AI-assisted campaign creation: parse a plain-language description into
campaign fields for the user to review before saving.

Pure Python, zero Textual imports — the whole surface here is testable by
feeding it canned JSON strings, no model or network required. Only
``src/tui/screens/campaign_ai_assist.py`` imports this package into the UI.
"""

from .client import LLMClient, LLMConfig
from .errors import (
    LLMAssistCancelled,
    LLMAssistError,
    LLMAuthError,
    LLMResponseError,
    LLMTimeoutError,
    LLMUnavailableError,
    ModelNotFoundError,
    ModelPullError,
)
from .extraction import ExtractionResult, extract_campaign_fields
from .hardware import RECOMMENDED_MODELS, recommend_model
from .matching import MatchResult, match_industry, match_location, match_network
from .schema import EXTRACTION_JSON_SCHEMA, ExtractedCampaign

__all__ = [
    "EXTRACTION_JSON_SCHEMA",
    "RECOMMENDED_MODELS",
    "ExtractedCampaign",
    "ExtractionResult",
    "LLMAssistCancelled",
    "LLMAssistError",
    "LLMAuthError",
    "LLMClient",
    "LLMConfig",
    "LLMResponseError",
    "LLMTimeoutError",
    "LLMUnavailableError",
    "MatchResult",
    "ModelNotFoundError",
    "ModelPullError",
    "extract_campaign_fields",
    "match_industry",
    "match_location",
    "match_network",
    "recommend_model",
]
