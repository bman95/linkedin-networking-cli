# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a LinkedIn networking automation CLI built with Python. The application uses Playwright for web automation, SQLModel for database operations, and ships one interactive interface: a full-screen Textual TUI (`src/tui/`, entry point `linkedin-tui`). A separate non-interactive entry point (`linkedin_run.py`, installed as `linkedin-run`) runs a single campaign headlessly for cron/systemd-timer scheduling. The classic InquirerPy-based `linkedin_cli.py` menu was retired in the issue #47 single-UI cutover once every flow reached TUI parity.

## Essential Commands

### Setup and Installation
```bash
# Install dependencies and sync environment
uv sync

# Install required Playwright browsers
uv run python -m playwright install chromium

# Set required environment variables
export LINKEDIN_EMAIL="your-linkedin-email@example.com"
export LINKEDIN_PASSWORD="your-password"
```

### Running the Application
```bash
# Full-screen TUI
uv run linkedin_tui.py
# or, via the installed entry point
linkedin-tui

# Non-interactive, single-campaign run (cron/systemd-timer)
linkedin-run --campaign <id-or-name> [--max N]
```

## Architecture Overview

### Core Components

**Database Layer (`src/database/`)**:
- `models.py` - SQLModel definitions for Campaign, Contact, Analytics, Settings
- `operations.py` - DatabaseManager class with CRUD operations
- Uses SQLite for local storage at `~/.linkedin-networking-cli/linkedin_networking.db`

**Automation Layer (`src/automation/`)**:
- `linkedin.py` - LinkedInAutomation class using Playwright for web scraping; also owns session persistence (see Browser Automation Notes)
- `interactions.py` - Async page-interaction helpers (waits, CAPTCHA detection, scrolling) shared by the automation engine
- Implements rate limiting, profile searching, and connection request automation

**UI Layer**:
- `src/tui/` - **Main application**: full-screen **Textual** presentation layer (issue #24), entry point `linkedin-tui`. The sole interactive UI (issue #47 retired the classic InquirerPy CLI once every flow reached parity). Design tokens live in `theme.py`, layout in `app.tcss`, screens under `screens/`. See `docs/tui-migration.md` for the migration history and architecture invariants.
- `linkedin_run.py` / `src/cli/runner.py` - Non-interactive `linkedin-run` entry point: runs one campaign's search-and-connect pass without prompts, for cron/systemd-timer scheduling. `src/cli/` also holds `helpers.py`/`automation_errors.py`, pure logic shared with the TUI.

**Configuration (`src/config/`)**:
- `settings.py` - AppSettings class managing environment variables and app configuration
- Browser settings, automation parameters, and credential validation
- The Rate Limiting tunables (`EDITABLE_SETTINGS`: connection delays, default
  daily limit, cooldown, search limit) are editable from the TUI's Settings
  screen and persist to `~/.linkedin-networking-cli/config.json`
  (`AppSettings.save_overrides`). Precedence: `config.json` > env > default —
  an in-app edit must win even when `.env` also sets the value. Credentials,
  browser identity, and LLM keys stay env-only. There is no configurable
  weekly invitation limit (removed 2026-07-11); LinkedIn's own weekly-limit
  modal is still detected and handled reactively.

### Data Flow

1. **Campaign Creation**: Users create campaigns with targeting criteria through the TUI's forms
2. **Automation Execution**: LinkedInAutomation class processes campaigns using Playwright
3. **Data Storage**: All campaign data, contacts, and analytics stored in SQLite via SQLModel
4. **Progress Tracking**: Real-time updates streamed into the TUI's run log (or stdout for `linkedin-run`)

### Key Design Patterns

- **Async Context Managers**: LinkedInAutomation uses `async with` pattern for resource management
- **Keyboard-first Navigation**: Textual screens/lists/command palette for full-screen TUI interaction
- **Database Sessions**: SQLModel sessions with proper cleanup using context managers
- **Environment-based Configuration**: Settings loaded from environment variables with fallbacks

### Dependencies

- **Textual**: Full-screen TUI framework for the interactive presentation layer
- **Playwright**: Web automation for LinkedIn interaction
- **SQLModel**: Type-safe database operations with SQLite
- **Rich**: Terminal formatting and progress display
- **Pydantic**: Data validation and settings management

### Browser Automation Notes

The LinkedIn automation requires careful handling:
- Implements random delays between actions to avoid detection
- Requires manual login verification on first run
- Respects daily connection limits and rate limiting

**Fingerprint coherence.** `start_browser` sets `locale` and `timezone_id` on
every context (persistent and transient) from `get_browser_settings`, with
host-derived defaults (`locale` → `en-US`; `timezone_id` → the host's IANA zone
detected from `TZ`/`/etc/localtime`/`/etc/timezone`, left unset — so the browser
uses its own host default — when no valid zone can be resolved). Override with
`BROWSER_LOCALE` / `BROWSER_TIMEZONE`. The **user-agent is intentionally left to
real Chrome** — its own UA already matches its platform and version, so no UA is
hardcoded (a hardcoded Windows UA on a Linux host would *create* the very
mismatch this avoids). `BROWSER_USER_AGENT` exists as an opt-in override and is
the user's responsibility to keep coherent; unset, it is never passed.

**Session persistence** is owned entirely by `LinkedInAutomation.start_browser`
and uses two complementary mechanisms, selected by how the browser launches:
- **Persistent context** (primary): when a real Chrome install is configured
  (custom executable or the `chrome` channel) and the persistent launch
  succeeds, `launch_persistent_context` reuses the on-disk Chrome profile under
  `~/.linkedin-networking-cli/browser_data/`. Cookies and login state live in
  that profile.
- **`storage_state` JSON** (fallback): on the transient (non-persistent) launch
  path — no real Chrome configured, a non-`chrome` channel, or a failed
  persistent launch — the context is loaded from `session.json`.

Only one mechanism is *read* per run (the persistent profile when present,
otherwise `~/.linkedin-networking-cli/session.json`). Writing is not exclusive:
`login` writes `session.json` on a confirmed login, and `close_browser` writes
it whenever the run's session is still believed healthy (`is_authenticated`) —
persistent runs included — so a later transient run can resume a session a
persistent run established. `close_browser` deliberately *skips* the write when
no authenticated session was confirmed (crash-recovery and failed-login
teardowns) **and** when the session was compromised mid-run: a detected
CAPTCHA/checkpoint/logout calls `_mark_session_compromised()` (clearing
`is_authenticated`), and as a belt-and-braces the write is also skipped when
the page is sitting on a login/challenge URL at close — so a degraded context
never clobbers a still-good `session.json`.

**Cross-process profile lock.** The persistent profile is guarded by
`<app_dir>/browser_profile.lock` (`pid:token` inside, `token` a random hex id
generated per `LinkedInAutomation` instance): `start_browser` acquires it
before `force_close_chrome`, so cleanup only ever kills *orphaned* Chrome from
a crashed run. Acquisition is atomic (payload written to a temp file, claimed
via `os.link`, retried on loss of a race — never a read-check-write), so two
near-simultaneous starters can never both believe they hold it; on a
filesystem without hard links it degrades, with a warning, to the pre-atomic
non-atomic claim rather than crashing. A lock naming a live foreign PID
raises `BrowserProfileBusyError` (surfaced as a clean "profile in use"
message) instead of killing a concurrent TUI/`linkedin-run` session; a
dead-PID lock is stale and reclaimed. A lock naming *our own* PID is judged
by its token against the in-process registry of live holders
(`_PROCESS_LOCK_TOKENS`): our own token → already ours (reclaimed without
touching the file); a token some live sibling `LinkedInAutomation` instance
in this process holds → busy (never silently kill a sibling run's Chrome);
any other token → a dead predecessor under our recycled PID number → stale
(`_pid_is_alive` on one's own PID is trivially true, so PID liveness alone
would self-deadlock the profile forever). The lock is released in
`close_browser`'s teardown, on a failed `start_browser`, and — if
`_refresh_context`'s crash-recovery relaunch fails before it can reacquire —
by a dedicated release in `_refresh_context` itself; otherwise it is held
(never released mid-gap) across that relaunch.

### File Structure Context

- `linkedin_tui.py` - Main entry point, launches the full-screen Textual TUI
- `linkedin_run.py` - Non-interactive entry point for a single scheduled campaign run
- Application data stored in user home directory under `.linkedin-networking-cli/`