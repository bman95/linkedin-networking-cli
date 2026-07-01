# FABLE5 Next Steps — linkedin-networking-cli

Prioritized action plan derived from `FABLE5-AUDIT.md` (2026-07-01, `master` @ `56f1ffe`).

**SAFE tag:** `SAFE: YES` = an AI agent may implement it autonomously tonight (local changes to
code, tests, or docs only; verified by the test suite). `SAFE: NO` = requires human decisions,
credentials, remote/git-push actions, or a live LinkedIn account.

---

## Quick wins

| # | Step | Priority | SAFE | Effort |
|---|------|----------|------|--------|
| 1 | Repo hygiene sweep (stray DB, dead CSS, doc drift, migration scripts) | P0 | YES | ~1 h |
| 2 | Translate `TODOS.md` and Spanish comments to English | P1 | YES | ~30 min |
| 3 | Guard `int(os.getenv(...))` parsing in settings | P1 | YES | ~1 h |
| 4 | Fix the two "coroutine never awaited" test warnings | P1 | YES | ~30 min |

### 1. Repo hygiene sweep — P0 — SAFE: YES — ~1 h

**Rationale:** Cheap, zero-risk cleanup of debris the audit flagged: stray empty
`linkedin_networking.db` at repo root (gitignored, verified schema-only with zero rows), dead
`src/styles.css` (TUI uses `src/tui/app.tcss`), `CLAUDE.md:7` still describing a prompt-toolkit
CLI that no longer exists, and completed one-off `migrate_database.py` / `migrate_schema.py`
sitting at top level.

**Acceptance criteria:**
- `linkedin_networking.db` removed from repo root *after re-verifying it contains zero rows*
  (e.g. `sqlite3 linkedin_networking.db "SELECT count(*) FROM contact;"` etc.).
- `src/styles.css` deleted; `grep -r "styles.css" src/ tests/ linkedin_cli.py linkedin_tui.py`
  returns nothing.
- `CLAUDE.md` no longer mentions prompt-toolkit; overview matches the actual two UIs
  (InquirerPy CLI + Textual TUI).
- `migrate_database.py` / `migrate_schema.py` moved to `scripts/` (or deleted if truly one-shot —
  moving is the conservative choice for an autonomous agent) with a one-line note in each header.
- Full test suite still passes (717 tests).

### 2. English-only compliance — P1 — SAFE: YES — ~30 min

**Rationale:** `TODOS.md` is entirely in Spanish and `pyproject.toml:39-40` has Spanish comments
("Para tests paralelos", "Para mockear tiempo"), violating the owner's global English-only rule.

**Acceptance criteria:**
- `TODOS.md` fully translated to English, preserving structure, status markers, and all IDs/codes.
- Spanish comments in `pyproject.toml` translated.
- `grep -rniE "para |descripción|búsqueda|pendiente" --include="*.py" --include="*.toml" --include="*.md" .`
  (excluding `.venv/`, `FABLE5-*.md`) shows no remaining Spanish in tracked source/docs.

### 3. Guard env-var parsing in settings — P1 — SAFE: YES — ~1 h

**Rationale:** `src/config/settings.py:205-217` and `:258-264` call `int(os.getenv(...))`
directly; a malformed value like `DAILY_CONNECTION_LIMIT=twenty` crashes startup with an
unhandled `ValueError`.

**Acceptance criteria:**
- A small helper (e.g. `_env_int(name, default)`) parses int env vars; on malformed values it
  logs a warning and falls back to the default instead of raising.
- New unit tests cover: valid value, missing value (default), malformed value (default + warning),
  for both blocks (`205-217` and `258-264`).
- Full suite passes; no behavior change for valid values.

### 4. Fix async-mock test warnings — P1 — SAFE: YES — ~30 min

**Rationale:** Two `RuntimeWarning: coroutine ... was never awaited` from
`tests/test_linkedin_automation.py` (TestNavigationGuardWiring) and `tests/test_navigation.py`
(TestGotoRetry). Mock hygiene, not product bugs, but warnings mask future real ones.

**Acceptance criteria:**
- `uv run pytest -W error::RuntimeWarning tests/test_linkedin_automation.py tests/test_navigation.py`
  passes (or, at minimum, `pytest` output shows zero `never awaited` warnings).
- No production code changed — test-side mocks fixed with `AsyncMock`/awaits.

---

## Structural work

### 5. Kill the `sys.path.append` import hack — P1 — SAFE: YES — ~3-5 h

**Rationale:** ~10 `src/` modules (e.g. `src/config/settings.py:7`,
`src/automation/linkedin.py:26`, `src/database/operations.py:10`) prepend paths manually and
import packages as top-level (`from utils.logging import ...`); the wheel build compensates with
a custom source mapping (`pyproject.toml:51-59`). Fragile, non-standard, and confuses tooling
(IDE resolution, coverage, future contributors and agents alike).

**Acceptance criteria:**
- All `sys.path.append` lines in `src/` removed.
- Imports resolve via a proper convention — either a real `src`-layout package
  (`from src.utils.logging import ...` everywhere + standard hatch src layout) or top-level
  packages installed editable via `uv sync` — one convention, applied consistently.
- `pyproject.toml` wheel config simplified accordingly; `uv build` produces a wheel that installs
  and imports cleanly in a fresh Python 3.13 environment (this was already verified once per
  `TODOS.md`, so keep it true).
- `linkedin-cli` and `linkedin-tui` entry points still work (`--help`/menu renders).
- Full test suite passes; coverage config (`source`) updated to match the new layout.

### 6. Bring `linkedin_cli.py` under coverage and start splitting it — P2 — SAFE: YES — ~4-6 h

**Rationale:** `linkedin_cli.py` is a 1,664-line monolith at repo root mixing menu UI,
orchestration, and formatting — and it is excluded from coverage measurement entirely
(`pyproject.toml:82`, `source = ["src"]`) despite `tests/test_cli_helpers.py` targeting it.
You cannot manage what you do not measure.

**Acceptance criteria:**
- `linkedin_cli.py` (or its extracted modules) included in coverage `source`; the coverage report
  shows a real number for it.
- Pure helpers (formatting, campaign-form assembly, CSV export glue) extracted into an importable
  module (e.g. `src/cli/`) with the entry point reduced to menu wiring; no behavior change.
- Tests in `tests/test_cli_helpers.py` updated to import from the new location; suite passes.
- Do the extraction incrementally (helpers first); a full decomposition can span several sessions.

### 7. Raise automation-core coverage before touching `linkedin.py` — P2 — SAFE: YES — ~4-8 h

**Rationale:** The automation core is only ~41.5% line-covered. `src/automation/linkedin.py`
(3,000 lines) is the god module most in need of a split, but splitting it at 41% coverage is how
regressions happen. Order matters: tests first, split second.

**Acceptance criteria:**
- Coverage of `src/automation/` raised meaningfully (target: `linkedin.py`, `navigation.py`,
  `checker.py` each ≥ 60% lines, browser fully mocked as today).
- New tests focus on the highest-risk paths: invite-send tail (`reserved`/`possibly_sent`
  transitions), per-day counter enforcement, backoff on restriction/CAPTCHA, session
  persistence selection logic.
- No production code changed except trivially testability-motivated seams (and each one justified
  in the commit message when Bryan later commits).

### 8. Split `src/automation/linkedin.py` (3,000 lines) — P2 — SAFE: NO — 1-2 days

**Rationale:** The main god module. Marked NOT safe for tonight — not because it touches anything
external, but because a large-scale refactor of the least-covered, highest-value module should
happen (a) after step 7 raises the safety net and (b) with Bryan deciding the target seams
(e.g. session management vs. invite pipeline vs. search/scrape orchestration). Doing it blind
overnight risks subtle regressions the current suite would not catch.

**Acceptance criteria:**
- `linkedin.py` reduced to a facade/orchestrator (< ~800 lines) delegating to focused modules;
  public API (`LinkedInAutomation`, `async with` usage) unchanged.
- All 717+ tests pass; coverage does not drop.
- `CLAUDE.md` architecture section updated.

---

## Human-in-the-loop

### 9. Resolve uncommitted mascot work and prune ~19 stale branches — P1 — SAFE: NO — ~30 min (Bryan)

**Rationale:** Uncommitted mascot home-screen work (3 modified files, 4 untracked paths) should be
committed or discarded — that is a product/authorship decision, and committing is explicitly out
of scope for an agent here. Separately, 5 local + ~14 remote `claude/*`/`feat/*` branches need
pruning, which means deleting remote branches — an external, irreversible action.

**Acceptance criteria:**
- Mascot work either committed on a branch/master or deliberately discarded.
- Merged branches deleted locally and on the remote; `git branch -a` shows only live lines of work.

### 10. Live-fire validation and TUI migration continuation — P2 — SAFE: NO — ongoing

**Rationale:** The dominant risk is external: LinkedIn ToS enforcement and selector drift. Only a
periodic run against a real account (Bryan's call, Bryan's account) validates `selectors.py` and
the login/search/send paths end-to-end. The Textual TUI migration (`docs/tui-migration.md`) is
disciplined and mid-flight; which screen to port next and whether write-paths move to the TUI are
product decisions.

**Acceptance criteria:**
- A dated smoke-run checklist result (login, one search, zero or one real invite) recorded, e.g.
  in `docs/`; selector breakages filed as issues.
- Next TUI screen chosen and its per-screen status updated in `docs/tui-migration.md`.

---

## Suggested tonight (autonomous agent scope)

Steps **1, 2, 3, 4** fully, then **5**, and as much of **6** and **7** as time allows — in that
order, running the full suite after each step. Steps 8-10 wait for Bryan.
