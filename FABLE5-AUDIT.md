# FABLE5 Audit — linkedin-networking-cli

Audit date: 2026-07-01. Snapshot: `master` at `56f1ffe` plus uncommitted mascot work.

## Overview

A Python CLI/TUI for automating LinkedIn networking campaigns: campaign CRUD with
targeting criteria, Playwright-driven connection-request automation with rate
limiting and humanization, connection-status checking, profile extraction,
analytics, and CSV export. Data persists in SQLite (SQLModel) under
`~/.linkedin-networking-cli/`. Two UIs coexist: the classic InquirerPy CLI
(`linkedin_cli.py`) and a newer full-screen Textual TUI (`src/tui/`, issue #24)
that reuses the same business logic read-only. README carries an appropriate
ToS/account-risk disclaimer.

## Current State

- **Actively developed.** Last commit 2026-06-25; 15 recent commits are focused
  feature/fix work (TUI screens, resilient invite-send tail #31/#39, Chrome
  ProcessSingleton fix). Uncommitted work in progress: mascot home-screen art
  (`src/tui/screens/_mascot.py`, `tools/mascot/`, `docs/*.png`, modified
  `home.py`/`app.tcss`/`test_tui_home.py`) — coherent, not abandoned debris.
- **Tests green:** 717 tests pass locally in ~49 s (verified during this audit),
  browser fully mocked. CI (`.github/workflows/ci.yml`) runs the suite on every
  push/PR with uv + Python 3.13.
- TUI migration is mid-flight but disciplined: `docs/tui-migration.md` documents
  the plan, invariants, and per-screen status.
- Many stale branches: 5 local + ~14 remote `claude/*` / `feat/*` branches, most
  presumably merged.

## Architecture & Code Quality

Layered and mostly clean: `src/automation/` (Playwright engine, split into
linkedin/navigation/interactions/scraping/checker/selectors/diagnostics),
`src/database/` (SQLModel models + `DatabaseManager`), `src/config/`,
`src/utils/`, `src/tui/` (Textual screens + theme + tcss). Comments and
docstrings are unusually good — nontrivial decisions (session persistence
duality, `reserved`/`possibly_sent` contact states, timezone detection
fallbacks) are explained where they live.

Concrete issues:

- **`sys.path.append` import hack replicated in ~10 modules** (e.g.
  `src/config/settings.py:7`, `src/automation/linkedin.py:26`,
  `src/database/operations.py:10`). Packages are imported as top-level
  (`from utils.logging import ...`) instead of a proper `src/` package or
  editable install; the wheel build has to compensate with a custom source
  mapping (`pyproject.toml:51-59`). Fragile and non-standard.
- **God modules:** `src/automation/linkedin.py` is 3,000 lines;
  `linkedin_cli.py` is a 1,664-line monolith at repo root mixing menu UI,
  orchestration, and formatting. `src/database/operations.py` (977 lines) and
  `src/automation/navigation.py` (958) are also heavy.
- **Dead file:** `src/styles.css` is tracked but referenced by no Python module
  (the TUI uses `src/tui/app.tcss`).
- **Doc drift:** `CLAUDE.md:7` still mentions "an experimental interactive CLI
  using prompt-toolkit" that no longer exists in the tree.
- **Language rule violations:** `TODOS.md` is written in Spanish, and
  `pyproject.toml:39-40` has Spanish comments ("Para tests paralelos", "Para
  mockear tiempo") — contrary to the owner's global English-only instruction.
- **Root-level one-off scripts:** `migrate_database.py` and `migrate_schema.py`
  (both June 11) look like completed one-time migrations kept at top level.
- **Env-var parsing without guards:** `src/config/settings.py:205-217` and
  `:258-264` call `int(os.getenv(...))` directly; a malformed value (e.g.
  `DAILY_CONNECTION_LIMIT=twenty`) raises an unhandled `ValueError` at startup.
- `AppSettings._match_localtime_by_bytes` (`src/config/settings.py:72-95`)
  reads potentially all ~600 zoneinfo files byte-for-byte on the fallback path —
  bounded but slow; fine for a CLI, worth knowing.

## Bugs & Risks

- **No concrete correctness bugs found** in the areas reviewed; the trickiest
  logic (pre-send `reserved` marker, `possibly_sent` after renderer wedge,
  per-day counters, unique-constraint upsert) is explicitly modeled in
  `src/database/models.py:53-115` and covered by tests.
- **Inherent risk:** the whole tool automates LinkedIn against its ToS;
  detection/selector drift can break it at any time. Mitigated by conservative
  defaults, humanization tunables, and a centralized `selectors.py`, but scraping
  code rot is the dominant maintenance cost.
- **Test-suite warnings:** two `RuntimeWarning: coroutine ... was never awaited`
  from `tests/test_linkedin_automation.py` (TestNavigationGuardWiring) and
  `tests/test_navigation.py` (TestGotoRetry) — mock hygiene, not product bugs.
- Credentials are env-var only (`LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD`), never
  persisted by the app into the repo; email is masked in logs
  (`src/config/settings.py:33`). Session cookies live in
  `~/.linkedin-networking-cli/`, outside the repo.

## Tests & Docs

- **Tests:** 21 test modules, ~653 test functions, 717 collected — all passing.
  Coverage (from the freshly generated `coverage.xml`) is **~41.5% of `src/`**:
  DB/config/mappings/TUI-read paths are well covered; the deep Playwright
  automation paths are only partially covered (expected, browser is mocked).
  `linkedin_cli.py` itself is excluded from coverage measurement
  (`pyproject.toml:82`, `source = ["src"]`) despite `tests/test_cli_helpers.py`
  targeting it.
- **Docs:** README is accurate and honest (setup, session-persistence duality,
  invite-note limitation, Docker); `docs/tui-migration.md` is an excellent
  living migration plan; `tests/README.md` documents the suite; `CLAUDE.md` is
  good apart from the prompt-toolkit drift noted above. `TODOS.md` is a real
  backlog but in Spanish.

## Hygiene

- **No secrets found** in tracked files, `.claude/settings.local.json`, or
  anywhere in the tree. No credentials in git history surfaced by inspection of
  tracked files.
- **Stray database:** `linkedin_networking.db` (49 KB) sits at repo root. It is
  gitignored and — verified — contains only empty tables (schema, zero rows),
  likely created by `migrate_schema.py`'s `DB_PATH_LOCAL` branch. Safe to
  delete.
- Generated clutter in the working dir, all correctly gitignored: `.coverage`,
  `coverage.xml`, `htmlcov/`, `__pycache__/` (root, `src/`, `tools/`),
  `.pytest_cache/`, `.venv/`. Coverage artifacts regenerate on every `pytest`
  run because coverage flags are hardwired in `addopts` (`pyproject.toml:67-74`).
- Branch debris: ~19 merged/stale local+remote branches worth pruning.
- Uncommitted mascot work (3 modified files, 4 untracked paths) should be
  committed or discarded.

## Verdict

**Maturity: active / usable.**

This is one of the healthier personal projects one could audit: real CI, 717
passing tests, honest documentation of both risks and internal invariants, and
recent focused commits. The engineering on the hard parts (idempotent invite
sending, crash-resilient navigation, session persistence) is thoughtful and
well-tested. Its weaknesses are structural rather than functional: the
`sys.path` import convention, two god modules (`linkedin.py`,
`linkedin_cli.py`), ~41% coverage of the automation core, minor doc drift, and
Spanish-language artifacts that violate the owner's own conventions. The
existential risk is external — LinkedIn ToS enforcement and selector churn —
which no amount of code quality removes. Worth continuing; the next cheap wins
are pruning branches/dead files, fixing the packaging convention, and finishing
the TUI migration.
