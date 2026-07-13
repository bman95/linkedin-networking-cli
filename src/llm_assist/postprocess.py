"""Deterministic fixups applied to a parsed :class:`ExtractedCampaign`.

Cheap, rule-based corrections for the failure modes small models hit most
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


def clean_keywords(
    keywords: str | None, location_text: str | None = None
) -> tuple[str | None, bool]:
    """Deterministically clean a comma-separated keywords string.

    Small local models routinely return noisy keyword lists despite the
    prompt's explicit instructions: exact-or-recased duplicates, stray
    whitespace/empty entries, and terms that just repeat ``location_text``
    (the prompt already tells the model "not locations — location_text
    covers those", but 1-4B models ignore it). None of this needs another
    model call: split on commas, trim, drop empties, case-insensitively
    dedupe keeping the first occurrence, then drop any term that echoes
    ``location_text`` — a single one of its words ("Berlin"), one of its
    comma-separated segments ("New York"), or the whole phrase copied
    verbatim ("San Francisco Bay Area"). The single-word check can
    false-positive on a legitimate keyword that happens to equal a location
    word; the mandatory review step (the field is flagged whenever cleanup
    changed anything) is the backstop for that inherent tradeoff. Returns
    the rejoined string (``None`` if nothing survives) and whether the
    input actually changed.
    """
    if keywords is None:
        return None, False

    location = (location_text or "").lower()
    # Single words ("berlin", "germany") …
    location_terms = {token for token in re.split(r"\W+", location) if token}
    # … comma-separated segments ("new york") …
    location_terms.update(
        segment for segment in (" ".join(s.split()) for s in location.split(",")) if segment
    )
    # … and the whole phrase, punctuation-normalized ("san francisco bay area").
    whole = " ".join(token for token in re.split(r"\W+", location) if token)
    if whole:
        location_terms.add(whole)

    seen: set[str] = set()
    cleaned: list[str] = []
    for raw_term in keywords.split(","):
        term = " ".join(raw_term.split())
        if not term:
            continue
        key = term.lower()
        if key in seen or key in location_terms:
            continue
        # Punctuation-normalized only for the location check ("Berlin -
        # Germany" still matches the whole phrase); the dedupe key above
        # stays exact so "C++" never collides with "C".
        if " ".join(token for token in re.split(r"\W+", key) if token) in location_terms:
            continue
        seen.add(key)
        cleaned.append(term)

    result = ", ".join(cleaned) if cleaned else None
    return result, result != keywords
