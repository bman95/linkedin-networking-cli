"""Fuzzy-match free-text LLM mentions against the app's curated vocabularies.

Location and industry are closed vocabularies backed by real LinkedIn-internal
codes (geoUrn / industry ids) — the model is never trusted to invent these
directly. It reports what the user said in its own words; this module
resolves that to one of the existing curated display names (the same ones
``src/tui/screens/campaign_form.py`` already offers), or reports "no good
match" so the caller can leave the field at its safe default and flag it for
review instead of guessing.

Matching policy, in order:
1. Exact match, case-insensitive — the only confident outcome
   (``needs_review=False``). The model echoed a curated name verbatim.
2. Unambiguous word-boundary containment — accepted, but ALWAYS flagged for
   review. It's usually right, but it's still an inference (e.g. "India"
   picking a single Indian city, or "2nd degree" picking the one network
   option that happens to mention it), not something the model stated
   verbatim.
3. difflib ratio above ``_MATCH_CUTOFF`` — accepted, but ALWAYS flagged for
   review, same reasoning as containment. The cutoff is deliberately high
   (0.75): a low cutoff lets unrelated candidates (e.g. "government" ~0.61
   "Entertainment") through as confident matches.
4. Below cutoff — no match; the field is left unset and flagged.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass

from automation.linkedin_mappings import (
    get_industry_display_names,
    get_location_display_names,
    get_network_display_names,
)

_ANY = "Any"
_MATCH_CUTOFF = 0.75


@dataclass(frozen=True)
class MatchResult:
    """The outcome of matching one free-text mention against a curated list.

    ``matched_display_name`` is set only on a good match. ``raw_text`` carries
    the original mention whenever there was one (matched or not) so an
    unmatched mention can still be shown to the user as a hint.
    ``needs_review`` is True only when there was a mention that failed to
    resolve — an absent mention is not something to review, it's just unset.
    """

    matched_display_name: str | None
    raw_text: str | None
    needs_review: bool


def _match(raw_text: str | None, candidates: list[str]) -> MatchResult:
    if raw_text is None or not raw_text.strip():
        return MatchResult(None, None, False)
    text = raw_text.strip()
    text_lower = text.lower()

    # Exact match, case-insensitive: the model echoed a curated name
    # verbatim — the only outcome trusted without a review flag.
    for candidate in candidates:
        if text_lower == candidate.lower():
            return MatchResult(candidate, text, False)

    # Word-boundary containment: a short colloquial mention ("Mexico City",
    # "Boston") is very often a whole-word span of a longer curated name
    # ("Mexico City Metropolitan Area", "Greater Boston Area") — the
    # dominant pattern in this app's location vocabulary. difflib's ratio
    # penalizes exactly this case (it's length-sensitive: "mexico city" vs
    # "mexico city metropolitan area" scores 0.55, just under cutoff, even
    # though it's a perfect match) — caught via live testing against a real
    # local model. Only trust an UNAMBIGUOUS containment match; if more than
    # one candidate contains it, fall through to ratio matching instead of
    # guessing (never silently pick wrong). Still an inference, not a
    # verbatim match, so it's always flagged for review.
    contained = [c for c in candidates if _word_containment(text_lower, c.lower())]
    if len(contained) == 1:
        return MatchResult(contained[0], text, True)

    # difflib's ratio is also case-sensitive (e.g. "software" vs "Computer
    # Software" scores 0.56 on casing alone, below the cutoff) — compare
    # lowercased, but report back the curated list's original casing. An
    # accepted ratio match is always flagged for review too.
    lowered_to_candidate = {candidate.lower(): candidate for candidate in candidates}
    best = difflib.get_close_matches(
        text_lower, list(lowered_to_candidate.keys()), n=1, cutoff=_MATCH_CUTOFF
    )
    if best:
        return MatchResult(lowered_to_candidate[best[0]], text, True)
    return MatchResult(None, text, True)


def _word_containment(short: str, long: str) -> bool:
    """Is ``short``'s word sequence a contiguous run within ``long``'s (or
    vice versa, for a model that over-specifies)?

    Word-boundary, not character-substring — "us" must never match inside
    "austin". Both inputs are pre-lowercased by the caller.
    """
    if short == long:
        return True
    short_words, long_words = short.split(), long.split()
    if not short_words or not long_words:
        return False
    small, big = (
        (short_words, long_words)
        if len(short_words) <= len(long_words)
        else (long_words, short_words)
    )
    span = len(small)
    return any(big[start : start + span] == small for start in range(len(big) - span + 1))


def match_location(raw_text: str | None) -> MatchResult:
    candidates = [name for name in get_location_display_names() if name != _ANY]
    return _match(raw_text, candidates)


def match_industry(raw_text: str | None) -> MatchResult:
    candidates = [name for name in get_industry_display_names() if name != _ANY]
    return _match(raw_text, candidates)


def match_network(raw_text: str | None) -> MatchResult:
    return _match(raw_text, get_network_display_names())
