We are waiting for task `60ebb3da-03e1-48a3-94a3-892d2f6638af/task-215` to complete. Do not take action. We will automatically notify you when it's done.SHIP — All round 1–4 blockers are verified fixed; action gates, push-destination rules, process locks, and pane error states hold without meeting any blocking criteria.

### BLOCKING FINDINGS
NONE

### NON-BLOCKING FINDINGS
1. [sessions.py:815-830](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/sessions.py#L815-L830): `Pane._report_parked()` records parked items as `interrupted` in the turn ledger but omits emitting `peer_result` events when `getattr(t, "peer", False)` is true, causing sender panes blocked in `seat_wait` to wait out the full 120s timeout instead of failing immediately.
2. [sessions.py:1495-1505](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/sessions.py#L1495-L1505): In `Pane._drain()`, if `pause()` or `/clear` executes concurrently while `_refuse_queued_into_blocked_branch()` evaluates an unblocked worktree, `_drain` records a late `dispatched` edge in the turn ledger for a turn already marked `interrupted`.
3. [worktrees_cli.py:182](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/worktrees_cli.py#L182): `commit_still_unsettled()` remediation advice suggests `git reset -q`, which can discard unstaged working tree modifications if unstaged changes were present when the commit op was interrupted.
4. [worktrees.py:1685](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/worktrees.py#L1685): `_set_aside_stale_lock()` renames dead agent lockfiles to `.corral-stale-<ts>` instead of removing them, leaving abandoned lock artifacts on disk across repeated crashes.
5. [sessions.py:1930](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/sessions.py#L1930): In `auth_sweep()`, an agent whose resume is skipped due to a concurrent action lock sets `dead_login`, postponing automatic recovery until the next login event (manual resume remains unblocked).
6. [worktrees.py:1180](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/worktrees.py#L1180): In `fit_review()`, if non-patch metadata alone exceeds `limit` (2 MiB), it drops file entries but cannot shrink the response payload further.
7. [static/app.js:1410](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/static/app.js#L1410): UI `forgetPane` on an entry whose creation crashed in `phase === "intent"` prompts the user to open review, which toasts and refuses because the phase is not `active`.
8. [test_resilience.py:330](file:///home/cvande/.cache/corral-bugbash-2026-10-03-r5/gemini38/corral-light/test_resilience.py#L330): Crash simulation tests calling `os.fork()` on multi-threaded runners trigger Python 3.14 `DeprecationWarning` and produce unclosed file `ResourceWarning` notices for `hub.lock`.

### ONE SENTENCE
Craig is most likely to be bitten when an agent sends a peer message to a pane undergoing a failed review action or discard, and the sender blocks through the full 120-second `seat_wait` timeout because `_report_parked` parks the peer message without emitting a `peer_result(delivered=False)` event.
