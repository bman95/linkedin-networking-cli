# Design Proposals — linkedin-networking-cli

Date: 2026-07-06. These are the *rethink* items from the audit that should **not**
be implemented autonomously, because each needs either a live LinkedIn account to
validate against or a product decision only the owner can make. They are ordered by
leverage. Everything else from the audit (concrete bugs, security, data-layer
concurrency, tooling, and the two-UI de-duplication) has already been implemented —
see `FABLE5-IMPLEMENTATION-LOG.md` §"2026-07-06 audit fixes".

Each item states **why**, a concrete **approach**, the **risk**, and the
**prerequisite** (what unblocks it).

---

## 1. Cut over to a single UI (drop InquirerPy + `linkedin_cli.py`)

**Why.** The migration doc's own cutover gate — every classic flow has a TUI
equivalent at parity — is met. Each week both UIs coexist, behaviour drifts because
parity is maintained by hand-copying. The 2026-07-06 pass extracted the shared logic
(acceptance-rate, CSV, email masking, error mapping) into `src/cli/`, so the
*duplication* is gone, but two presentation layers still have to be kept in step.

**Approach.** Now that both UIs consume one shared core, the cutover is mechanical:
per flow, confirm the TUI screen matches the classic behaviour (the last known gap —
the browser-bound online location search / custom-geoUrn entry in Create/Edit — was
ported on 2026-07-07, see `campaign_form.py` + `tests/test_tui_location_search.py`),
then delete the classic flow. When the last flow is signed off, remove `linkedin_cli.py`,
the `InquirerPy` dependency, and the `linkedin-cli` interactive path (keep the new
non-interactive `run` subcommand — move it to `linkedin_tui` or a small headless
entry). Update `pyproject.toml` scripts and the README.

**Risk.** Low technically; it's a product/authorship decision. Irreversible deletion
of a working UI.

**Prerequisite.** Your per-flow sign-off (the migration doc reserves this for you),
and porting the deferred online-location-search path to the TUI first.

---

## 2. Read data from the Voyager API, not the DOM

**Why.** The tool uses the DOM as both its *sensor* (reading search results, profile
fields, connection state) and its *actuator* (clicking). The sensor half is the
fragile part — CSS/`data-view-name` selectors churn constantly and are the #1
real-world break (`scraping.py` is already stale against the SDUI rollout; see §6).
LinkedIn's own pages fetch their data from the internal **Voyager** JSON API, whose
schema changes far less often than markup.

**Approach.** While Playwright drives the page, subscribe to `page.on("response")`
and capture responses from `*/voyager/api/*`. Parse the JSON the page already
received — search hits, profile data, invitation state all flow through it — into the
existing `LinkedInProfile`/contact shapes. Keep the DOM strictly for *actions*
(clicking Connect/Send), where the fail-loud `selectors.py` registry already does its
job. This makes extraction resilient and removes most of `scraping.py`.

**Risk.** Medium. Voyager is undocumented and auth/CSRF-gated; parsing it is still
reverse-engineering, and doing so may carry its own ToS weight. Needs care to not
increase detection surface.

**Prerequisite.** A live session to capture real Voyager payloads and pin their shape
(record a few sanitized responses as fixtures — which also feeds §3 of the tooling
work: drift-catching tests).

---

## 3. Replace the profile-visiting connection checker with a Sent-Invitations diff

**Why.** The current checker visits *N* profiles to detect acceptance — the slowest,
most detection-visible approach available, and where three of the confirmed bugs lived
(the no-op wait, the tab leak, the unbounded scroll — all now fixed, but the algorithm
is still wrong). LinkedIn exposes a single **Sent invitations** page (My Network →
Manage → Sent) listing every pending invite.

**Approach.** Once per run, load the Sent-invitations page and read the set of
still-pending invitees. Diff it against the DB's `sent`/`possibly_sent` set: anything
that was pending last run and is now absent was **accepted or withdrawn** — confirm
against the connections list (or a single profile check only for the disappeared
ones). One or two page loads replace *N* profile visits.

**Risk.** Medium. Needs the Sent-invitations page selectors (not in the registry
today) and handles the accepted-vs-withdrawn ambiguity. Changes acceptance-detection
semantics, so it needs live validation before trusting the stats.

**Prerequisite.** Live DOM of the Sent-invitations page to build + verify selectors.

---

## 4. Model the *weekly* budget as the primary constraint  ✅ *partially implemented 2026-07-06*

**Why.** LinkedIn's binding limit is the ~weekly invitation cap (~100–200), not a
daily one. The app tracked only a daily counter and learned the weekly limit
*reactively*, by hitting the modal mid-run.

**Status.** A **proactive** rolling-7-day budget was added on 2026-07-06
(`WEEKLY_INVITATION_LIMIT` setting + `get_weekly_connection_count()` summing the
existing per-day rows + a pre-send check that stops cleanly before the wall). What
remains is *tuning* the default cap and confirming, against a live account, that the
rolling-7-day sum matches LinkedIn's actual reset window (it may be a fixed weekly
boundary rather than a trailing 7 days).

**Prerequisite.** Live observation of when your account's invite quota actually resets.

---

## 5. Non-interactive, randomized-cadence execution  ✅ *entry point implemented 2026-07-06*

**Why.** "Open the app, send 20 invites in one sitting" is the most bot-like schedule
possible; no amount of per-click jitter fixes cadence. The restart-safe counters
already support a better design: small batches at randomized times.

**Status.** A non-interactive `linkedin-cli run --campaign X [--max N]` entry point was
added on 2026-07-06 so a scheduler (cron / systemd-timer) can drive short sessions.
What remains is a **product decision**: the recommended operating mode becomes "3–5
invites per session, a few sessions a day at jittered times," with the TUI as the
monitoring/approval surface rather than the engine. Optionally ship a sample
systemd-timer / cron unit with randomized `OnCalendar`/`RandomizedDelaySec`.

**Prerequisite.** Your call on the default cadence, and live validation of the headless
send path (it sends real invites — validated manually, per the existing convention).

---

## 6. Rewrite `scraping.py` against current LinkedIn markup (or delete it)

**Why.** Every selector in `scraping.py` keys off pre-SDUI classes (`.pv-entity__*`,
`.pv-top-card__*`, …) that LinkedIn replaced. `extract_detailed_profile` almost
certainly returns empty fields silently today, with no evidence bundle (unlike the
fail-loud search path). The 2026-07-06 pass fixed the `get_open_to_work_status`
false-positive but left the stale selectors — they can't be fixed without live DOM.

**Approach.** Either (preferred) fold profile extraction into the Voyager approach
(§2), or re-derive the selectors from current markup and route them through the
central `selectors.py` registry so drift fails loud with evidence. If detailed
extraction isn't actually used, delete the module.

**Risk.** Low–medium. **Prerequisite.** Live profile DOM (or a decision to drop the
feature).

---

## 7. Make analytics an append-only event log; drop the denormalized aggregates

**Why.** The `Analytics` table is orphaned (never written outside tests) while stats
are computed live. Meanwhile `Campaign.total_sent/accepted/pending` are denormalized
and can lag (the TUI dashboard already documents this). Two half-solutions instead of
one source of truth.

**Approach.** Introduce an append-only `events` table (`invite_reserved`,
`invite_sent`, `invite_possibly_sent`, `accepted`, `limit_hit`, `captcha`,
`run_started`/`run_ended`, with `campaign_id` + timestamp). All stats become SQL
aggregations over it; delete the `Analytics` table and the drifting `Campaign.total_*`
columns (or make them a cached view). You get run history, weekly-budget data (§4), and
pacing telemetry for free. Emit events at the existing state-transition points in the
send tail.

**Risk.** Medium — a schema change plus rewiring every stat read. Needs the migration
story (§8) to land first. Best done together with §9 (the status state machine), since
both formalize the same `found → reserved → sent/possibly_sent → accepted` lifecycle.

**Prerequisite.** None external; but it's invasive enough to want your go-ahead and to
sequence after §8.

---

## 8. A real migration story (retire the hand-rolled startup DDL)

**Why.** Schema evolves via imperative `ALTER TABLE` + dedupe routines that run on
every startup (`_ensure_contact_reservation_token_column`, `_dedupe_contacts_*`,
`_ensure_contact_unique_index`). This is idempotent and works today, but doesn't scale
past the next couple of columns and has no versioning/rollback.

**Approach.** Adopt a lightweight versioned scheme: a `PRAGMA user_version` (or a
`schema_migrations` table) plus an ordered list of migration functions applied once and
recorded. Wrap the existing idempotent migrations as v1 so current DBs upgrade cleanly.
Alembic is the heavier alternative; for a single-user local SQLite app, a
`user_version` ladder is proportionate.

**Risk.** Medium — retrofitting migrations onto live user DBs is exactly where data
gets damaged if rushed, which is why this was *not* done autonomously. Needs testing
against a copy of a real, populated DB.

**Prerequisite.** Your go-ahead + a real populated DB to test the upgrade path against.

---

## 9. Promote the contact lifecycle to an explicit state machine  ✅ *enum implemented 2026-07-06*

**Why.** The `found → reserved → sent/possibly_sent → accepted` protocol is the
best-engineered idea in the codebase, but it lived as ~640 lines of nested try/finally
inside `_attempt_connect` plus string literals scattered across three layers.

**Status.** A `ContactStatus` str-enum + single-source stat groupings were added on
2026-07-06, removing the scattered string literals from the stat paths. The larger
refactor remains: define the *transitions* in one module and have the automation call
`transition(contact, event)` instead of open-coding status writes. That refactor also
gives the `linkedin.py` split (the last roadmap item) its natural seams: browser/session
lifecycle · action primitives · workflows · the state machine.

**Risk.** Medium (touches the least-covered, highest-value module). **Prerequisite.**
Do it after raising `_attempt_connect` coverage, and pair it with §7.

---

## 10. Deferred TUI refactors (safe, mechanical — just not yet done)

- **Worker-guard mixin.** The `_load_generation` / capture-`app` / `_marshal_*` /
  `is_running`+`RuntimeError`+`is_mounted`+generation race-guard is copy-pasted across
  ~7 screens. Extract one mixin. Low risk; deferred only to keep the 2026-07-06 pass
  scoped.
- **Single-source navigation.** Home destinations are enumerated in four parallel lists
  (`home.py` `NAV_ITEMS`, its number-key bindings, `commands.py` `_targets`, and the
  screen imports) that must be kept in sync by hand. Derive them from one registry.

**Prerequisite.** None — these are safe follow-ups, left out of the audit pass only for
scope.
