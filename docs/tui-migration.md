# TUI Migration Plan (issue #24)

Modernizing the CLI presentation layer to a full-screen Textual TUI.

## 1. Purpose & acceptance bar

The classic InquirerPy CLI (`linkedin_cli.py`) works as a sequence of prompts:
each step prints its result into the scrollback, so the experience accumulates
output instead of rendering in place. Issue #24 replaces the **presentation
layer only** with a full-screen TUI built on [Textual](https://textual.textualize.io/)
— same ecosystem as Rich (already a dependency) and asyncio-native (fits the
existing async Playwright stack).

The acceptance bar, set by the owner:

> An attractive, easy-to-use CLI with a *very curated* user experience.

Success is the **quality of the experience** — full-screen, smooth, navigable,
calm, like Claude Code / Codex — not line count. #24 is an **epic** that stays
open until every flow is migrated.

## 2. Architecture constraints (do not break)

These invariants are load-bearing and protected by tests. Every new screen must
respect them.

- **Presentation layer only.** Business logic under `src/automation`,
  `src/database`, `src/config` is UI-agnostic and reused **as-is**. If a change
  seems to need touching business logic, stop and reconsider — it almost
  certainly doesn't.
- **The classic CLI keeps working.** `linkedin_cli.py` and the InquirerPy
  dependency stay until full parity is reached. Both entry points
  (`linkedin-cli`, `linkedin-tui`) ship side by side during the migration.
- **Lazy package import (PEP 562).** `tui/__init__.py` exposes `LinkedInTUI`
  lazily and must **not** eagerly import `tui.app` or any `tui.screens.*` module.
  Screen modules call `get_logger(__name__)` at module scope, which the first
  time runs `LoggerSetup.setup()` with production defaults (creating
  `~/.linkedin-networking-cli/logs`) — an import-time side effect that crashes
  on a read-only/sandboxed home. Guarded by
  `test_importing_package_does_not_eagerly_load_app`.
- **Bootstrap order.** Both entry points (`linkedin_tui.py`,
  `src/tui/__main__.py`) call `LoggerSetup.setup()` **before** importing the app
  modules. `get_logger(__name__)` at module scope is fine only because those
  modules are imported after setup.
- **Degrade gracefully.** If `AppSettings`/`DatabaseManager` init fails, the app
  runs with `db_manager = None` and screens show a designed "unavailable" state
  rather than crashing — mirroring the classic CLI's demo-mode fallback.
- **Threaded-worker race discipline.** `DatabaseManager` reads (and
  `AppSettings()` construction) are synchronous/blocking and may touch disk, so
  every data load runs in a `@work(thread=True, exclusive=True)` worker with the
  full safety contract in §6.

## 3. Target screen map

Each classic flow maps to one TUI screen. Read-only screens come first (zero
side effects); write and automation flows follow.

| Classic flow | TUI screen | Data / deps | Side effects | Status |
| --- | --- | --- | --- | --- |
| Dashboard | `DashboardScreen` | `get_dashboard_stats`, `get_campaigns`, `get_daily_connection_count`, `AppSettings` | none (read-only) | **done (this PR)** |
| Settings | `SettingsScreen` | `AppSettings`, `get_daily_connection_count` | none (read-only) | **done (this PR)** |
| Manage Campaigns | `CampaignsScreen` | `get_campaigns` | none (read-only) | done (#37); detail/edit later |
| Create Campaign | campaign form screen | `create_campaign` | DB write | later — first *write* flow |
| Execute Campaign | automation/progress screen | `LinkedInAutomation`, Playwright | browser, network, sends | later — highest risk |
| Check Connections | automation/progress screen | `LinkedInAutomation` | browser, network | later |
| Extract Profile Data | automation/progress screen | `LinkedInAutomation` | browser, network | later |
| Exit | key binding (`q`) / command palette | — | — | done |

## 4. Flow-by-flow migration order, with rationale

1. **App shell + design system + read-only screens (this PR).** Lay down the
   theme, the persistent frame, command-palette navigation, and the
   highest-leverage read-only screens (Dashboard, Settings) on top of the #37
   Campaigns slice. Read-only first means we lock the look, the navigation
   model, and the threaded-worker data-flow conventions with **zero** risk of
   side effects.
2. **Write flow: Create Campaign.** The first screen that mutates state.
   Introduces the form/validation patterns (inputs, selects, confirmation) and
   the "instant in-place feedback after a write" interaction. Lower risk than
   automation because there's no browser.
3. **Automation flows: Execute / Check Connections / Extract Profile Data.**
   The hardest slice: long-running async Playwright work with live in-place
   progress, cancellation, and credential gating. These are deferred until the
   shell, theme, and data-flow conventions are proven, and until a dedicated
   progress/worker design exists. They also require a coherent story for
   surfacing CAPTCHA/login prompts inside the TUI.
4. **Cutover.** Once every flow has a TUI equivalent at parity, drop InquirerPy.

Rationale: de-risk by deferring side-effecting flows. Each stage builds on the
proven conventions of the previous one, so the experience stays coherent as the
surface grows.

## 5. Parity / cutover strategy for dropping InquirerPy

The TUI coexists with `linkedin_cli.py` throughout. Cutover is gated on a
per-flow parity checklist:

- **Vocabulary parity.** The TUI reuses the classic CLI's labels and wording so
  users aren't relearning the tool. Verified examples already in place:
  - Dashboard: "Active Campaigns", "Total Connections", "Success Rate"
    (the label is *Success Rate*, value is the acceptance rate).
  - Campaigns table: Name / Status / Sent / Accepted / Rate / Daily Limit.
  - Settings: "Status: Configured/Not configured", masked email
    (`joh***@example.com`), "Password: Set/Not set", "Channel", "Executable",
    "Headless Mode", "Viewport", "User Data Dir", "Connection Delay",
    "Daily Connection Limit", "Used Today", "Inter-session Cooldown",
    "Search Limit", "App Directory", "Database", "Session Data", "Browser Data".
- **Behavior parity.** Each migrated flow does what its classic counterpart
  does, including the demo/degraded fallbacks.
- **Cutover.** InquirerPy and `linkedin_cli.py` are removed only when every flow
  in §3 has a signed-off TUI equivalent. Until then both entry points ship.

## 6. Design system

One source of truth per concern.

### Theme (colour) — `src/tui/theme.py`

A registered Textual `Theme` named `linkedin`, anchored on the brand blue
`#0A66C2` (the same `BRAND_BLUE` the classic CLI uses). Screens reference
**semantic tokens** (`$primary`, `$panel`, `$surface`, `$text`, `$text-muted`,
`$success`, `$warning`, `$error`) — never raw hex — so colour lives in exactly
one place and contrast is derived consistently.

| Token | Value | Role |
| --- | --- | --- |
| `primary` / `accent` | `#0A66C2` | brand accent: borders, focus, headings |
| `secondary` | `#378FE9` | highlighted rows, secondary accents |
| `success` | `#057642` | healthy/active states, positive rates |
| `warning` | `#B24020` | quota at/over the daily cap |
| `error` | `#CC1016` | error / degraded states |
| `background` / `surface` / `panel` | `#1B1F23` / `#22272E` / `#2D333B` | calm dark layering |
| `foreground` / `text-muted` | `#E8E8E8` / `#9AA5B1` | body text / captions |

### Layout (structure) — `src/tui/app.tcss`

The external stylesheet (loaded via `App.CSS_PATH`) owns spacing, borders, focus
styling, grids, and sizing. It reads the theme's tokens, so it carries no hex.
Loaded once at the app level — not per-screen ad hoc styles.

> Note: `src/styles.css` is a pre-existing **orphaned** stylesheet from an early
> experiment (Buttons/Switches/forms that don't exist). It is referenced
> nowhere. It is left untouched here (not our debt to remove) but should be
> deleted in a later cleanup once nothing else references it.

### App shell — `src/tui/app.py` + `src/tui/screens/base.py`

- **Persistent frame.** Every screen has a pinned `Header` (app title) and a
  pinned `Footer` whose key hints are always discoverable. `BaseScreen` provides
  the shared chrome (header, a bold context title, footer) and the shared
  `Back` / `Quit` bindings, so navigation is identical everywhere. The main menu
  is the one screen that overrides `escape` (it has nowhere to pop back to).
- **No scroll-behind.** Screens are full opaque `Screen` overlays pushed with
  `push_screen`, so switching is in-place with nothing showing through. Each
  screen fills the viewport; only the inner content region scrolls
  (`overflow-y: auto`), never the whole screen, so the header/footer stay fixed.
- **Keyboard-first navigation.** A focused main menu (Enter activates the
  highlighted item on first launch), plus Textual's command palette (`ctrl+p`)
  extended with a `NavCommands` provider so power users can jump to Dashboard /
  Campaigns / Settings / Quit from anywhere. `COMMANDS` *extends* the built-in
  providers, so the default system commands (theme switch, quit, …) remain.

### State design

Every data screen renders designed **loading / populated / empty / error /
degraded** states — never a raw traceback. The threaded-worker contract:

1. `load_*()` runs on the UI thread, bumps a monotonic `_load_generation`,
   captures `self.app`, and hands both to the worker. (Resolving `self.app`
   inside the deferred worker body would raise if the screen was popped first.)
2. The `@work(thread=True, exclusive=True)` worker fetches off the event loop,
   wrapping reads in try/except to turn failures into a friendly in-place error.
3. `_marshal_populate(app, generation, …)` guards `app.is_running` and catches
   `RuntimeError`, so a late callback after quit is a silent no-op (no hang).
4. `_populate(generation, …)` bails if `not self.is_mounted` and drops results
   whose generation no longer matches the latest — a slower, superseded load can
   never overwrite a newer snapshot.

## 7. Open visual / theme decisions

- **Light theme.** Only one calm dark theme is registered now. A light variant
  (and exposing theme switching via the command palette) is deferred.
- **Emoji.** The classic CLI decorates panels with emoji (📊, 🔐, …). The TUI
  deliberately drops decorative emoji for a cleaner, calmer look while keeping
  the **text labels** identical for parity. If the owner prefers emoji retained,
  it's a one-line-per-label change — flagged as an explicit decision rather than
  a silent divergence.
- **Stat-card density / iconography.** The dashboard uses a 3×2 grid of
  bordered stat cards. Whether to add sparklines/trends (from the `Analytics`
  table) is a later enhancement once the read-only baseline is signed off.

## 8. Testing strategy

All TUI tests use Textual's headless harness (`App.run_test()` + `Pilot`) with a
real `DatabaseManager` on a temp SQLite path (seeded via `create_campaign` /
`create_contact`) — the actual data-flow path, not mocks. No credentials and no
browser are needed, so the suite runs in CI. Worker-populated assertions poll a
status line; race invariants (stale-load drop, quit-mid-load, UI-thread app
capture) get dedicated deterministic tests, mirroring the #37 approach. See
`tests/test_tui.py` and `tests/test_tui_dashboard.py`.

## 9. Risks & edge cases

- **Bootstrap order / PEP 562.** New screen modules must never be imported
  eagerly by `tui/__init__.py` or `tui/screens/__init__.py`. Covered by the
  import-probe test.
- **`AppSettings()` writes to disk** (`app_dir.mkdir`). It is constructed inside
  the worker, wrapped in try/except, so a read-only home degrades to a friendly
  state rather than crashing.
- **Threaded-worker races.** Capture `self.app` on the UI thread; guard
  `app.is_running` + `RuntimeError`, `self.is_mounted`, and stale generations.
  Each has a regression test.
- **Method-name clashes with Textual internals.** Widget/Screen subclasses must
  not shadow framework methods (e.g. `_render` is a Textual internal). Use
  distinct private names.
- **Packaging.** `app.tcss` lives next to `app.py` so `CSS_PATH` resolves, and
  the wheel's `only-include = ["src", …]` ships non-`.py` files — verified by
  inspecting the built wheel for `tui/app.tcss`.
- **Secrets.** The Settings screen never renders raw credentials: the email is
  masked and the password is shown only as `Set` / `Not set`. The raw values are
  read solely to compute those display-safe flags.
