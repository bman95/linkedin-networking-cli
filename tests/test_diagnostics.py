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
    reset_page_ring,
    reset_diagnostics_run,
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


class _CrashedPage:
    """A page where every accessor raises on ATTRIBUTE ACCESS.

    A real closed Playwright page raises ``Target page ... has been closed``
    the moment you touch ``page.url`` / ``page.title`` / ``page.content`` /
    ``page.screenshot`` — before any await. An ``AsyncMock`` with
    ``side_effect`` only raises on the await, which is a weaker failure mode,
    so we model the realistic one explicitly here.
    """

    @property
    def url(self):
        raise RuntimeError("closed")

    def __getattr__(self, name):
        if name in ("title", "content", "screenshot"):
            raise RuntimeError("closed")
        raise AttributeError(name)


def _crashed_page():
    """A mock page where every accessor raises on attribute access."""
    return _CrashedPage()


class _BoomRepr:
    """A value whose ``__repr__`` raises, to exercise the _safe_repr guard."""

    def __repr__(self):
        raise ValueError("boom in repr")


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
    async def test_dom_capture_is_time_bounded(self, monkeypatch):
        # A *wedged* (not closed) page: page.content() hangs. The DOM dump must
        # be time-bounded so it can never stall the error path and swallow the
        # original exception. Tighten the cap so the test stays fast.
        import asyncio

        monkeypatch.setattr(diagnostics, "_DOM_TIMEOUT_S", 0.05)
        page = _good_page()

        async def _hang():
            await asyncio.sleep(30)
            return "<html>never</html>"

        page.content = _hang

        # Whole capture must return well within a second despite the 30s hang.
        result = await asyncio.wait_for(
            capture_error_context(page, "wedged", exc=RuntimeError("orig")),
            timeout=2.0,
        )
        assert result["dom_ok"] is False
        assert result["dom"] is None
        # Screenshot path is independent and still succeeds (mock).
        assert result["screenshot_ok"] is True

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
        # Single parseable message line carries the key fields too, including
        # the artifact paths (an acceptance-criteria field most likely to
        # regress silently).
        msg = rec.getMessage()
        assert "step=readiness_wait" in msg
        assert "exc_type=ValueError" in msg
        assert "screenshot=" in msg
        assert "dom=" in msg
        assert rec.diagnostics_screenshot is not None
        assert rec.diagnostics_dom is not None

    @pytest.mark.asyncio
    async def test_log_line_emitted_even_when_both_captures_fail(self, caplog):
        page = _crashed_page()
        with caplog.at_level(logging.ERROR, logger="automation.diagnostics"):
            result = await capture_error_context(page, "boom", exc=RuntimeError("x"))
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        # Exactly one DIAGNOSTICS line must fire (not a backstop "failed" line).
        diag_lines = [r for r in errors if "DIAGNOSTICS error" in r.getMessage()]
        assert diag_lines, "structured DIAGNOSTICS line must fire on a crashed page"
        # Return value is consistent with what's actually on disk (nothing).
        assert result["screenshot"] is None and result["dom"] is None
        assert result["screenshot_ok"] is False and result["dom_ok"] is False

    @pytest.mark.asyncio
    async def test_same_step_same_second_does_not_overwrite(self, artifacts_dir):
        # Two captures with the same step name in the same wall-clock second
        # must resolve to distinct artifact paths, or the second silently
        # clobbers the first's evidence (one-second timestamp granularity).
        page = _good_page()
        page.content = AsyncMock(side_effect=["<html>FIRST</html>", "<html>SECOND</html>"])
        first = await capture_error_context(page, "same_step", exc=ValueError("a"))
        second = await capture_error_context(page, "same_step", exc=ValueError("b"))

        assert first["dom"] != second["dom"]
        assert first["screenshot"] != second["screenshot"]
        # Both DOMs survive on disk, distinct content.
        assert Path(first["dom"]).read_text(encoding="utf-8") == "<html>FIRST</html>"
        assert Path(second["dom"]).read_text(encoding="utf-8") == "<html>SECOND</html>"

    @pytest.mark.asyncio
    async def test_never_raises_on_throwing_repr_context(self, caplog):
        page = _good_page()
        with caplog.at_level(logging.ERROR, logger="automation.diagnostics"):
            # A context value whose __repr__ raises must not derail capture.
            result = await capture_error_context(
                page, "boom", exc=ValueError("x"), context={"weird": _BoomRepr()}
            )
        assert result["dom_ok"] is True  # capture still completed
        diag_lines = [
            r for r in caplog.records if "DIAGNOSTICS error" in r.getMessage()
        ]
        assert diag_lines, "structured line must fire despite a throwing __repr__"
        assert "<unrepresentable>" in diag_lines[-1].getMessage()


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

    @pytest.mark.asyncio
    async def test_noop_captures_do_not_consume_budget(self):
        # A string of no-op attempts on a crashed page writes nothing and must
        # not exhaust the per-run budget — a later capture on a live page still
        # gets through.
        crashed = _crashed_page()
        for _ in range(_MAX_ANOMALY_CAPTURES * 2):
            await capture_anomaly_context(crashed, "dead")
        # Budget intact: a real capture on a live page still lands evidence.
        result = await capture_anomaly_context(_good_page(), "alive")
        assert result is not None
        assert result["dom_ok"] is True


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

    @pytest.mark.asyncio
    async def test_failed_overwrite_removes_stale_png(self, artifacts_dir):
        # A slot first written successfully by a real screenshot...
        pages_dir = artifacts_dir / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        stale = pages_dir / "page_0.png"
        stale.write_bytes(b"stale-evidence-from-a-previous-page")

        # ...is reused by a later snapshot whose screenshot fails. The fresh
        # sidecar must not be paired with the stale png.
        page = _good_page(url="https://www.linkedin.com/new")
        page.screenshot = AsyncMock(side_effect=RuntimeError("snap failed"))
        result = await snapshot_page(page, 0)

        assert result is None
        assert not stale.exists(), "stale screenshot must be removed on failed overwrite"
        # The sidecar is still written (records the URL that failed to shoot).
        assert (pages_dir / "page_0.txt").exists()


# ---------------------------------------------------------------------------
# Per-run reset of diagnostics state
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestResetDiagnosticsRun:
    @pytest.mark.asyncio
    async def test_reset_page_ring_clears_stale_slots(self, artifacts_dir):
        page = _good_page()
        # Populate a few slots (sidecars are written for real).
        for seq in range(4):
            await snapshot_page(page, seq)
        pages_dir = artifacts_dir / "pages"
        assert list(pages_dir.glob("page_*.txt"))

        reset_page_ring()
        assert not list(pages_dir.glob("page_*")), "ring must be empty after reset"

    @pytest.mark.asyncio
    async def test_reset_page_ring_safe_when_dir_absent(self):
        # No pages dir created yet — must not raise.
        reset_page_ring()

    @pytest.mark.asyncio
    async def test_reset_diagnostics_run_resets_both(self, artifacts_dir):
        page = _good_page()
        for seq in range(3):
            await snapshot_page(page, seq)
        for _ in range(_MAX_ANOMALY_CAPTURES):
            await capture_anomaly_context(page, "spam")
        # Counter exhausted and ring populated.
        assert await capture_anomaly_context(page, "spam") is None
        assert list((artifacts_dir / "pages").glob("page_*.txt"))

        reset_diagnostics_run()

        assert await capture_anomaly_context(page, "ok") is not None
        # Only the just-written anomaly snapshot-less bundle; the page ring was cleared.
        assert not list((artifacts_dir / "pages").glob("page_*"))


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


@pytest.mark.unit
class TestSearchLoopDiagnosticsWiring:
    """The ring buffer, per-run reset, and anomaly path are wired live."""

    @pytest.mark.asyncio
    async def test_run_resets_rate_limit_and_snapshots_landed_page(
        self, mock_linkedin_automation
    ):
        from database.models import Campaign

        campaign = Campaign(name="Loop Test")
        # One search page that yields no profiles, then stop (no next button).
        mock_linkedin_automation.page.query_selector_all = AsyncMock(return_value=[])
        mock_linkedin_automation.page.query_selector = AsyncMock(return_value=None)

        with patch(
            "automation.linkedin.reset_diagnostics_run"
        ) as mock_reset, patch(
            "automation.linkedin.snapshot_page", new=AsyncMock()
        ) as mock_snapshot, patch.object(
            mock_linkedin_automation, "_extract_profiles_new_ui",
            new=AsyncMock(return_value=[]),
        ):
            await mock_linkedin_automation.search_profiles(campaign, limit=5)

        # Per-run boundary cleared all diagnostics state exactly once.
        mock_reset.assert_called_once()
        # The landed page was snapshotted into the ring buffer (seq 0).
        mock_snapshot.assert_awaited()
        assert mock_snapshot.await_args.args[1] == 0

    @pytest.mark.asyncio
    async def test_no_profiles_extracted_captures_anomaly(
        self, mock_linkedin_automation
    ):
        from database.models import Campaign

        campaign = Campaign(name="Anomaly Test")
        mock_linkedin_automation.page.query_selector_all = AsyncMock(return_value=[])
        mock_linkedin_automation.page.query_selector = AsyncMock(return_value=None)

        with patch(
            "automation.linkedin.capture_anomaly_context", new=AsyncMock()
        ) as mock_anomaly, patch.object(
            mock_linkedin_automation, "_extract_profiles_new_ui",
            new=AsyncMock(return_value=[]),
        ):
            await mock_linkedin_automation.search_profiles(campaign, limit=5)

        mock_anomaly.assert_awaited_once()
        call = mock_anomaly.await_args
        assert call.args[1] == "search_page_no_profiles_extracted"
        assert call.kwargs["context"]["campaign"] == "Anomaly Test"
