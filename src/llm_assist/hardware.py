"""Advisory-only local-model recommendation from host RAM.

Never authoritative: the caller always presents this as a pre-selected
default in a picker the user can override, and nothing is pulled or selected
without an explicit confirmation. No GPU detection — Ollama already does its
own GPU/Metal offload better than an external heuristic could, and on Apple
Silicon unified memory means RAM already captures the most GPU-relevant
consumer platform.
"""

from __future__ import annotations

import psutil

from utils.logging import get_logger

logger = get_logger(__name__)

#: Curated small-model tiers offered in the local-mode model picker, smallest
#: first. :func:`recommend_model` pre-selects one of these by host RAM.
RECOMMENDED_MODELS: tuple[str, ...] = ("gemma3:1b", "gemma3:4b")

_LOW_RAM_MODEL = RECOMMENDED_MODELS[0]
_HIGH_RAM_MODEL = RECOMMENDED_MODELS[1]
_HIGH_RAM_THRESHOLD_BYTES = 12 * 1024**3  # 12 GB


def recommend_model() -> str:
    """The RAM-appropriate default model tag; the low tier if RAM can't be read."""
    try:
        total = psutil.virtual_memory().total
    except Exception:
        logger.warning(
            "Could not read host memory; defaulting to %s", _LOW_RAM_MODEL, exc_info=True
        )
        return _LOW_RAM_MODEL
    return _HIGH_RAM_MODEL if total >= _HIGH_RAM_THRESHOLD_BYTES else _LOW_RAM_MODEL
