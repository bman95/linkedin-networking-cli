"""Prompt text for the campaign-description extraction pipeline.

Kept separate from control flow (``extraction.py``) so wording can be
iterated on without touching orchestration logic.
"""

from __future__ import annotations

_SYSTEM_PROMPT = (
    "You turn a short, freeform description of a LinkedIn outreach campaign "
    "into structured data. Extract ONLY what the user explicitly says or "
    "clearly implies — leave a field null if it isn't mentioned; never guess "
    "or invent a value. Respond with a single JSON object matching the given "
    "schema and nothing else — no markdown fences, no commentary.\n\n"
    "Field notes:\n"
    "- location_text / industry_text / network_text: the user's own words for "
    "who/where they want to reach (e.g. 'Mexico City', 'software companies', "
    "'people I'm not connected to yet') — do not resolve these to codes or "
    "canonical names yourself.\n"
    "- daily_limit: a plain integer if a number of connections per day is "
    "mentioned; do not invent one from an unrelated number in the text (e.g. "
    "'companies with 100+ employees' is NOT a daily_limit of 100).\n"
    "- message_template: if the user describes a tone or a message, draft one "
    "short, friendly connection note that includes the literal placeholder "
    "{name} for the recipient's first name."
)


def extraction_messages(description: str) -> list[dict[str, str]]:
    """The initial extraction request for a user's campaign description."""
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": description},
    ]


def repair_messages(
    description: str, prior_raw: str, validation_error: str
) -> list[dict[str, str]]:
    """A follow-up request feeding the validation failure back for one retry."""
    messages = extraction_messages(description)
    messages.append({"role": "assistant", "content": prior_raw})
    messages.append(
        {
            "role": "user",
            "content": (
                "That response was not valid JSON matching the schema "
                f"({validation_error}). Reply again with ONLY a corrected JSON "
                "object — no markdown fences, no commentary."
            ),
        }
    )
    return messages
