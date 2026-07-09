"""Orchestrates the campaign-description -> :class:`ExtractedCampaign` pipeline.

truncate -> schema-constrained ``chat_json`` -> parse/validate -> one
whole-object repair retry -> deterministic post-processing -> fuzzy-match
against the curated vocabularies. See the AI-assisted campaign creation plan
for the reasoning behind each step; no self-consistency voting or multi-call
chains — the mandatory human-review step in the TUI is the real backstop.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import ValidationError

from . import matching, postprocess, prompts
from .client import LLMClient
from .errors import LLMAssistCancelled, LLMResponseError
from .matching import MatchResult
from .schema import EXTRACTION_JSON_SCHEMA, ExtractedCampaign

ProgressFn = Callable[[str], None]
StopFn = Callable[[], bool]


@dataclass(frozen=True)
class ExtractionResult:
    data: ExtractedCampaign
    flagged_fields: frozenset[str]
    location_match: MatchResult
    industry_match: MatchResult
    network_match: MatchResult
    repaired: bool


def extract_campaign_fields(
    description: str,
    client: LLMClient,
    *,
    max_input_chars: int = 4000,
    progress: ProgressFn | None = None,
    should_stop: StopFn | None = None,
) -> ExtractionResult:
    """Run the full reliability stack for one campaign description.

    Raises an ``llm_assist`` error subclass on unrecoverable failure, or
    :class:`LLMAssistCancelled` if ``should_stop`` fired before a call this
    function could still avoid making. A single in-flight blocking HTTP
    request cannot itself be aborted — cancellation is only checked between
    calls (before the first request, and before the repair retry).
    """
    notify = progress or (lambda _msg: None)
    stopped = should_stop or (lambda: False)

    text = description.strip()
    if len(text) > max_input_chars:
        text = text[:max_input_chars]
        notify(f"Using the first {max_input_chars} characters of your description.")

    if stopped():
        raise LLMAssistCancelled("Cancelled before the request was sent.")

    notify("Asking the model to fill in the campaign details…")
    raw = client.chat_json(prompts.extraction_messages(text), EXTRACTION_JSON_SCHEMA)
    parsed, repaired = _parse_with_repair(client, text, raw, notify, stopped)

    message_template, template_flagged = postprocess.repair_name_placeholder(
        parsed.message_template
    )
    daily_limit, daily_limit_flagged = postprocess.clamp_daily_limit(parsed.daily_limit)

    location_match = matching.match_location(parsed.location_text)
    industry_match = matching.match_industry(parsed.industry_text)
    network_match = matching.match_network(parsed.network_text)

    data = parsed.model_copy(
        update={"message_template": message_template, "daily_limit": daily_limit}
    )

    flagged: set[str] = set()
    if template_flagged:
        flagged.add("message_template")
    if daily_limit_flagged:
        flagged.add("daily_limit")
    if location_match.needs_review:
        flagged.add("location")
    if industry_match.needs_review:
        flagged.add("industry")
    if network_match.needs_review:
        flagged.add("network")

    notify("Done.")
    return ExtractionResult(
        data=data,
        flagged_fields=frozenset(flagged),
        location_match=location_match,
        industry_match=industry_match,
        network_match=network_match,
        repaired=repaired,
    )


def _parse_with_repair(
    client: LLMClient,
    description: str,
    raw: str,
    notify: ProgressFn,
    stopped: StopFn,
) -> tuple[ExtractedCampaign, bool]:
    try:
        return _parse(raw), False
    except ValueError as exc:
        if stopped():
            raise LLMAssistCancelled("Cancelled before the repair retry was sent.") from exc
        notify("The response needed a follow-up — asking the model to fix it…")
        repaired_raw = client.chat_json(
            prompts.repair_messages(description, raw, str(exc)), EXTRACTION_JSON_SCHEMA
        )
        try:
            return _parse(repaired_raw), True
        except ValueError as retry_exc:
            raise LLMResponseError(
                "The model's response couldn't be understood, even after asking it to fix it."
            ) from retry_exc


def _parse(raw: str) -> ExtractedCampaign:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"not valid JSON: {exc}") from exc
    try:
        return ExtractedCampaign.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
