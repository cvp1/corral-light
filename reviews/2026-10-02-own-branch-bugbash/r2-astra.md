I’ll inspect the fixes and round-one findings, run the prescribed tests in the isolated seat environment, and build targeted repros for any gaps I find.The gate now covers review actions, ordinary sends, and peer dispatches. I’m checking paths that run before it, plus the discard preflight and restart checks; those are the most likely places for a bypass or regression.Two gate gaps are visible in the code: pane dispatch checks `unknown` but not `intent`, and `/clear` runs before either check. Discard also still checks whether files changed after stopping the agent. I’m turning those into reproducible tests and checking whether stale-lock recovery can move another process’s lock.The targeted repros confirm that `intent` permits a real agent write, `/clear` starts a replacement agent despite `unknown`, and `/clear` during Discard leaves an agent alive in trash. The prescribed suite ran 233 tests; 15 errors and one dependent isolation failure came from the sandbox denying sockets. I’m keeping those environment failures separate from the code findings.I reproduced staged-work loss on upgrade: an interrupted commit journal from before this fix has no `index_id`, so restart reconciliation overwrites newly staged bytes and marks the operation done. I also reproduced Discard renaming a live external Git process’s `index.lock`. All 22 tests in the two BugBash classes and the new Manager guard class pass despite these gaps.Verification is complete: ten targeted tests fail on the fixes, and a separate repro shows a forked child bypassing the new state claim through inherited `_CLAIMED` state. The atomic phase updates, interrupted-purge cleanup, and failing-hook handling pass their targeted tests. I’ll rank the report around staged-work loss, live-lock removal, and the remaining gate and Discard races.DO NOT SHIP — restart can still overwrite staged work from an older journal, and Discard can remove a live Git process’s lock.

**PER-FIX VERDICT**

| Fix | Verdict |
|---|---|
| 1: Review-action gate | **PARTLY** — hub actions refuse unsettled ops; CLI restore bypasses the gate. |
| 1: Dispatch/resume gate | **NOT CLOSED** — `intent` permits dispatch; `/clear` bypasses both blocking and holds. |
| 2: Index identity, live and restart | **PARTLY** — new journals protect staged bytes; older journals silently disable protection. |
| 2: Failures after update-ref become unknown | **PARTLY** — swap exceptions are covered; postcondition exceptions leave `intent`. |
| 2: Independent reconciliation | **PARTLY** — initial repository-list failure still aborts everything. |
| 2: Unreachable remote during reconciliation | **CLOSED** — failed checks become `unknown`. |
| 3: Discard preflight | **PARTLY** — foreign-process refusal preserves the agent; stale-review refusal still kills it. |
| 3: Serialize resume/send against holds | **NOT CLOSED** — concurrent resume and `/clear` can leave an agent alive in trash. |
| 3: Stale-lock recovery | **NOT CLOSED** — the stopped agent’s identity does not establish lock ownership. |
| 3: Atomic op completion and phase | **CLOSED** — `finish_op` removes the two-write window. |
| 3: Interrupted purge deletes branch | **CLOSED** — restart performs conditional branch deletion. |
| 3: Failing post-checkout hook | **CLOSED** for the reported case — active worktree retained with warning. |
| 3: Forget confirmation | **CLOSED** by inspection and Manager test; HTTP/browser execution remains unverified. |
| 4: Second-Manager guard | **PARTLY** — independent-process tests pass; inherited claim cache bypasses protection after fork. |

**FINDINGS**

Repros and exact isolated commands: [evidence README](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/scratch/README.md). Below, `R2.test_*` refers to [repro_r2.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/scratch/repro_r2.py); all ten tests fail on the reviewed code.

1. **High · PROVEN — Older commit journals still lose staged work.**  
   [worktrees.py:1777](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1777). An interrupted pre-fix commit has no `index_id`. After staging unique content during downtime, reconciliation passes `expect_id=None`, replaces the index, and marks the op `done`. The repro confirms the staged blob becomes unreachable from refs. **Minimal fix:** require a valid journalled identity before rebuilding; otherwise mark `unknown` unless the index already matches the committed tree. Repro: `R2.test_legacy_commit_journal_loses_staging`.

2. **High · PROVEN — Discard removes another live Git process’s lock.**  
   [worktrees.py:1553](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1553). Run `git --git-dir=<worktree-admin> update-index --index-info` from outside the worktree, keeping stdin open. It owns `index.lock` but escapes the worktree process scan. Normal hub Discard stops its agent, falsely attributes the external lock to that agent, renames it, and proceeds while Git remains alive. **Minimal fix:** refuse automatic removal without evidence tying that particular lock to the stopped writer; mtime and an unrelated dead PID are insufficient. Repro: `R2.test_foreign_live_index_lock`.

3. **High · PROVEN — `/clear` bypasses blocking and resurrects an agent during Discard.**  
   [sessions.py:1346](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/sessions.py:1346). `/clear` reaches `clear_context()` before either new guard. It replaces an agent despite `unknown`; injected after Discard’s final process scan, it leaves a replacement agent alive in the moved folder. The repro confirms that process can write into trash. **Minimal fix:** gate and serialize `clear_context()` before changing anything. Repros: `R2.test_clear_unknown`, `R2.test_clear_during_discard`.

4. **High · PROVEN — Ordinary dispatch still accepts `intent`.**  
   [sessions.py:2339](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/sessions.py:2339). `_worktree_blocked_reason()` checks only `unknown`, although send, peer dispatch, and resume depend on it. A real fake-ACP agent writes a file with a commit op still `intent`. This also exposes the asynchronous startup-reconciliation interval. **Minimal fix:** include `intent` in the shared blocking predicate. Repro: `R2.test_intent_send`.

5. **High · PROVEN — Resume’s hold check is not serialization.**  
   [sessions.py:810](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/sessions.py:810). Pause a pane, start resume, then suspend it after the hold check. Discard acquires the hold and scans; allowing resume to continue now starts an agent that survives the move. Deterministic thread barriers reproduce this with a real ACP process. **Minimal fix:** serialize agent attachment against `_action_lock`, rechecking registry state within that protection. Repro: `R2.test_resume_races_discard`.

6. **Medium · PROVEN — Stale-review refusal still kills the agent first.**  
   [sessions.py:2516](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/sessions.py:2516), [worktrees.py:1638](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1638). Change a tracked file after taking the snapshot, then Discard. The operation returns `changed`, but the previously healthy agent is already stopped. **Minimal fix:** check the reviewed tree during preflight before stopping, retaining the later check for subsequent changes. Repro: `R2.test_stale_review_discard`.

7. **Medium · PROVEN — Post-commit verification exceptions leave `intent`.**  
   [worktrees.py:1301](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1301). Inject a timeout into the final branch verification after the ref and index have moved. That code lies outside the new exception handler, so the op remains `intent`; combined with finding 4, agent dispatch remains possible. **Minimal fix:** cover all post-update-ref checks and completion with the unknown-on-failure handling. Repro: `R2.test_commit_postcheck_failure`.

8. **Medium · PROVEN — Repository discovery still aborts reconciliation globally.**  
   [worktrees.py:1860](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1860). A timeout in `_registered(common)` escapes before the per-entry handler. Later repositories are never checked and interrupted ops remain `intent`. **Minimal fix:** isolate repository discovery failures, flag affected entries, and continue other groups. Repro: `R2.test_reconcile_repo_failure`.

9. **Medium · PROVEN — Restore mutates an unsettled worktree.**  
   [worktrees.py:1672](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/worktrees.py:1672). A trashed entry with an `unknown` purge op can be restored through the library function used by the CLI. Files move and phase becomes `active` without resolving the earlier operation. **Minimal fix:** call `refuse_open_ops` under the repository lock before journalling restore. Repro: `R2.test_restore_unknown`.

10. **Medium · PROVEN — Fork inherits authorization to reap the parent’s state.**  
    [sessions.py:1791](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/corral-light/sessions.py:1791). After a process claims state, its forked child inherits `_CLAIMED`. `claim_state()` returns immediately, bypassing both flock acquisition and a `hub.pid` naming the live parent. The claim bypass is reproduced without signalling agents. **Minimal fix:** associate cached claims with the owning PID and reset inherited descriptors/cache/guard after fork. Repro: [repro_guard.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r2/astra/scratch/repro_guard.py).

**TEST GAPS**

- All **22 existing BugBash and guard tests pass** despite these repros. Hub gate tests omit `intent` and `/clear`; library coverage omits restore.
- `test_BB_4` supplies `boom(idx, content)`, but `_replace_index` receives three arguments. It passes by catching an unintended `TypeError`, without executing the intended lock-failure stub.
- Discard tests miss genuinely overlapping resume, stale-tree refusal, and a live external Git lock owner. The existing stale-lock tests manufacture ownership records.
- Restart tests cover new journals and failures inside op resolution, omitting old journals and repository discovery failure. Guard tests omit inherited process state.
- The prescribed **233-test** run had **15 socket-permission errors and one dependent isolation failure**; HTTP/browser behavior was therefore not validated in this sandbox.

The remaining bug most likely to bite Craig in the first week is Discard rejecting an outdated review only after killing the agent.
