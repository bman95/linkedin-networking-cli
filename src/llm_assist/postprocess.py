"""Deterministic fixups applied to a parsed :class:`ExtractedCampaign`.

Cheap, rule-based corrections for the two failure modes small models hit most
often on these specific fields — never round-tripped back through the LLM.
"""

from __future__ import annotations

import re

# Common variants a small model emits instead of the literal "{name}" the
# campaign message template validator requires: {first_name}, [name],
# [first_name], {firstname}, {full_name}, [First Name] (case-insensitive,
# optional underscore/space between the two words).
_PLACEHOLDER_RE = re.compile(r"[{\[](?:first[_ ]?|full[_ ]?)?name[}\]]", re.IGNORECASE)


def repair_name_placeholder(template: str | None) -> tuple[str | None, bool]:
    """Fix a recognizable ``{name}``-placeholder variant; flag if it changed.

    ``None`` in -> ``(None, False)``. Already-correct or unrecognizable text
    is returned unchanged (never invents a placeholder that wasn't there) —
    ``read_form``'s own ``{name}`` validator is the final authority either way.
    """
    if template is None:
        return None, False
    if "{name}" in template:
        return template, False
    match = _PLACEHOLDER_RE.search(template)
    if not match:
        return template, False
    repaired = template[: match.start()] + "{name}" + template[match.end() :]
    return repaired, True


# Any brace/bracket placeholder that is not exactly "{name}" — e.g. an
# invented {company} or {startup_name}. Checked after repair_name_placeholder,
# so recognizable {name} variants have already been normalized away.
_FOREIGN_PLACEHOLDER_RE = re.compile(r"[{\[](?!name[}\]])[^{}\[\]]+[}\]]")


def has_foreign_placeholder(template: str | None) -> bool:
    """True when the template carries a placeholder other than ``{name}``.

    Only ``{name}`` is ever substituted at send time; anything else a model
    invents would go out literally in a real connection request, so the
    caller flags the field for the user's review.
    """
    if template is None:
        return False
    return _FOREIGN_PLACEHOLDER_RE.search(template) is not None


def clamp_daily_limit(value: int | None) -> tuple[int | None, bool]:
    """Clamp to the form's [1, 100] bound; flag if clamping changed it."""
    if value is None:
        return None, False
    clamped = max(1, min(100, value))
    return clamped, clamped != value
