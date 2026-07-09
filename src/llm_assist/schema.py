"""The flat, fully-nullable extraction target for the campaign-description parser.

Every field is optional: ``None`` means "not mentioned in the user's
description," never a guess — the form's own defaults are the fallback, not
the model. Resolving ``location_text``/``industry_text``/``network_text`` to
one of the app's curated, LinkedIn-real vocabularies is ``matching.py``'s
job; the model is never trusted to invent a geoUrn or industry id directly.

Kept flat (no nesting) and small (8 fields) deliberately — small local models
degrade sharply on nested schemas.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ExtractedCampaign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    description: str | None = None
    keywords: str | None = None
    location_text: str | None = None
    industry_text: str | None = None
    network_text: str | None = None
    daily_limit: int | None = None
    message_template: str | None = None


#: JSON schema payload for schema-constrained decoding (Ollama's ``format`` /
#: OpenAI-style ``response_format``). Computed once at import time.
EXTRACTION_JSON_SCHEMA: dict[str, Any] = ExtractedCampaign.model_json_schema()
