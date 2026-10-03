# Own-branch bug bash, round 2: synthesis

Reviewed: 6500638 (round 1 fixes) plus the second-Manager guard, each seat in a
private clone under ~/.cache/corral-bugbash-2026-10-03-r2/<seat>/ with every
default state path pinned into the seat sandbox. Charge: r2-charge.md.

Seats: Codex gpt-6-astra (r2-astra.md), Gemini 3.8 flash high (r2-gemini38.md).
Grok 4.7 did not finish: its pane raises a permission card per command (the
running hub predates b0fda02), and approving cards from an agent session was
blocked, so it is parked on a card for the operator. Neither finished seat touched a
path outside its sandbox (tool-call audit).

Verdicts: Codex DO NOT SHIP. Gemini FIX FIRST.

## Agreed by both seats (PROVEN by both)
1. The dispatch gate ignores `intent`: `_worktree_blocked_reason` checks only
   `unknown`, so send, peer dispatch and resume run during an unsettled op.
   Fix: include `intent` in the shared predicate.
2. Discard on a stale review still stops the agent and parks the queue before
   `discard` refuses with `changed`. Fix: compare the reviewed tree in the
   preflight, keep the later check.
3. `restore()` never calls `refuse_open_ops`. Fix: call it under the repo lock
   before journalling restore.

## One seat, PROVEN
4. High (Codex): a pre-fix commit journal has no `index_id`, so restart
   rebuilds the index with `expect_id=None` and loses staged work. Fix: no
   journalled identity means `unknown` unless the index already matches.
5. High (Codex): Discard renames a LIVE foreign git process's index.lock
   (git --git-dir from outside the worktree escapes the process scan).
   Gemini found the opposite edge: with the agent already dead, `stale` is
   None and Discard waits the full LOCK_WAIT_S then fails. Resolve both
   together: only set a lock aside with evidence tying it to the stopped writer.
6. High (Codex): `/clear` reaches `clear_context()` before either guard; it
   replaces an agent despite `unknown`, and during Discard leaves an agent
   alive in trash. Fix: gate and serialize `clear_context()`.
7. High (Codex): resume's hold check is not serialization; a resume racing
   Discard starts an agent that survives the move. Fix: attach under
   `_action_lock` and recheck the registry there.
8. Medium (Codex): exceptions in post-update-ref verification leave the op
   `intent` (outside the unknown-on-failure handler).
9. Medium (Codex): a timeout in `_registered(common)` aborts reconcile for
   every later repository.
10. Medium (Gemini): `open_pr` skips `refuse_open_ops` when a PR already
    exists and still updates the registry.
11. Medium (Codex): the second-Manager guard's claim cache was inherited by a
    forked child. FIXED in the working tree (claims are per pid and cleared
    after fork; test_a_forked_child_does_not_inherit_the_claim).

## SUSPECTED
12. Low (Gemini): Forget on a pane whose worktree is stuck in phase `intent`
    offers review, but the review dialog only opens for `active`.

## Tests that lie
- `test_BB_4` stubs `_replace_index` with two parameters; the real call passes
  three, so it passes on a TypeError, not the lock failure it names (Codex).
- The 22 BugBash and guard tests all pass against every finding above.

## Fix order
Gate first (1, 6, 7, 10, 3), then Discard ordering and the lock rule (2, 5),
then restart safety (4, 8, 9), then 12 and the lying test.

## Status (2026-10-03)
All twelve findings and the lying test are fixed in the commit after this
file, with a test each (BugBash2Library, BugBash2Publish, BugBash2Hub in
test_worktrees.py). Discard's stale-review check refuses before the stop only
when the files have stopped changing; files still changing mean the agent is
writing, so it is stopped first, as T-RMV-11 requires. Grok's seat was not
rerun.
