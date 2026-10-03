# Bug bash synthesis: own branches (2026-10-02)

Reviewed: HEAD b6f7dfc, feature range `5767fa9^..HEAD` (WS1.1 to macOS M4).
Each seat worked in a private clone at `~/.cache/corral-bugbash-2026-10-02/corral-light`
with isolated HOME and TMPDIR. Charge: `charge.md`.

| Seat | Model (hub-echoed) | Wall | Verdict | Repros |
|---|---|---|---|---|
| Astra | gpt-6-astra, effort high | 570 s | DO NOT SHIP | `scratch/bugbash/repro.py`, `repro_lifecycle.py` in the clone |
| Grok | grok-4.7, effort high | 4,342 s, of which 2,567 s waiting on a permission card | FIX FIRST | `~/.cache/corral-bugbash-2026-10-02/scratch/bb/repro_findings.py` |
| Gemini | gemini-3.8-flash-high | 517 s | FIX FIRST | none saved; inline repros reference attributes that do not exist (`p.queue`, `mgr.create('fake', ..., worktree=False)` on a non-repo), so treat its PROVEN labels as code-reading only |

Author spot-checks against HEAD are marked **checked**.

## Converged (two or more seats)

1. **Review actions ignore an `unknown` or `intent` op** (Astra, Grok). `_worktree_action` never consults `_worktree_blocked_reason`; only resume does. Commit, publish and discard run on a pane whose last op has an unknown outcome. Violates ship gate §5.4.7. **checked** (sessions.py `_worktree_action`).
2. **Commit index reconciliation can lose or misplace work** (Astra, Grok). Astra: restart reconcile replaces the index without checking it against the journalled identity, so work staged while the hub was down becomes unreachable (PROVEN by SIGKILL). Grok: if `_replace_index` fails after `update-ref`, the branch holds the reviewed commit, the index holds the old blob, the op stays `intent`, the pane stays usable, and a plain `git commit` puts the old bytes on the tip (PROVEN). Grok adds that one failing entry aborts the whole reconcile loop.
3. **Discard tears down the agent before it knows the discard will succeed** (Grok, Gemini). The client is closed and the pane set `detached` before `_wt.discard()`. A refusal leaves the worktree in place, the turn killed, and the queue parked. **checked** (sessions.py `worktree_discard`).
4. **Discard races a normal send** (Astra, PROVEN with a real fake-ACP process). A send after the process scan calls `resume()` before checking the hold, so a live agent survives the move and writes into `.trash`, outside the recovery ref. Existing T-RMV-14 appends to the queue directly and misses this path.

## Single-seat findings worth fixing

- **Discard crash window** (Astra, PROVEN). A crash between marking the move op `done` and saving `phase=trashed` and `trash_path` leaves the files in trash but unrestorable through the CLI. Restore has the same two-write pattern.
- **Chained `insteadOf` rewrites redirect a confirmed push** (Astra, PROVEN up to `ls-remote`; no push performed). The confirmed URL is rewritten again when passed to git.
- **Own-branch subdir can resolve outside the worktree** (Astra, PROVEN). A committed symlink replaced by a real directory in the main checkout passes probe; the agent cwd lands outside the worktree.
- **Push resolve treats a failed `ls-remote` as "did not happen"** (Grok, PROVEN). Spec says unknown. A `GitTimeout` there stops reconcile.
- **Failed `worktree add` from a failing post-checkout hook** (Grok, PROVEN). Worktree and branch exist, entry is `missing`, pane is popped; nothing lists, discards or purges it.
- **`resolve --op` clears a commit op without checking git** (Grok, code). Can unblock a pane whose tip holds the naive revert.
- **Dismissing a dead own-branch pane** (Gemini). `forget` saves the pane closed and drops it; the worktree stays `active` with no owner in the UI, and nothing offers Discard as spec F7 requires. **checked** (corral_core/sessions.py `forget` has no worktree handling).
- **Stale `index.lock` recovery is never called** (Gemini). `lock_is_ours_and_stale` is defined and tested but has no caller; a stale lock blocks Commit and Discard for 60 s every time. **checked** (only the def matches).
- **Interrupted purge leaves the branch** (Gemini). Resolve marks the entry `purged` when the folder is gone but never deletes `corral/<slug>`. Leftover, not loss. **checked** (worktrees.py `resolve_op` purge branch).
- Medium and low: git timeout does not cover inherited pipes (Astra, PROVEN); review response can exceed the 2 MiB cap with Unicode (Astra, PROVEN); `staged_differs` banner never rendered (Grok); a refused-discard path through `/proc` skips a pid whose cwd cannot be read (Grok); `open_pr` does not bind `pr.repo` to the push URL (Grok); no-op commit shows "committed " (Gemini); restore fails if the repo dir under the root was removed (Gemini); macOS `lsof` warning on stderr fails the scan (Gemini); `same_fs_as_root` recorded but never refused (Grok, suspected); `self.panes.pop` without `_lock` in create failure paths (Gemini).

## Found during the run, outside the charge

- **The test suites leak temp directories into `/tmp`.** After this run `/tmp` (tmpfs) had 0 free inodes of 1,048,576: about 9,700 `corral-wt-test*`, 5,300 `fake-acp*`, 14 `corral-iso-*` (about 15,000 inodes each), plus several hundred each of `corral-light-{test,roles,cli,restore,fts,ledger}*` and `seat-state*`, dating from 2026-10-01. Shell tools that write to `/tmp` now fail with ENOSPC. Needs a cleanup and `tearDown`/`addCleanup` fixes.
- **Grok under `auto` stalls on its own permission cards.** The seat waited 43 minutes on a card. Uncommitted work in the real repo (launcher passes `--always-approve` under `auto`) appeared during the run from another session; it was not part of this review.

## Recommended order

1. Gate every review action and every dispatch on the registry op state (converged 1), and make the commit index swap journal its expected identity and fail to `unknown` (converged 2).
2. Reorder Discard: preflight and tree check first, then stop the agent; serialize resume and send against review actions (converged 3 and 4); write op completion and phase in one registry mutation.
3. Forget on an own-branch pane offers Discard; wire `lock_is_ours_and_stale`; purge resolve deletes the branch.
4. Add the test gaps each seat named; the strongest are real `Pane.send()` during discard, SIGKILL between op-done and phase-save, a failing post-checkout hook, and actions on an `unknown` op.
