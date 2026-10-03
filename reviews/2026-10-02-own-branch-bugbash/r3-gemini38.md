SHIP — All round 1 and round 2 defects have been verified as fixed; concurrency locks, gate enforcements, process isolation, and crash reconciliation hold under test and review against the ship bar.

### BLOCKING FINDINGS
NONE

### NON-BLOCKING FINDINGS
1. [worktrees.py:1506](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/worktrees.py#L1506): In `open_pr()`, `if not login or login == owner:` falls back to accepting an open PR URL if GitHub returns a null `headRepositoryOwner` (e.g. if a fork repo was deleted).
2. [worktrees.py:1180](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/worktrees.py#L1180): In `fit_review()`, if untracked non-patch metadata alone exceeds `limit` (2 MiB), it drops all file entries but cannot shrink the response further.
3. [worktrees_cli.py:182](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/worktrees_cli.py#L182): In `commit_still_unsettled()`, the remediation hint advises `git reset -q`, but if worktree files were modified or detached, `git read-tree` may be needed to preserve unstaged changes cleanly.
4. [sessions.py:1930](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/sessions.py#L1930): In `auth_sweep()`, an agent whose resume is skipped due to a concurrent action lock sets `dead_login`, deferring automatic recovery until the next login event (manual resume remains unblocked).
5. [worktrees.py:145](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/worktrees.py#L145): `_kill_group()` signals only the leader's process group; any grandchild process that executed `setsid()` escapes group termination, though `processes_in()` via `/proc` still catches it.
6. [worktrees.py:1675](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/worktrees.py#L1675): `_set_aside_stale_lock()` renames locks to `.corral-stale-<ts>` rather than deleting them, which may accumulate abandoned lock files over time if an agent crashes repeatedly.
7. [sessions.py:2385](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/sessions.py#L2385): `_worktree_blocked_reason()` relies on ops list checks and startup reconciliation to block intermediate `intent` states rather than explicitly naming `"intent"` in the initial phase check tuple.
8. [test_resilience.py:330](file:///home/USER/.cache/corral-bugbash-2026-10-03-r3/gemini38/corral-light/test_resilience.py#L330): Simulated crash unit tests emit `ResourceWarning` notices for unclosed file handles on `hub.lock` and events logs across forks.

### ONE SENTENCE
The operator is most likely to encounter users confused by `discard_preflight` refusing a Discard when rapid file edits trigger the 0.5s settle-check mismatch, requiring a second Discard click after the writer stops.
