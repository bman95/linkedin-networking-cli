# FABLE5 Implementation Log — linkedin-networking-cli

Date: 2026-07-02. Implemented the overnight-SAFE steps 1–7 from `FABLE5-NEXT-STEPS.md`.
All work is local (code/tests/docs). Nothing was committed.

**Baseline:** the working tree was already dirty on arrival (pre-existing, untouched):
modified `src/tui/app.tcss`, `src/tui/screens/home.py`, `tests/test_tui_home.py`;
untracked `FABLE5-AUDIT.md`, `FABLE5-NEXT-STEPS.md`, `docs/bit_sprite.png`,
`docs/mascot.png`, `src/tui/screens/_mascot.py`, `tools/`. This pre-existing mascot
work was left exactly as found.

**Final verification:** `uv run pytest` → **742 passed** (717 baseline + 25 new tests),
0 `coroutine ... never awaited` warnings, no stray DB at repo root afterward.

---

## Step 1 — Repo hygiene sweep [P0] — DONE

- **Stray root `linkedin_networking.db`:** re-verified schema-only with **zero rows**
  (5 tables, total 0 rows), backed it up to the session scratchpad, and removed it from
  the repo root. It is gitignored.
  - **Root cause fixed (durable):** the file was being regenerated on every test run by
    `tests/test_database_operations.py::test_init_with_default_path`, which instantiates
    `DatabaseManager()` with its *relative* default path (`linkedin_networking.db`) from
    the repo-root cwd. Wrapped that test in `monkeypatch.chdir(tmp_path)` so the SQLite
    file is created under a temp dir; the `Path("linkedin_networking.db")` assertion is
    cwd-independent and still holds. Confirmed the stray DB no longer reappears after a
    full suite run.
- **Dead `src/styles.css`:** deleted (tracked, referenced by no Python module — the TUI
  uses `src/tui/app.tcss`). Updated the stale note in `docs/tui-migration.md` that
  described it as "left untouched ... should be deleted".
- **`CLAUDE.md` prompt-toolkit drift:** rewrote the overview line; it no longer mentions
  the non-existent prompt-toolkit CLI and now describes the two real UIs (InquirerPy CLI
  + Textual TUI). (`prompt-toolkit` remains in `uv.lock` as a legitimate transitive dep
  of InquirerPy — correct, left as-is.)
- **Migration scripts:** moved `migrate_database.py` and `migrate_schema.py` to
  `scripts/` and added a one-line "One-off migration ... relocated to scripts/" note to
  each header.

Files: `linkedin_networking.db` (removed), `src/styles.css` (removed),
`scripts/migrate_database.py`, `scripts/migrate_schema.py` (moved + header note),
`CLAUDE.md`, `docs/tui-migration.md`, `tests/test_database_operations.py`.

Verify: `uv run pytest -q` → 742 passed; `ls linkedin_networking.db` → absent after the
full suite; `grep -r styles.css src tests linkedin_cli.py linkedin_tui.py` → none.

## Step 2 — English-only compliance [P1] — DONE

- Translated `pyproject.toml:39-40` Spanish comments ("Para tests paralelos" → "For
  parallel tests"; "Para mockear tiempo" → "For mocking time").
- Fully translated `TODOS.md` to English, preserving structure, headings, emoji, status
  markers (✅/❓/🔮), IDs/codes, URL formats, and code blocks.
- Verified no remaining Spanish in docs/comments. The residual grep hits
  (`pendiente`/`para`/`el límite semanal...`/`Añadir una nota`) are **functional
  LinkedIn ES-locale strings** the automation matches against a Spanish LinkedIn UI (in
  `linkedin.py`, `interactions.py`, `selectors.py`) plus false positives (the `el`
  element variable, English proper nouns "Los Angeles"/"Las Vegas"). These are data
  required for correctness, not documentation — deliberately left intact.

Files: `pyproject.toml`, `TODOS.md`.

Verify: `git grep -niE "para |descripción|búsqueda|pendiente|..." -- '*.md' '*.toml'`
(excluding FABLE5-*) → only the functional locale strings remain.

## Step 3 — Guard `int(os.getenv(...))` parsing [P1] — DONE

- Added module-level helper `_env_int(name, default)` to `src/config/settings.py`:
  returns the default when the var is unset, and on a malformed value logs a warning and
  returns the default instead of raising `ValueError`.
- Replaced all direct `int(os.getenv(...))` calls in both blocks — `get_automation_settings`
  (formerly lines 205-217) and `get_navigation_settings` (formerly 258-264) — with
  `_env_int(...)`. No behavior change for valid values.
- Updated the pre-existing `test_automation_settings_with_invalid_int_value` (which
  asserted the *old* crash-on-malformed behavior) to assert the new default-fallback.
- Added new tests: `TestEnvIntHelper` (valid/missing/malformed/empty/whitespace),
  `TestAutomationSettingsGuardedParsing`, `TestNavigationSettingsGuardedParsing` — each
  covering valid/missing/malformed for both blocks, including warning emission.

Files: `src/config/settings.py`, `tests/test_settings.py`.

Verify: `uv run pytest tests/test_settings.py -q` → 94 passed.

## Step 4 — Fix "coroutine never awaited" test warnings [P1] — DONE

Root causes (both test-side only; no production code touched):
- `tests/test_navigation.py::TestGotoRetry::test_goto_hang_becomes_crash_shaped_error`:
  the production code eagerly builds `page.goto(...)` and hands it to `asyncio.wait_for`;
  the mock replaced `wait_for` with an `AsyncMock` that raised `TimeoutError` without
  awaiting its argument, leaking the `page.goto` coroutine. Fixed the mock to `.close()`
  the passed awaitable before raising.
- `tests/test_linkedin_automation.py::TestNavigationGuardWiring::test_search_navigation_uses_guard_with_strict_path`:
  the patched `navigate_guarded` was a bare `AsyncMock()`, so `self.page` was rebound to
  a plain `MagicMock`; the downstream real `verify_listing_rendered` awaited
  `page.locator(...).count()` on it, leaking a coroutine. Fixed the mock to hand the page
  back (`side_effect=lambda page, *a, **k: page`) — matching the documented sibling test
  `test_search_scroll_runs_after_guard` — and patched `_extract_profiles_new_ui` to `[]`.

Files: `tests/test_navigation.py`, `tests/test_linkedin_automation.py`.

Verify: `uv run pytest -o addopts="" tests/test_linkedin_automation.py tests/test_navigation.py`
→ 0 warnings; full suite `grep -ic "never awaited"` → 0.

## Step 5 — Remove `sys.path.append` import hack, one packaging convention [P1] — DONE

- Removed **all 11** `sys.path.append` lines (9 `src/` modules: `automation/checker.py`,
  `selectors.py`, `diagnostics.py`, `interactions.py`, `scraping.py`, `navigation.py`,
  `config/settings.py`, `database/operations.py`, `automation/linkedin.py`; plus entry
  scripts `linkedin_cli.py`, `linkedin_tui.py`). Also removed the imports my change
  orphaned (`import sys` everywhere; `from pathlib import Path` where it was only used by
  the append; the duplicate `from pathlib import Path` in `linkedin.py`).
- **Convention chosen:** top-level packages (`from utils.logging import ...`, unchanged
  everywhere) resolved via the editable install (`uv sync`) / the built wheel — the
  editable `.pth` already puts repo-root + `src/` on the path, so no per-module path
  juggling is needed.
- **Wheel config simplified:** deleted the empty, dead `src/__init__.py` (never imported
  as a package) and removed the now-unneeded `exclude = ["src/__init__.py"]` from
  `pyproject.toml`. The standard hatch src-layout mapping (`sources = {"src" = ""}`) is
  retained. Updated the now-stale "no-install" docstrings in `linkedin_tui.py` and
  `src/tui/__main__.py`.
- Coverage `source` unchanged for this step (files still live under `src/`).

Files: the 9 `src/` modules above, `linkedin_cli.py`, `linkedin_tui.py`,
`src/tui/__main__.py`, `pyproject.toml`, `src/__init__.py` (removed).

Verify:
- `grep -rn sys.path.append src linkedin_cli.py linkedin_tui.py` → none.
- `uv sync --extra dev` re-created the editable install cleanly; top-level imports resolve
  from an unrelated cwd; `linkedin-cli`/`linkedin-tui` entry points present.
- `uv build --wheel` → wheel has clean top-level layout (packages `automation/config/
  database/tui/utils`, root modules `linkedin_cli.py/linkedin_tui.py/exceptions.py`, **no**
  root `__init__.py`). Installed into a **fresh Python 3.13 venv**: all top-level packages
  import, both entry-point scripts present, `linkedin-cli` renders its welcome + menu.
  (Temp venv + `dist/` build artifact cleaned up afterward.)
- Full suite: 742 passed.

Note: `linkedin-tui` renders a full-screen Textual app that needs a real TTY, so it was
verified by resolving its entry wiring (`linkedin_tui.main` + `tui.app.run` importable)
rather than launched headless.

## Step 6 — Bring `linkedin_cli.py` under coverage; extract pure helpers [P2] — DONE

- **Coverage:** added `linkedin_cli` to the measured sources (`--cov=linkedin_cli` in
  `addopts`, `source = ["src", "linkedin_cli"]`). It now reports a real number
  (**20%** in the full suite) instead of being excluded.
- **Extraction (no behavior change):** moved the three pure, tested helpers to a new
  importable module `src/cli/helpers.py` — `campaign_get_field`, `csv_value`,
  `mask_email`. `LinkedInCLI._campaign_get_field/_csv_value/_mask_email` remain as thin
  static-method delegators, so all internal call sites and the class surface are
  unchanged. `src/cli/helpers.py` is at **100%** coverage.
- Rewrote `tests/test_cli_helpers.py` to import from the new location (`from cli.helpers
  import ...`) and added delegator-parity tests asserting the class methods still return
  identical results.

Files: `src/cli/__init__.py` (new), `src/cli/helpers.py` (new), `linkedin_cli.py`,
`pyproject.toml`, `tests/test_cli_helpers.py`.

Verify: `uv run pytest tests/test_cli_helpers.py` → 12 passed; coverage report shows
`linkedin_cli.py` and `src/cli/helpers.py` with real numbers.

## Step 7 — Raise automation-core coverage to ≥60% [P2] — DONE

- Measured the current per-module coverage in the full suite: `linkedin.py` **71%** and
  `navigation.py` **92%** already exceed the 60% target; only `checker.py` (**40%**) was
  below.
- Added focused, browser-fully-mocked tests to `tests/test_checker.py` for the
  connection-checking core: the smart-checker page walk (found/accepted →
  `_update_accepted_connection` DB update with email/phone/address enrichment), the
  connection-limit stop, navigation-error handling, enrichment-error swallowing, the
  fragment-only URL cleanup branch, richer `check_specific_contacts` (contact-info
  collection + failure counting), and `monitor_pending_connections` per-campaign error
  and inter-iteration wait paths. Added small mock builders (`_profile_el`,
  `_connection_el`, `_set_limit`) and a deterministic `random.randint` patch.
- Result: `checker.py` **40% → 94%**. All three target modules now ≥60%.
- Production code unchanged in this step (tests only).

Files: `tests/test_checker.py`.

Verify: `uv run pytest` → 742 passed; per-module lines: `checker.py` 94%,
`linkedin.py` 71%, `navigation.py` 92%.

---

## Skipped

None. Steps 1–7 were all completed. (`FABLE5-NEXT-STEPS.md` steps 8–10 are out of scope
here: 8 is marked SAFE: NO, and 9–10 are human-in-the-loop.)

## Fable 5 Review

Independent verification by a fresh reviewer (2026-07-02). Verdict: **PASS**. Every
claimed step was checked against the actual working-tree diff and re-run offline with
`uv`. Nothing was committed (HEAD still at `56f1ffe`); no forbidden git commands, no
deleted user data (stray schema-only DB confirmed 0-row before removal), no secrets in
the diff.

Per-step findings:
- **[P0] Repo hygiene** — VERIFIED. No `linkedin_networking.db` at repo root (and none
  reappears after a full suite run). `src/styles.css` deleted; `docs/tui-migration.md`
  note updated to past tense. `CLAUDE.md` overview no longer mentions prompt-toolkit and
  describes the two real UIs. `migrate_database.py`/`migrate_schema.py` moved to
  `scripts/` each with a relocation header note. `test_init_with_default_path` now uses
  `monkeypatch.chdir(tmp_path)`.
- **[P1] Translation** — VERIFIED. `TODOS.md` has no residual Spanish; `pyproject.toml`
  dev-deps comments now English. No Spanish comments remain in `src/automation/*.py`
  (functional ES-locale match strings deliberately kept).
- **[P1] settings `_env_int` guard** — VERIFIED. Helper added; all `int(os.getenv(...))`
  calls in both settings blocks replaced; malformed values degrade to default with a
  warning instead of crashing. `settings.py` at 94% coverage.
- **[P1] coroutine-never-awaited** — VERIFIED. 0 "never awaited" RuntimeWarnings
  suite-wide (checked with `-W default::RuntimeWarning`), and 0 in the two touched test
  files. Production code untouched.
- **[P1] sys.path.append removal** — VERIFIED. No `sys.path.append` in `src/` or the two
  entry scripts (the single remaining one lives in the *moved* `scripts/migrate_database.py`,
  which was explicitly out of scope). `uv build --wheel --offline` produces a clean
  top-level layout with no root `__init__.py`; a fresh Python 3.13 venv installs the wheel
  offline, imports all top-level packages, and exposes working `linkedin-cli`/`linkedin-tui`
  entry points.
- **[P2] linkedin_cli coverage + helpers** — VERIFIED. `src/cli/helpers.py` at 100%;
  `linkedin_cli.py` now measured (20%); thin static-method delegators retained with
  parity assertions in `tests/test_cli_helpers.py`.
- **[P2] automation-core coverage** — VERIFIED. checker.py 94%, linkedin.py 71%,
  navigation.py 92% — all ≥60%. checker.py gain is tests-only.

Full suite re-run by reviewer: **742 passed** in ~58s (matches self-report). Pre-existing
dirty/mascot work (`src/tui/screens/_mascot.py`, `docs/*.png`, `tools/`, tui home/tcss)
was left untouched, as claimed.
