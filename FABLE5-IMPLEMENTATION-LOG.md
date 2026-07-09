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

---

# 2026-07-06 audit fixes — deep re-audit + remediation

A fresh full-repo audit (four parallel subagent readers over automation, data/config,
both UIs, and tests/tooling) went beyond the 2026-07-01 pass and surfaced concrete
bugs, security gaps, data-layer concurrency problems, and design-level rethinks. All
**safe** items were implemented across eight parallel subagents on disjoint file sets;
strategic items needing a live account or a product decision were written up in
`DESIGN-PROPOSALS.md` instead of implemented blind.

**Baseline → result:** 742 → **778 passed**, coverage 72% → **73%** (CI floor set to
65%). Nothing committed.

## Bugs (verified in source, fixed)

- **checker.py** — `page.is_visible(timeout=)` doesn't wait in Playwright's async API
  (misreported acceptances); replaced with `wait_for_selector(state="visible")` and made
  the connection indicators bilingual (ES/EN). Tab leak in `_update_accepted_connection`
  wrapped in try/finally. Unbounded scroll in `_check_connections_page` given a
  `max_scroll_rounds` guard. +3 regression tests (one reproduced the hang).
- **interactions.py** — keystroke humanization was a no-op (`press_sequentially(char,
  delay=)` never applies per single char); now an explicit randomized `asyncio.sleep`
  between keystrokes. `_human_mouse_move` interpolated from viewport origin (a
  fingerprintable straight line); now interpolates from the last cursor position.
- **scraping.py** — `get_open_to_work_status` matched the whole serialized DOM (false
  positives); now scoped to visible badge elements.
- **linkedin_cli.py** — removed the dead closed-loop `asyncio` fallback (left a closed
  loop installed as current) and the unreachable `isinstance(selected, dict)` branch.
- **linkedin.py** — `_build_search_params` f-string-injected `geo_urn`/`network`
  unencoded; now validated + percent-encoded (byte-identical URLs for valid input).

## Security / privacy

- `session.json` (full auth cookies) now written 0600 via a single `_write_session_state`
  helper, app/profile dirs 0700; `close_browser` no longer overwrites a good session from
  an unauthenticated context (gated on `is_authenticated`).
- diagnostics artifacts (screenshots + DOM dumps) chmod 0600/0700 and pruned to the 20
  newest bundles.
- Dockerfile rewritten: deps layer cached before source copy, chromium in the cached
  layer, non-root `appuser`, `CMD ["linkedin-cli"]`; `.dockerignore` extended.

## Data layer

- **SQLite concurrency**: engine now sets `journal_mode=WAL`, `busy_timeout=5000`,
  `foreign_keys=ON` per connection (via a `connect` event) + explicit
  `check_same_thread=False` — the reservation/upsert machinery is finally backed by a DB
  configured for the two-process/many-thread access it assumes.
- `delete_campaign` now bulk-deletes contacts **and** the previously-orphaned Analytics
  rows.
- All writes standardized on timezone-aware UTC (was mixed naive/aware in the same
  columns).
- Dashboard/campaign stats rewritten from full-table Python scans to SQL `GROUP BY`.
- `ContactStatus` str-enum + single-source `SENT_STATUSES`/`PENDING_STATUSES`/
  `ACCEPTED_STATUSES` groupings replace scattered string-set literals (stored value stays
  the plain string).

## Two-UI de-duplication (net −148 lines)

Extracted the duplicated presentation-adjacent logic into one home so both UIs consume
it: `acceptance_rate` (was in 5 places), `write_contacts_csv`/`CONTACT_CSV_FIELDS`/
`contacts_csv_filename` (CSV export was duplicated + divergent), `mask_email` (TUI copy
removed), and `describe_automation_error` (new `src/cli/automation_errors.py`, CLI keeps
only its Rich-escaping/traceback presentation). Fixed the TUI empty-states that told
users to "use the classic CLI" for a feature the TUI has, and the extract-profiles
mode-toggle showing both widgets at once.

## New capability (additive, safe; live send path validated manually per convention)

- **Proactive weekly-invite budget** — `WEEKLY_INVITATION_LIMIT` (default 100) +
  `get_weekly_connection_count()` (trailing-7-day sum of the existing per-day rows) +
  a pre-send stop at the same orchestration level as the daily cap. The tool now paces
  to the *weekly* wall instead of discovering it reactively via LinkedIn's modal.
- **Non-interactive `linkedin-cli run --campaign X [--max N]`** for cron/systemd-timer
  scheduling of small randomized-cadence batches; reuses the existing automation (no
  reimplementation). No-arg `linkedin-cli` is unchanged.

## Tooling

- ruff + mypy added (deps + `[tool.ruff]`/`[tool.mypy]` config); both *run* (549 / 124
  pre-existing legacy findings surfaced, not auto-fixed — matching existing style). CI
  gains a non-blocking lint step and a `--cov-fail-under=65` floor. `tests/README.md`
  numbers corrected (was 12× stale); two dead autouse conftest fixtures removed.

## Mascot work finished

The pre-existing uncommitted mascot work was completed: redundant `tools/mascot/
generate.py` deleted (its only target was the removed `_mascot.py`), `generate_wordmark.py`
no longer emits the unused `WORDMARK`, dead splash CSS block removed from `app.tcss`,
orphan `.pyc`s deleted, stale docstrings/comments fixed.

## Not done autonomously — see `DESIGN-PROPOSALS.md`

UI cutover (needs per-flow sign-off), Voyager-API extraction (needs live payloads),
Sent-Invitations-diff checker rework (needs live DOM), `scraping.py` SDUI selector
rewrite (needs live DOM), analytics-as-event-log + migration versioning (invasive; want
a populated DB to test against), and the state-machine/`linkedin.py` split. The
2026-07-06 pass implemented the safe subset of the weekly-budget, `run`-command, and
status-enum proposals; the doc marks those partial.

**Final verification:** `uv run pytest -q` → **778 passed**, 73% coverage; `ruff`/`mypy`
run; `import linkedin_cli`/`linkedin_tui` OK; `linkedin-cli run --help` exit 0. Work
spread across eight subagents on disjoint file sets, full suite re-run green after each
wave.

---

## 2026-07-07 — §10 TUI refactors + location-search port (pre-cutover)

Implements `DESIGN-PROPOSALS.md` §10 (both deferred TUI refactors) and closes the
last §1 parity gap, unblocking the single-UI cutover pending per-flow sign-off.

- **Worker-guard mixin.** The copy-pasted threaded-worker race discipline
  (generation token, capture-app-at-schedule-time, `is_running`+`RuntimeError`+
  `is_mounted` guards) now lives once in `tui/screens/workers.py`
  (`WorkerGuardMixin`: `begin_load` / `marshal` / `marshal_load`), inherited via
  `BaseScreen` and mixed into `HomeScreen`. Seven screens migrated; per-screen
  `_marshal_*` methods and guard boilerplate deleted. Tests that drove the guards
  directly were updated to the mixin API; `docs/tui-migration.md` §"State design"
  rewritten to match.
- **Single-source navigation.** `tui/nav.py` `NAV_ITEMS` (key, title, description,
  push) now drives the home list, the home number-key bindings, and the command
  palette (`commands.py`); the four hand-synced lists and home's eager screen
  imports are gone. Screen imports stay lazy inside each `push`.
- **Online location search + custom geoUrn (last §1 parity gap).** Ported the
  classic "🔎 Search location online" / "Other (enter custom geoUrn)" flows to the
  shared campaign form. New `CampaignFormScreen` base (Create/Edit inherit): the
  Location select gains two sentinel options revealing an inline query input
  (Playwright login + `search_location` in a thread worker;
  `perform_location_search` is the test seam, mirroring `run_body`) or the
  geoUrn/name inputs; a picked result becomes a first-class select option backed
  by a display-name → geoUrn override map. `fill_form` uses the same map to
  preserve a stored non-curated location on Edit — previously it silently reset
  to "Any" and dropped the campaign's geoUrn on the next save (parity bug, now
  regression-tested). 8 new tests in `tests/test_tui_location_search.py`; TUI
  states verified visually via headless Pilot screenshots. The live search path
  (real browser + login) was validated manually by the owner on 2026-07-07, per
  convention — every classic flow now has a TUI equivalent at validated parity.

**Final verification:** `uv run pytest -q` → **786 passed**, 73% coverage.
