# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a LinkedIn networking automation CLI built with Python. The application uses Playwright for web automation, InquirerPy for the TUI interface, SQLModel for database operations, and includes both a main InquirerPy-based CLI and an experimental interactive CLI using prompt-toolkit.

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
# Main InquirerPy-based CLI
uv run linkedin_cli.py

# Project script
linkedin-cli
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
- `linkedin_cli.py` - **Main application**: InquirerPy-based CLI with rich interactive menus
- `src/tui/` - Full-screen **Textual** presentation layer (issue #24), entry point `linkedin-tui`. Coexists with the classic CLI and reuses the same business logic read-only. Design tokens live in `theme.py`, layout in `app.tcss`, screens under `screens/`. See `docs/tui-migration.md` for the migration plan and architecture invariants.

**Configuration (`src/config/`)**:
- `settings.py` - AppSettings class managing environment variables and app configuration
- Browser settings, automation parameters, and credential validation

### Data Flow

1. **Campaign Creation**: Users create campaigns with targeting criteria through InquirerPy forms
2. **Automation Execution**: LinkedInAutomation class processes campaigns using Playwright
3. **Data Storage**: All campaign data, contacts, and analytics stored in SQLite via SQLModel
4. **Progress Tracking**: Real-time updates through InquirerPy interface with rich formatting

### Key Design Patterns

- **Async Context Managers**: LinkedInAutomation uses `async with` pattern for resource management
- **Menu-based Navigation**: InquirerPy menus for intuitive CLI interaction
- **Database Sessions**: SQLModel sessions with proper cleanup using context managers
- **Environment-based Configuration**: Settings loaded from environment variables with fallbacks

### Dependencies

- **InquirerPy**: Primary CLI framework for interactive menus and forms
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
`close_browser` and `login` always write `session.json` for the active context
— persistent runs included — so a later transient run can resume a session a
persistent run established.

### File Structure Context

- `linkedin_cli.py` - Main entry point with InquirerPy-based CLI interface
- Application data stored in user home directory under `.linkedin-networking-cli/`