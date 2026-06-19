"""Best-effort diagnostics capture for the LinkedIn automation.

Whenever the automation hits a fatal failure or a notable anomaly we want a
consistent *evidence bundle* on disk: a viewport screenshot, a DOM snapshot,
and one structured log line. When LinkedIn changes its layout (or anything
else breaks) this bundle is what makes a postmortem possible.

Design rules:

- **Capture functions never raise.** They run on the error path, so they must
  not mask the original exception. Screenshot and DOM dump are wrapped
  individually; if both fail (crashed/closed page) we still emit the log line.
- **Bounded disk cost.** Anomaly captures are rate-limited per run, and the
  rolling page snapshots use a fixed-size ring buffer.

Modeled on the LinkedIn Worker project's ``agent/src/browser/diagnostics.py``.

All capture functions are async and operate on an async Playwright ``Page``.
"""

import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.path.append(str(Path(__file__).parent.parent))

from utils.logging import get_logger

logger = get_logger(__name__)

# Viewport-only screenshot on the error path: a full_page shot of a huge or
# crashed page can hang or balloon, so cap it tight.
_SCREENSHOT_TIMEOUT_MS = 5_000

# Ring-buffer size for landed-page snapshots.
_PAGE_RING_SIZE = 10

# Max anomaly captures per run, so a repeating banner can't flood the dir.
_MAX_ANOMALY_CAPTURES = 8

# Mutable run-scoped counter for anomaly rate limiting.
_anomaly_capture_count = 0


def _artifacts_dir() -> Path:
    """Resolve the artifacts directory, creating it on demand.

    Honors ``LINKEDIN_CLI_ARTIFACTS_DIR`` so tests can redirect writes away
    from the real home directory; otherwise sits alongside ``logs/`` and
    ``browser_data/`` under the app dir.
    """
    override = os.getenv("LINKEDIN_CLI_ARTIFACTS_DIR")
    base = (
        Path(override)
        if override
        else Path.home() / ".linkedin-networking-cli" / "artifacts"
    )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _slugify(name: str, *, max_len: int = 40) -> str:
    """Turn an arbitrary step name into a filesystem-safe slug."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", (name or "").strip().lower())
    slug = slug.strip("_")
    if not slug:
        slug = "unnamed"
    return slug[:max_len].strip("_") or "unnamed"


def _timestamp() -> str:
    """Local timestamp suffix for artifact filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


async def _safe_text(coro_factory, default: str = "") -> str:
    """Await a page accessor, swallowing any error (best-effort metadata).

    ``coro_factory`` is a zero-arg callable so the *attribute access* it
    performs (e.g. ``page.title``) is also inside the guard — on a closed
    page that access itself can raise.
    """
    try:
        return await coro_factory()
    except Exception:
        return default


async def _safe_url(page) -> str:
    """Read ``page.url`` defensively. On a closed page the access raises."""
    try:
        return str(page.url)
    except Exception:
        return ""


def _safe_repr(value: Any) -> str:
    """``repr`` that can never raise (a value's ``__repr__`` may throw)."""
    try:
        return repr(value)
    except Exception:
        return "<unrepresentable>"


async def _capture_screenshot(page, path: Path) -> bool:
    """Write a viewport screenshot. Returns True on success, never raises."""
    try:
        await page.screenshot(
            path=str(path), full_page=False, timeout=_SCREENSHOT_TIMEOUT_MS
        )
        return True
    except Exception as exc:
        logger.debug("Screenshot capture failed for %s: %s", path.name, exc)
        return False


async def _capture_dom(page, path: Path) -> bool:
    """Write the current DOM to ``path``. Returns True on success, never raises."""
    try:
        html = await page.content()
        path.write_text(html, encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug("DOM capture failed for %s: %s", path.name, exc)
        return False


async def _capture_bundle(
    page,
    prefix: str,
    name: str,
    severity: int,
    *,
    exc: Optional[BaseException] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Capture screenshot + DOM + structured log line. Never raises.

    Returns a dict describing what was written (artifact paths, captured
    metadata) so callers/tests can introspect the bundle.
    """
    slug = _slugify(name)
    ts = _timestamp()
    context = context or {}

    try:
        base_dir = _artifacts_dir()
    except Exception as dir_exc:
        # If we cannot even create the directory, still emit the log line so
        # the failure is never silent.
        logger.log(
            severity,
            "diagnostics bundle could not be written (artifacts dir unavailable): %s",
            dir_exc,
            extra={"diagnostics_step": name, "diagnostics_slug": slug},
        )
        return {"screenshot": None, "dom": None, "screenshot_ok": False, "dom_ok": False}

    png_path = base_dir / f"{prefix}_{slug}_{ts}.png"
    html_path = base_dir / f"{prefix}_{slug}_{ts}.html"

    # Best-effort page metadata. Each accessor is fully guarded, including the
    # attribute access itself (e.g. ``page.title``) — on a closed page even
    # touching the attribute can raise.
    url = await _safe_url(page)
    title = await _safe_text(lambda: page.title(), "")

    screenshot_ok = await _capture_screenshot(page, png_path)
    dom_ok = await _capture_dom(page, html_path)

    exc_type = type(exc).__name__ if exc is not None else None
    exc_msg = _safe_repr(exc) if exc is not None else None

    artifacts = {
        "screenshot": str(png_path) if screenshot_ok else None,
        "dom": str(html_path) if dom_ok else None,
        "screenshot_ok": screenshot_ok,
        "dom_ok": dom_ok,
    }

    extra = {
        "diagnostics_step": name,
        "diagnostics_slug": slug,
        "diagnostics_url": url,
        "diagnostics_title": title,
        "diagnostics_exc_type": exc_type,
        "diagnostics_exc_msg": exc_msg,
        "diagnostics_screenshot": artifacts["screenshot"],
        "diagnostics_dom": artifacts["dom"],
        "diagnostics_profile_url": context.get("profile_url"),
        "diagnostics_campaign": context.get("campaign"),
    }

    # One clearly-parseable line carrying all the salient context. Every
    # interpolation uses _safe_repr so a value with a throwing __repr__ can
    # never derail the log emission below.
    parts = [
        f"step={name}",
        f"url={_safe_repr(url)}",
        f"title={_safe_repr(title)}",
        f"exc_type={exc_type}",
        f"exc_msg={exc_msg}",
        f"screenshot={artifacts['screenshot']}",
        f"dom={artifacts['dom']}",
    ]
    if context.get("profile_url"):
        parts.append(f"profile_url={_safe_repr(context['profile_url'])}")
    if context.get("campaign"):
        parts.append(f"campaign={_safe_repr(context['campaign'])}")
    # Surface any additional context keys without overwriting the structured ones.
    for key, value in context.items():
        if key in ("profile_url", "campaign"):
            continue
        parts.append(f"{key}={_safe_repr(value)}")
        extra[f"diagnostics_{key}"] = value

    # The structured line is the one thing that must always be emitted, even
    # if both captures failed on a crashed page. Guard it so message assembly
    # can never swallow it.
    try:
        logger.log(severity, "DIAGNOSTICS %s | %s", prefix, " ".join(parts), extra=extra)
    except Exception as log_exc:  # pragma: no cover - defensive backstop
        logger.log(severity, "DIAGNOSTICS %s | step=%s (log assembly failed: %s)",
                   prefix, name, log_exc)

    return artifacts


async def capture_error_context(
    page,
    name: str,
    *,
    exc: Optional[BaseException] = None,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Capture an evidence bundle for a fatal failure (logged at ERROR).

    Best-effort: never raises, even on a crashed or closed page. Intended to
    be called immediately before re-raising the original exception so the
    caller's error path is unchanged.
    """
    try:
        return await _capture_bundle(
            page, "error", name, logging.ERROR, exc=exc, context=context
        )
    except Exception as capture_exc:  # pragma: no cover - defensive backstop
        logger.error(
            "capture_error_context failed for %s: %s", name, capture_exc
        )
        return {"screenshot": None, "dom": None, "screenshot_ok": False, "dom_ok": False}


async def capture_anomaly_context(
    page,
    name: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Capture an evidence bundle for a non-fatal anomaly (logged at WARNING).

    Rate-limited to ``_MAX_ANOMALY_CAPTURES`` per run so a repeating banner
    cannot flood the artifacts directory. Returns ``None`` once the cap is
    reached. Best-effort: never raises.
    """
    global _anomaly_capture_count
    if _anomaly_capture_count >= _MAX_ANOMALY_CAPTURES:
        logger.warning(
            "Anomaly capture rate limit reached (%d); skipping bundle for %s",
            _MAX_ANOMALY_CAPTURES,
            name,
        )
        return None
    _anomaly_capture_count += 1

    try:
        return await _capture_bundle(
            page, "anomaly", name, logging.WARNING, context=context
        )
    except Exception as capture_exc:  # pragma: no cover - defensive backstop
        logger.warning(
            "capture_anomaly_context failed for %s: %s", name, capture_exc
        )
        return {"screenshot": None, "dom": None, "screenshot_ok": False, "dom_ok": False}


async def snapshot_page(page, seq: int) -> Optional[str]:
    """Record a landed page into the rolling ring buffer. Never raises.

    Writes ``artifacts/pages/page_<slot>.png`` plus a ``.txt`` sidecar holding
    the ISO timestamp and URL, where ``slot = seq % _PAGE_RING_SIZE``. Bounded
    disk cost; lets a postmortem see how the session reached the failure.

    Returns the screenshot path on success, else ``None``.
    """
    try:
        slot = seq % _PAGE_RING_SIZE
        pages_dir = _artifacts_dir() / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        png_path = pages_dir / f"page_{slot}.png"
        txt_path = pages_dir / f"page_{slot}.txt"

        url = await _safe_url(page)
        sidecar = f"{datetime.now(timezone.utc).isoformat()}\n{url}\n"
        try:
            txt_path.write_text(sidecar, encoding="utf-8")
        except Exception as exc:
            logger.debug("Page snapshot sidecar failed for slot %d: %s", slot, exc)

        ok = await _capture_screenshot(page, png_path)
        return str(png_path) if ok else None
    except Exception as exc:  # pragma: no cover - defensive backstop
        logger.debug("snapshot_page failed for seq %s: %s", seq, exc)
        return None


def reset_anomaly_rate_limit() -> None:
    """Reset the per-run anomaly capture counter (call at the start of a run)."""
    global _anomaly_capture_count
    _anomaly_capture_count = 0
