"""The flat, fully-nullable extraction target for the campaign-description parser.

The model is asked to draft a *complete* form: ``name``, ``description``,
``keywords``, and ``message_template`` are always written from the
description (see ``prompts.py``), while the targeting/limit fields
(``location_text``/``industry_text``/``network_text``/``daily_limit``) are
extraction-only — ``None`` means "not mentioned," and the form's own safe
defaults (Any / Any / 1st+2nd / 20) are the fallback, never a model guess.
Every field stays nullable in the schema regardless, so a weak model that
skips a required draft still validates and simply leaves that form field
untouched. Resolving ``location_text``/``industry_text``/``network_text`` to
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

# OpenAI strict mode requires every property to be listed in "required";
# optionality is expressed via each property's own nullable (anyOf-null)
# type, not by omission from this list — every field here is still
# ``None``-able per the model above.
EXTRACTION_JSON_SCHEMA["required"] = sorted(EXTRACTION_JSON_SCHEMA["properties"].keys())
