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
| Manage Campaigns | `CampaignsScreen` → `CampaignDetailScreen` / `CampaignEditScreen` | `get_campaigns`, `get_campaign`, `update_campaign`, `delete_campaign`, `get_contacts` | DB write + CSV export | **done** (view / edit / toggle / export / delete) |
| Create Campaign | `CreateCampaignScreen` | `create_campaign` | DB write | **done**; online location search + custom geoUrn deferred |
| Execute Campaign | `ExecuteCampaignScreen` | `LinkedInAutomation.search_and_connect`, Playwright | browser, network, sends | **done** (user-initiated run) |
| Check Connections | `CheckConnectionsScreen` | `smart_connection_checker` / `check_connection_status` | browser, network | **done** (user-initiated run) |
| Extract Profile Data | `ExtractProfilesScreen` | `extract_detailed_profile` | browser, network | **done** (user-initiated run) |
| Exit | key binding (`q`) / command palette | — | — | done |

## 4. Flow-by-flow migration order, with rationale

1. **App shell + design system + read-only screens (this PR).** Lay down the
   theme, the persistent frame, command-palette navigation, and the
   highest-leverage read-only screens (Dashboard, Settings) on top of the #37
   Campaigns slice. Read-only first means we lock the look, the navigation
   model, and the threaded-worker data-flow conventions with **zero** risk of
   side effects.
2. **Write flow: Create Campaign (done).** The first screen that mutates state.
   Introduces the form/validation patterns (inputs, selects, confirmation) and
   the "instant in-place feedback after a write" interaction. Lower risk than
   automation because there's no browser — which is exactly why the classic
   flow's "🔎 Search location online (requires login)" option (and the
   custom-geoUrn entry it falls back to) is **deferred**: both drive Playwright +
   a LinkedIn login, so they belong with the automation slice. The static
   location list, network degree, and industry are at full parity; `Any`
   location/industry persist as `None`, and the same validation rules apply
   (non-empty name, daily limit 1–100, `{name}` required in the message).
3. **Automation flows: Execute / Check Connections / Extract Profile Data (done).**
   The hardest slice: long-running async Playwright work with live in-place
   progress and credential gating. A shared `AutomationRunScreen` base
   (`automation_run.py`) encodes one shape — **gate → select → confirm → run
   (streaming log) → summary / error** — so the three screens differ only in
   their selection widgets, the async automation body, and the summary. The run
   drives `asyncio.run` inside a `@work(thread=True)` worker (mirroring the
   classic `asyncio.run(run_automation())`), and the automation's
   `progress_callback` streams lines into a `RichLog` via `call_from_thread`.
   Typed automation exceptions (CAPTCHA / rate-limit / auth / landing / selector)
   map to the same actionable stop messages as the classic
   `_report_automation_failure`, via `automation_errors.describe_automation_error`,
   plus the saved evidence path. The browser run is **user-initiated**: nothing
   runs until a campaign/mode is selected and confirmed with a *second* `ctrl+r`.
   `run_body` is the single seam tests override to exercise the
   run/log/summary/error pipeline without a browser; the live run itself is
   validated manually (it sends real invites). Cancellation mid-run is not
   offered — the automation methods take no cancel token — so the screen says
   "keep this open until it finishes"; `esc` after completion returns.
4. **Cutover.** Once every flow has a TUI equivalent at parity, drop InquirerPy.
   Every classic main-menu flow now has a TUI equivalent. The only deferred
   parity items are the browser-bound **online location search** / **custom
   geoUrn** entry in Create/Edit; cutover itself (removing InquirerPy and
   `linkedin_cli.py`) is gated on the owner's per-flow sign-off.

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

A registered Textual `Theme` named `linkedin`. The brand blue `#0A66C2` (the same
`BRAND_BLUE` the classic CLI uses, and the value `test_brand_theme_is_active`
pins) stays the **identity** — solid fills, focus, active state — but the rest of
the palette is a calm, modern dark scheme in the spirit of 2025/2026 terminal
aesthetics (Tokyo Night / Catppuccin): a deep, slightly cool base layered by
*elevation*, soft accents used with restraint, and desaturated semantics. A
brighter blue carries text accents where the deep brand blue would read
low-contrast on dark. Screens reference **semantic tokens** (`$primary`,
`$panel`, `$surface`, `$text`, `$text-muted`, `$success`, `$warning`, `$error`)
— never raw hex — so colour lives in one place.

| Token | Value | Role |
| --- | --- | --- |
| `primary` | `#0A66C2` | brand identity: solid fills, focus, active row, accent rule |
| `secondary` / `accent` | `#4D9FFF` | brighter blue: text accents/links legible on dark |
| `success` | `#4FB477` | healthy/active states, positive rates |
| `warning` | `#E0A65B` | amber — quota at/over the daily cap |
| `error` | `#E5615B` | soft red — error / degraded states |
| `background` / `surface` / `panel` | `#11151A` / `#161B22` / `#1C222B` | deep, cool, calm elevation |
| `foreground` / `text-muted` | `#E4E9F0` / `#8A95A6` | body text / captions |

> **First-parse variable rule.** `app.tcss` is parsed once with the *default*
> theme before `on_mount` selects `linkedin`, so only built-in tokens
> (`$text-muted`, `$text-disabled`) and auto-generated variants
> (`$surface-lighten-1`, `$panel-darken-1`, …) resolve there. A brand-new custom
> theme variable referenced in CSS raises `UnresolvedVariableError` and breaks
> the *whole* stylesheet — so the dim/elevation tiers reuse those built-ins
> rather than new variables.

### Layout (structure) — `src/tui/app.tcss`

The external stylesheet (loaded via `App.CSS_PATH`) owns spacing, elevation,
focus, grids, and sizing. It reads the theme's tokens, so it carries no hex.
Loaded once at the app level — not per-screen ad hoc styles.

**Design language.** Separation comes from *elevation* (surface → panel) and
*whitespace*, not boxed borders, with element idioms taken from Claude Code's own
terminal UI (its leaked design-system components were studied directly):

- **Selection is a `❯` pointer + recoloured text**, not a background bar — the
  pointer is reserved on every nav row (coloured like the surface) and revealed
  in the accent on the highlighted one, exactly like Claude Code's `ListItem`.
- **The foot is a dim hint bar** (`key action  ·  …`, bold keys, muted line) —
  not Textual's chunky key-cap `Footer`, whose corner letters read dated.
- **Tables** drop their wrapping box; a *muted* selection tint (`$primary 30%`,
  echoing Claude Code's desaturated selection blue) marks the current row, over
  a quiet muted header and a barely-there zebra.
- **Stat cards are borderless tiles**; section labels are dim upper-case
  *eyebrows* (matching the home's `NAVIGATE`) with no boxed rules.
- **A mascot + a consistent mark.** The home hero is **"Bit"**, a pixel robot
  (`MASCOT` in `home.py`): a head with binary `0`/`1` eyes (the app is a LinkedIn
  *bot*, given a digital identity) over a torso whose chest says `in`. Every
  sub-screen carries the compact echo of the same idea — a two-tile chip, `in`
  on the brand blue + a `01` *bit* on the accent (`BADGE` in `base.py`) — in its
  breadcrumb (`in 01  LinkedIn Networking  ·  <Screen>`). There is no generic
  Textual `Header` anywhere; the masthead is the title bar.

This is the deliberate move away from the boxed-in, outline-everything look
toward a calm, modern terminal surface.

> Note: `src/styles.css` was a pre-existing **orphaned** stylesheet from an early
> experiment (Buttons/Switches/forms that don't exist), referenced nowhere. It
> has since been deleted in a cleanup pass.

### App shell — `src/tui/app.py` + `src/tui/screens/base.py`

- **Persistent frame.** Every data screen opens with the shared **brand
  masthead** (`in 01  LinkedIn Networking  ·  <Screen>` — the chip + a breadcrumb
  to the current location, replacing Textual's generic `Header`) and closes with
  a pinned **dim hint bar** (`key action  ·  …`). `BaseScreen` provides this
  shared chrome (masthead via `masthead_markup`, hint bar via `hint_markup`) and
  the shared `Back` / `Quit` bindings, so navigation is identical everywhere. The
  **home launcher** (`HomeScreen`, the entry screen)
  is the one screen without a `Back` binding (it has nowhere to pop to); `q`
  quits.
- **No scroll-behind.** Screens are full opaque `Screen` overlays pushed with
  `push_screen`, so switching is in-place with nothing showing through. Each
  screen fills the viewport; only the inner content region scrolls
  (`overflow-y: auto`), never the whole screen, so the header/footer stay fixed.
- **Keyboard-first navigation.** The home is a curated launcher: the **"Bit"
  mascot** beside the wordmark + tagline, a **live one-line workspace summary** (worker-loaded — credential status for onboarding, else campaign
  count / today's quota / readiness), and a focused nav list of rich rows
  (title + description) whose selection is a `❯` pointer and an accent-recoloured
  title — not a loud full-width bar. Navigation is fast: `↑`/`↓` + `enter`, and
  the number keys **`1`–`4` jump** straight to a destination. Textual's command
  palette (`ctrl+p`), extended with a `NavCommands` provider, offers the same
  destinations from anywhere; `COMMANDS` *extends* the built-in providers, so the
  default system commands (theme switch, quit, …) remain.

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

Refreshing a screen (`r`) sets its status line to `Refreshing…` before the
worker starts, so the action gives immediate feedback even though the existing
data stays on screen until the new snapshot lands. The home runs the same
contract for its workspace summary, refreshing it on every `on_screen_resume`
so counts stay current after a visit to another screen.

## 7. Open visual / theme decisions

- **Light theme.** Only one calm dark theme is registered now. A light variant
  (and exposing theme switching via the command palette) is deferred.
- **Emoji.** The classic CLI decorates panels with emoji (📊, 🔐, …). The TUI
  deliberately drops decorative emoji for a cleaner, calmer look while keeping
  the **text labels** identical for parity. If the owner prefers emoji retained,
  it's a one-line-per-label change — flagged as an explicit decision rather than
  a silent divergence.
- **Stat-card density / iconography.** The dashboard uses a 3×2 grid of uniform
  borderless stat tiles (elevation, not outlines). Whether to add
  sparklines/trends (from the `Analytics` table) is a later enhancement once the
  read-only baseline is signed off.

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
- **Method/attribute clashes with Textual internals.** Widget/Screen subclasses
  must not shadow framework members. Methods: e.g. `_render` is a Textual
  internal. **Attributes too:** `AutomationRunScreen` first used `self._running`
  for "a run is in flight" — but `MessagePump._running` is a Textual attribute
  that is `True` for every mounted node, so the start guard `if self._running:
  return` became a permanent no-op and `ctrl+r` did nothing. The run-state flags
  are now namespaced (`_run_active` / `_run_done` / `_run_confirming` /
  `_run_can_start`). Use distinct private names for state, not just methods.
- **Packaging.** `app.tcss` lives next to `app.py` so `CSS_PATH` resolves, and
  the wheel's `only-include = ["src", …]` ships non-`.py` files — verified by
  inspecting the built wheel for `tui/app.tcss`.
- **Secrets.** The Settings screen never renders raw credentials: the email is
  masked and the password is shown only as `Set` / `Not set`. The raw values are
  read solely to compute those display-safe flags.
