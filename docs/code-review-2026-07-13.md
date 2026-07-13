# Comprehensive code review — 2026-07-13

**Scope:** the 13 commits on `claude/comprehensive-code-review-1yb8nk` vs `origin/master`
(~13.6k insertions across 104 files): the issue #47 single-UI cutover, the new
`src/llm_assist/` package and AI-assisted campaign creation, the `linkedin-run`
entry point, the automation-hardening pass (profile lock, session-compromise
guard, cooperative cancellation), and the TUI interaction/design pass.

**Method:** eight independent review angles (line-by-line diff scan,
removed-behavior audit, cross-file tracing, reuse, simplification, efficiency,
altitude, CLAUDE.md-conventions) produced 37 candidate findings; after
de-duplication, 17 were individually re-verified against the code. None was
refuted (16 confirmed, 1 plausible). Every finding below was filed as a GitHub
issue for later work. Line numbers refer to this branch at the time of review.

## Headline

The two hardening features this branch introduces — the cross-process
`browser_profile.lock` and the session-compromise guard — each have real holes
(#57, #58), and the connection checker has three independent ways to report a
failed or incomplete check as a green "success" (#59).

## Findings → issues

| # | Severity | Area | Finding | Issue |
|---|----------|------|---------|-------|
| 1 | High | automation | `acquire_profile_lock` reclaims own-PID locks: a second automation in the same TUI process (run worker + location search) bypasses the busy guard and `force_close_chrome` kills the live sibling run's Chrome (`linkedin.py:263`) | [#57](https://github.com/bman95/linkedin-networking-cli/issues/57) |
| 2 | High | automation | Lock acquisition is a non-atomic read-check-write (no `O_EXCL`): concurrent cron + TUI starts can both claim the profile (`linkedin.py:260`) | [#57](https://github.com/bman95/linkedin-networking-cli/issues/57) |
| 3 | High | automation | `_refresh_context` clears `_locked_user_data_dir` before relaunch; a relaunch failure before re-acquire leaks a live-PID lock that blocks every `linkedin-run` until the TUI exits (`linkedin.py:431`) | [#57](https://github.com/bman95/linkedin-networking-cli/issues/57) |
| 4 | High | automation | Session-compromise marking is per-catch-site (7 sites); the checker's new-tab `goto` path swallows challenges unmarked and `close_browser` persists compromised cookies over a good `session.json` (`linkedin.py:361`, `checker.py:356`) | [#58](https://github.com/bman95/linkedin-networking-cli/issues/58) |
| 5 | High | checker | Trailing `except Exception` in `smart_connection_checker` turns crashes into zero-stat dicts the TUI renders as green success (`checker.py:164`) | [#59](https://github.com/bman95/linkedin-networking-cli/issues/59) |
| 6 | High | checker | New single-empty-round terminator ends the walk on one stalled lazy-load, unflagged — accepted invites silently stay `sent` (`checker.py:331`) | [#59](https://github.com/bman95/linkedin-networking-cli/issues/59) |
| 7 | Medium | checker/TUI | `map_check_stats` drops the `truncated` flag (no consumer repo-wide); a 40-round-capped walk renders as "Connection check complete" (`campaign_detail.py:122`) | [#59](https://github.com/bman95/linkedin-networking-cli/issues/59) |
| 8 | Medium | runner | `linkedin-run` catches only `ValueError` around campaign resolution and has no top-level guard: DB errors / Ctrl-C dump raw tracebacks (regression vs the retired CLI's `main()`) (`runner.py:177`) | [#60](https://github.com/bman95/linkedin-networking-cli/issues/60) |
| 9 | Medium | TUI | Location query input left permanently disabled when a search finishes after the mode was switched (`campaign_form.py:372`) | [#61](https://github.com/bman95/linkedin-networking-cli/issues/61) |
| 10 | Medium | llm_assist | Model availability check compares untagged names against Ollama's `name:tag` list → offers multi-GB re-pull of installed models (`campaign_ai_assist.py:222`) | [#62](https://github.com/bman95/linkedin-networking-cli/issues/62) |
| 11 | Medium (plausible) | llm_assist | Hosted-mode consent gate fails open when `db_manager is None` (degraded mode is deliberate and the screen stays reachable) (`campaign_ai_assist.py:316`) | [#63](https://github.com/bman95/linkedin-networking-cli/issues/63) |
| 12 | Low | docs | README/tui-migration still document the removed `q` / `1`–`4` keys; `_start_browser_unlocked` docstring still claims unconditional `session.json` writes (`README.md:65`, `docs/tui-migration.md:283`, `linkedin.py:525`) | [#64](https://github.com/bman95/linkedin-networking-cli/issues/64) |
| 13 | Low | cleanup | Dedup/dead-code/efficiency batch: duplicated result→status mapping (already drifted), copy-pasted run-policy stack in both send loops, per-card weekly-count queries, LLM defaults ×3, `cli`↔`automation` inverted dependency, `_set_status` ×4, dead `monitor_pending_connections` / `extract_detailed_profile`, and smaller items | [#65](https://github.com/bman95/linkedin-networking-cli/issues/65) |

## Notes

- Findings 1–3 interact: the own-PID exemption exists to serve
  `_refresh_context`'s relaunch, so fixing the leak (holding or handing off the
  lock across the relaunch) is what makes removing the exemption safe. Fix #57
  as one unit.
- Findings 5–7 share one fix surface (`smart_connection_checker`'s result
  contract plus `map_check_stats`/`render_check_result`) and are grouped in #59.
- The review deliberately made no code changes; this document and the issues
  are the only artifacts.
