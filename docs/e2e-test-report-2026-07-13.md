# End-to-End Test Report — 2026-07-13

Full E2E pass over every TUI view and every non-browser software surface,
run against the working tree as of commit `5613195` plus the uncommitted
AI-assist/campaign-form changes.

## Scope and environment

- **Platform:** WSL2 (Linux 6.6), Python 3.13, Textual headless harness
  (`App.run_test()` + `Pilot`), real local Ollama (`gemma3:1b`) for the AI
  assist flow.
- **Isolation:** every scenario ran inside a fake `$HOME`, so the real
  `~/.linkedin-networking-cli/` (DB, `config.json`, `session.json`) was never
  touched. Fake `LINKEDIN_EMAIL`/`LINKEDIN_PASSWORD` were injected except in
  the unconfigured-state scenario.
- **Deliberately out of scope:** a live browser automation run (real LinkedIn
  login + invitations). Nothing in the current working tree touches the
  automation layer, the unit suite covers it heavily, and a live run spends
  real invitation quota. The run/check flows were exercised up to their
  confirmation gates. The `AutomationRunScreen` base has no shipped subclass
  (issue #44 removed the last one), so there is no screen to drive there.

## What ran

### 1. Full test suite

`uv run pytest`: **1052 passed, 0 failed** in 260s (85% line coverage).
One `ResourceWarning: unclosed database (sqlite3.Connection)` surfaces at
gc during teardown (finding 4).

### 2. TUI end-to-end drive — 41/41 checks passed

A scripted Pilot tour over four app boots (seeded DB at 140×42, seeded DB at
96×30, empty DB, unconfigured credentials), with an SVG screenshot exported at
every state (23 total). Coverage:

| View / flow | Verified |
|---|---|
| Home | Summary line (counts, single-active-campaign limit), nav focus, esc-esc quit guard (arm → disarm on other key → quit), narrow-terminal wordmark fallback, unconfigured onboarding line, empty-DB summary |
| Dashboard | Stat cards, recent-campaigns table, status line "Updated." |
| Campaigns | Row data, empty state hint, row → detail, New Campaign button |
| Campaign detail | Load, Run confirm arm/cancel (esc), Check confirm arm/cancel, Toggle active (both directions, persisted), Export CSV (file written under app dir), inactive-campaign run refusal |
| Campaign edit | Prefill, rename, save persisted, detail refresh on resume |
| Delete | Two-step confirm bar (arm prompt → Enter), row + contacts gone, pop back to list |
| Create campaign | Empty-name validation, manual create, terminal locked-form state |
| AI assist | Panel expand/collapse, model select from `LLM_MODEL`, **live extraction against Ollama gemma3:1b** — 8/8 fields filled in ~3s, flagged fields carry "You said: …" review hints, campaign then created |
| Settings | Load, Rate Limiting edit → Save → `config.json` override written and correct, blank-value rejection |

### 3. Non-TUI surfaces

`linkedin-run` (all safe paths, real process invocations):

| Case | Result |
|---|---|
| `--help` | exit 0 |
| no args | argparse error, exit 2 |
| `--max 0` | "must be a positive integer", exit 2 |
| nonexistent campaign | clean error, exit 1 |
| ambiguous name (two campaigns named alike) | names the candidate ids, suggests `--campaign <id>`, exit 1 |
| inactive campaign | refuses with reactivation hint, exit 1 |
| missing credentials | prints the saved-session warning, then continues to resolution |

Database layer (via the drive): campaign create/update/delete, contact
seeding across statuses, daily-count reads, CSV export. Settings persistence:
`config.json` precedence path verified end-to-end from the Settings screen.

## Findings

Everything above passed; four problems were found and filed:

1. **Stats disagree across views** — the Dashboard derives sent/accepted/rate
   from live contact rows while the Campaigns list and the detail screen's
   Performance section render the stored `Campaign.total_*` counters. With
   the same DB the dashboard showed *3 sent / 1 accepted / 33.3%* while
   Campaigns/Detail showed *12 / 5 / 41.7%*. `dashboard._recent_rows` already
   documents that the denormalized aggregates can go stale — but only the
   dashboard defends against it, so the app contradicts itself. → issue #66
2. **Settings hint bar still advertises `tab`** (`settings_view.py:83`), the
   only surviving `tab` hint after the arrows-only navigation cutover
   (commit `2513438`). → issue #67
3. **AI assist: extracted keywords arrive noisy from small local models** —
   gemma3:1b returned duplicated terms and location words
   ("… Berlin, Germany, … Berlin, Germany") despite the prompt forbidding
   locations in keywords; `llm_assist/postprocess.py` normalizes the message
   placeholder and daily limit but never dedupes/trims keywords. Mitigated by
   the flagged-review UX, but the prefill quality is poor on the recommended
   local-model path. → issue #68
4. **`ResourceWarning: unclosed database`** at pytest teardown — one SQLite
   connection escapes cleanup somewhere in the suite/fixtures. → issue #69

Notes that are *not* defects:

- A "glued columns" artifact in the Dashboard screenshot (Name/Status) turned
  out to be bold-font metrics in the SVG→PNG conversion; the exported SVG has
  the correct cell padding.
- The seeded stored-counter vs contact-derived discrepancy is what makes
  finding 1 visible; real runs keep them in sync via `update_campaign_stats`,
  but any drift (crashes, manual edits, imports) becomes user-visible.

## Artifacts

- Drive script, findings JSON, and the 23 SVG/PNG screenshots:
  session scratchpad `e2e/` (temporary; regenerate with the script if needed).
- Existing open issues #57–65 (from an earlier comprehensive review pass) were
  read in full and checked for overlap before filing; #66–69 are all new.
  Closest neighbor: #67 (Settings `tab` hint) shares a root cause with #64
  (stale keybinding docs) — cross-linked in comments, distinct artifacts.
