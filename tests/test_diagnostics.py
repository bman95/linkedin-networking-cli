"""Tests for the best-effort diagnostics evidence-bundle module.

These verify the core invariant: capture functions NEVER raise (they run on
the error path and must not mask the original exception), plus the bundle
contents, ring-buffer slot math, anomaly rate limiting, and the wiring into
the ``search_profiles`` readiness-wait failure path.
"""

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import automation.diagnostics as diagnostics
from automation.diagnostics import (
    capture_anomaly_context,
    capture_error_context,
    snapshot_page,
    reset_anomaly_rate_limit,
    _slugify,
    _PAGE_RING_SIZE,
    _MAX_ANOMALY_CAPTURES,
)


@pytest.fixture(autouse=True)
def artifacts_dir(tmp_path, monkeypatch):
    """Redirect artifact writes into a temp dir and reset the run counter."""
    monkeypatch.setenv("LINKEDIN_CLI_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    reset_anomaly_rate_limit()
    yield tmp_path / "artifacts"


def _good_page(url="https://www.linkedin.com/search", title="Search"):
    """A mock page where every accessor succeeds."""
    page = AsyncMock()
    page.url = url
    page.title = AsyncMock(return_value=title)
    page.content = AsyncMock(return_value="<html><body>ok</body></html>")
    page.screenshot = AsyncMock()
    return page


def _crashed_page():
    """A mock page where every accessor raises (crashed/closed page)."""
    page = AsyncMock()
    type(page).url = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))
    page.title = AsyncMock(side_effect=RuntimeError("closed"))
    page.content = AsyncMock(side_effect=RuntimeError("closed"))
    page.screenshot = AsyncMock(side_effect=RuntimeError("closed"))
    return page


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSlugify:
    def test_basic_slug(self):
        assert _slugify("Search Results Wait") == "search_results_wait"

    def test_special_chars_collapsed(self):
        assert _slugify("a!!!b///c @ d") == "a_b_c_d"

    def test_truncated_to_max_len(self):
        slug = _slugify("x" * 100)
        assert len(slug) <= 40

    def test_empty_returns_unnamed(self):
        assert _slugify("   ") == "unnamed"
        assert _slugify("") == "unnamed"

    def test_no_leading_trailing_underscores(self):
        assert _slugify("  !hello!  ") == "hello"


# ---------------------------------------------------------------------------
# capture_error_context
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCaptureErrorContext:
    @pytest.mark.asyncio
    async def test_writes_screenshot_and_dom(self, artifacts_dir):
        page = _good_page()
        result = await capture_error_context(page, "boom", exc=ValueError("nope"))

        assert result["screenshot_ok"] is True
        assert result["dom_ok"] is True
        png = Path(result["screenshot"])
        html = Path(result["dom"])
        assert png.name.startswith("error_boom_")
        assert png.suffix == ".png"
        assert html.suffix == ".html"
        # DOM is actually written; screenshot is mocked so only the path is asserted.
        assert html.exists()
        assert html.read_text(encoding="utf-8") == "<html><body>ok</body></html>"

    @pytest.mark.asyncio
    async def test_screenshot_uses_viewport_and_timeout(self):
        page = _good_page()
        await capture_error_context(page, "boom")
        kwargs = page.screenshot.await_args.kwargs
        assert kwargs["full_page"] is False
        assert kwargs["timeout"] == diagnostics._SCREENSHOT_TIMEOUT_MS

    @pytest.mark.asyncio
    async def test_never_raises_on_crashed_page(self):
        page = _crashed_page()
        # Must not raise even though every accessor throws.
        result = await capture_error_context(page, "boom", exc=RuntimeError("orig"))
        assert result["screenshot_ok"] is False
        assert result["dom_ok"] is False
        assert result["screenshot"] is None
        assert result["dom"] is None

    @pytest.mark.asyncio
    async def test_emits_structured_log_line_at_error(self, caplog):
        page = _good_page(url="https://www.linkedin.com/x", title="Title Z")
        with caplog.at_level(logging.ERROR, logger="automation.diagnostics"):
            await capture_error_context(
                page,
                "readiness_wait",
                exc=ValueError("bad"),
                context={"profile_url": "https://li/in/jdoe", "campaign": "Camp A"},
            )
        records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert records, "expected an ERROR-level diagnostics log line"
        rec = records[-1]
        # Structured extras present.
        assert rec.diagnostics_step == "readiness_wait"
        assert rec.diagnostics_url == "https://www.linkedin.com/x"
        assert rec.diagnostics_title == "Title Z"
        assert rec.diagnostics_exc_type == "ValueError"
        assert rec.diagnostics_profile_url == "https://li/in/jdoe"
        assert rec.diagnostics_campaign == "Camp A"
        # Single parseable message line carries the key fields too.
        msg = rec.getMessage()
        assert "step=readiness_wait" in msg
        assert "exc_type=ValueError" in msg

    @pytest.mark.asyncio
    async def test_log_line_emitted_even_when_both_captures_fail(self, caplog):
        page = _crashed_page()
        with caplog.at_level(logging.ERROR, logger="automation.diagnostics"):
            await capture_error_context(page, "boom", exc=RuntimeError("x"))
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "structured log line must still be emitted on a crashed page"


# ---------------------------------------------------------------------------
# capture_anomaly_context (rate limiting + WARNING severity)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCaptureAnomalyContext:
    @pytest.mark.asyncio
    async def test_anomaly_logs_at_warning(self, caplog):
        page = _good_page()
        with caplog.at_level(logging.WARNING, logger="automation.diagnostics"):
            await capture_anomaly_context(page, "weird_banner")
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(r.getMessage().startswith("DIAGNOSTICS anomaly") for r in warnings)

    @pytest.mark.asyncio
    async def test_anomaly_filename_prefix(self, artifacts_dir):
        page = _good_page()
        result = await capture_anomaly_context(page, "weird")
        assert Path(result["screenshot"]).name.startswith("anomaly_weird_")

    @pytest.mark.asyncio
    async def test_rate_limited_per_run(self):
        page = _good_page()
        # First N succeed.
        for _ in range(_MAX_ANOMALY_CAPTURES):
            assert await capture_anomaly_context(page, "spam") is not None
        # The next one is suppressed.
        assert await capture_anomaly_context(page, "spam") is None

    @pytest.mark.asyncio
    async def test_reset_clears_rate_limit(self):
        page = _good_page()
        for _ in range(_MAX_ANOMALY_CAPTURES):
            await capture_anomaly_context(page, "spam")
        assert await capture_anomaly_context(page, "spam") is None
        reset_anomaly_rate_limit()
        assert await capture_anomaly_context(page, "spam") is not None

    @pytest.mark.asyncio
    async def test_never_raises_on_crashed_page(self):
        page = _crashed_page()
        result = await capture_anomaly_context(page, "weird")
        assert result["screenshot_ok"] is False


# ---------------------------------------------------------------------------
# snapshot_page (ring buffer)
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSnapshotPage:
    @pytest.mark.asyncio
    async def test_writes_png_and_txt_sidecar(self, artifacts_dir):
        page = _good_page(url="https://www.linkedin.com/feed")
        path = await snapshot_page(page, 0)
        assert path is not None
        png = Path(path)
        txt = png.with_suffix(".txt")
        assert png.name == "page_0.png"
        assert txt.exists()
        sidecar = txt.read_text(encoding="utf-8")
        assert "https://www.linkedin.com/feed" in sidecar
        # ISO timestamp on the first line.
        assert "T" in sidecar.splitlines()[0]

    @pytest.mark.asyncio
    async def test_slot_is_seq_mod_ring_size(self, artifacts_dir):
        page = _good_page()
        path = await snapshot_page(page, _PAGE_RING_SIZE + 3)
        assert Path(path).name == "page_3.png"

    @pytest.mark.asyncio
    async def test_ring_buffer_bounds_file_count(self, artifacts_dir):
        page = _good_page()
        for seq in range(_PAGE_RING_SIZE * 3):
            await snapshot_page(page, seq)
        pages_dir = artifacts_dir / "pages"
        # screenshot is mocked (no real PNG), but the .txt sidecars are written
        # for real and are the ground truth for ring-buffer slot reuse: never
        # more than the ring size regardless of how many snapshots we took.
        sidecars = list(pages_dir.glob("page_*.txt"))
        assert len(sidecars) == _PAGE_RING_SIZE
        slots = {p.stem.removeprefix("page_") for p in sidecars}
        assert slots == {str(i) for i in range(_PAGE_RING_SIZE)}

    @pytest.mark.asyncio
    async def test_never_raises_on_crashed_page(self):
        page = _crashed_page()
        # Sidecar may still write (URL falls back to "") but screenshot fails.
        result = await snapshot_page(page, 0)
        assert result is None


# ---------------------------------------------------------------------------
# Wiring: search_profiles readiness wait captures a bundle before raising
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSearchReadinessWiring:
    @pytest.mark.asyncio
    async def test_readiness_timeout_captures_bundle_before_swallowing(
        self, mock_linkedin_automation
    ):
        from playwright.async_api import TimeoutError as PWTimeoutError
        from database.models import Campaign

        campaign = Campaign(name="Wiring Test")
        # Force the readiness wait_for_selector to time out.
        mock_linkedin_automation.page.wait_for_selector = AsyncMock(
            side_effect=PWTimeoutError("timeout")
        )

        with patch(
            "automation.linkedin.capture_error_context",
            new=AsyncMock(),
        ) as mock_capture:
            # search_profiles swallows the exception and returns partial results,
            # but the diagnostics bundle must have been captured first.
            result = await mock_linkedin_automation.search_profiles(campaign, limit=5)

        assert result == []
        mock_capture.assert_awaited_once()
        call = mock_capture.await_args
        assert call.args[1] == "search_results_readiness_wait"
        assert call.kwargs["context"]["campaign"] == "Wiring Test"
        # The captured exception is the SelectorNotFoundException.
        assert type(call.kwargs["exc"]).__name__ == "SelectorNotFoundException"
