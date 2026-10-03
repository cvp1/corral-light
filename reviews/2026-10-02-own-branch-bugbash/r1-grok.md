# Grok (grok-4.7, high) — own-branch bug bash

pane cace2e4c8a79 · model grok-4.7 · effort high · complete True · wall 4342s · needs-you 2567s

I'll bug-bash the own-branches feature against the spec, starting with the plan and the git, hub, and review paths that can lose or misplace work.The commit and review paths are the ones that can put the wrong tree on a branch. I'll reproduce those against real git next.The stale-index repro died on a lock the script itself left behind. I'll finish that proof and the remaining cases, then write the ranked report.Section 2 died because the repro left `index.lock` in place. I'll finish that case, then run the unknown-op, oversized-file, and failed `ls-remote` checks.**FIX FIRST.** A hub Commit can move the branch and leave the worktree index on the old blob, with the pane still active, so the next ordinary `git commit` records that old blob as the new tip.

## FINDINGS

1. **High. PROVEN.** `worktrees.py:1201` (`set_op` stage `ref_moved`, then `_reconcile_index`), `worktrees.py:1116`, `worktrees.py:1446`, `worktrees.py:1606`, `sessions.py:2175`, `sessions.py:2249`, `static/app.js:1285`, `static/app.js:1409`.
   Trigger: `_replace_index` fails after `update-ref` (an `index.lock` appears in that window, or the lock write fails). The repro raises from `_replace_index` after creating the lock.
   The branch tip is the reviewed commit (`HEAD:a.txt` = `reviewed line`), the real index blob is still `base`, status is `MM a.txt`, and the op stays `intent` / `ref_moved`. `_worktree_blocked_reason` is `None` because it ignores `intent`. `openReview` only refuses a phase other than `active`. After the lock is removed, a fresh snapshot has `tree == head_tree` (the dialog's Commit control then says nothing to commit) while status is still `MM`, and the snapshot does not rewrite the index. `git commit -q -m naive` with no add then makes `HEAD:a.txt` = `base`; `HEAD~1:a.txt` is still `reviewed line`. Reconcile while the lock exists raises `Refused: busy` after 5 s and leaves the op `intent`, and that exception aborts the rest of reconcile (`worktrees.py:1696`, swallowed at `sessions.py:2199`). Reconcile after the naive commit sets the op `unknown` and leaves the tip on `base`.
   Fix: on any failure after `ref_moved`, set the op `unknown` before releasing the repo lock, and refuse review actions while a commit op is `intent` or `unknown`. On restart, finish the swap only when `write-tree` of the real index equals the journalled tree; do not report the commit finished before that.
   Repro: `PYTHONUNBUFFERED=1` run of `/home/cvande/.cache/corral-bugbash-2026-10-02/scratch/bb/repro_findings.py` section 2 (isolated `CORRAL_LIGHT_STATE` / `CORRAL_LIGHT_WORKTREES` under `scratch/bb/run`). Tip after the naive commit: `9d`/`df13b079799b` was the reviewed commit; the new tip's file is `base`.

2. **High. PROVEN.** `sessions.py:2249` versus `sessions.py:2175` and `sessions.py:792`. `static/app.js:1409`. `hub.py:310`.
   Trigger: any op already `unknown` (or, with finding 1, still `intent`) on a live pane.
   Resume refuses an `unknown` op. Snapshot, commit, publish, and discard all go through `_worktree_action`, which never reads that reason, and the route adds no check. Section 3 printed the blocked reason and `commit_tree` still created `9aa5ac0dafd0`. Ship gate §5.4.7 ("nothing dispatches into a trashed or `unknown` pane") is not enforced on this path.
   Fix: refuse at the top of `_worktree_action` when `_worktree_blocked_reason` is set or any op is `intent`. Have `openReview` toast on `worktree.blocked` even when the phase is `active`.

3. **Medium. PROVEN.** `worktrees.py:1616`.
   Trigger: restart settles a `push` op and `ls-remote` fails.
   `check=False` plus empty stdout takes the same branch as "remote does not have the oid": state `done`, stage `not_done`, note "so it did not happen". A `GitTimeout` is not caught, so the op stays `intent` and reconcile stops. The spec says an inability to check is `unknown` (`docs/worktree-review-plan.md:228`).
   Fix: non-zero `ls-remote`, empty output, or a timeout becomes `unknown`. Catch per entry inside `reconcile` so one repo cannot skip the others.
   Repro: same script, section 5.

4. **Medium. PROVEN.** `worktrees.py:794`, `worktrees.py:1690`, `sessions.py:1948`, `worktrees.py:1540`.
   Trigger: `git worktree add` returns non-zero because `post-checkout` exits 1 (the worktree and `corral/<slug>` branch already exist).
   `create` records phase `missing` and re-raises. `sessions.create` pops the pane, so there is no owner and no Discard. Reconcile skips `missing` and does not list the path as an orphan. Purge refuses because the phase is not `trashed`. `git worktree list` still lists the path. The main checkout's index bytes and `HEAD` bytes were unchanged.
   The unit test `test_a_failed_add_leaves_the_entry_marked_not_silently_gone` mocks `git` to raise before `worktree add`, so it never sees this leftover.
   Fix: if add fails and git still lists the path, `worktree remove` it and delete `corral/<slug>` only when that ref still points at the start commit. Do not persist `missing` for a path that is still registered.
   Repro: same script, section 1.

5. **Medium. PROVEN (code).** `worktrees_cli.py:186`.
   Trigger: `corral-light worktrees resolve <id> --op <op>` after finding 1 has flipped the op to `unknown`.
   The command sets state `done`, stage `resolved_by_user`, and runs no git. The pane is unblocked while the tip is still the naive revert. `test_T_CLI_15_resolve_an_op_clears_the_block` asserts only that the block flag becomes `None`.
   Fix: refuse to clear a commit op unless the branch tip and the real index both match the journalled commit.

6. **Medium. PROVEN (code).** `static/app.js:1336`. Spec `docs/worktree-review-plan.md:212`. Field is returned at `worktrees.py:1000`.
   Trigger: a path is both staged and unstaged (`staged_differs`).
   `reviewBanners` never mentions it, so Commit is offered with no "staged version kept as a recovery ref" line. The recovery ref is written only when a commit actually runs (`worktrees.py:1182`). The nothing-to-commit return at `worktrees.py:1179` skips it, which is the refresh in finding 1.
   Fix: render the banner from `snap.staged_differs` and keep Commit disabled until it is shown.

7. **Medium. PROVEN (code).** `sessions.py:2334`.
   Trigger: Discard, then `discard()` refuses (tree changed, or a process still inside).
   The agent is cancelled and the pane set to `detached` before `discard()`. A refusal leaves the worktree in place and the turn already dead. Resume works when the entry is not blocked.
   Fix: run the process scan and the tree check first; kill the agent only once the move will proceed, and if `discard()` raises, leave the pane resumable.

8. **Low. PROVEN (code).** `worktrees.py:1307` and `worktrees.py:1319`.
   Trigger: `POST` publish with `pr.repo` set to some other `owner/name`.
   `open_pr` never checks `repo` against `github_repo(push_url)`. `pr list --head` uses the bare branch, while `pr create` uses `owner:branch` for a fork, so an existing fork PR is missed and a second PR can be opened. The dialog sends the confirmed repo; this is the direct route. Not run against `gh`.
   Fix: require `repo == github_repo(push_url)` unless the head is the fork form, and list with that same head.

9. **Low. PROVEN (code).** `worktrees.py:1393`.
   Trigger: discard's `/proc` scan, and `readlink` of one pid's `cwd` raises.
   That pid is skipped before its fds are scanned, so a process that has the worktree open can be treated as absent. A failure to list `/proc` itself does raise `ScanFailed` (tested). This per-pid path does not.
   Fix: if `cwd` cannot be read, still scan `fd`, and on a second failure raise `ScanFailed`.

10. **Low. SUSPECTED.** `worktrees.py:622`.
    `same_fs_as_root` is recorded and never added to `refusals`. Plan §2.3 lists a different filesystem as a refusal. Discard's `worktree move` stays under the worktree root, so this alone did not lose data.

## TEST GAPS

1. Fail `_reconcile_index` after `update-ref` in a live process, with and without a leftover `index.lock`. Assert the op is `unknown` or the index matches the reviewed tree before the lock is released, and that `git commit` with no `-a` does not move the tip onto the old blob.
2. `resolve_op` for `push` when `ls-remote` returns rc 128 or raises `GitTimeout` stays `unknown`, and reconcile still walks the next entry.
3. A real `post-checkout` that exits 1 leaves no git-listed worktree and no `corral/<slug>` branch. The current failed-add test mocks `git` and never creates either.
4. `_worktree_action` and `openReview` refuse a pane with an `unknown` op and a pane with an `intent` commit. `T-CLI-15` only checks that resolve clears the flag.
5. `reviewBanners` includes `staged_differs`, and Commit stays disabled until that line is shown. `T-CMT-10` checks the recovery ref only.
6. Restart with `index.lock` present (valid new index bytes, and garbage). `T-CRS-2` SIGKILLs at the journal points while the lock is absent, then expects a clean status.

## CHECKED AND SOUND

- Section 4: a 600 KiB untracked `huge.bin` is listed in `too_big` and is absent from the snapshot tree and from the commit (`a.txt` only).
- The commit path re-snapshots under the repo lock and requires the same tree OID and index sha256; `update-ref` is compare-and-swap; `commit-tree` is what lands the reviewed tree (`worktrees.py:1172`).
- Hub push arguments are `--porcelain`, then `--`, the confirmed URL, and `oid:refs/heads/...` (`worktrees.py:1266`). No `--force` on that argv. A dirty tree is refused before push.
- Snapshot copies the index and runs `add` / `write-tree` with `GIT_INDEX_FILE` on the copy. Section 2 showed the real index bytes unchanged by a later snapshot. `read-tree` is invoked without `-u` (`worktrees.py:1136`).
- Section 1: a failed `worktree add` left the main checkout's index and `HEAD` bytes unchanged.
- Purge requires the typed branch name, `phase == trashed`, and a trash realpath, and deletes the branch only with the discard OID (`worktrees.py:1526`).
- Review nodes are built with `el()` / `textContent`. Action bodies are taken from the snapshot (`static/app.js:1309`). A 409 reloads the snapshot (`static/app.js:1329`).
- The 5 s observe loop calls pane `snapshot()`, not git (`hub.py:106`). `ROUTING_ENV` strips `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, and the other named routing variables (`worktrees.py:56`). The suite itself was not re-run.

The bug most likely to bite Craig in the first week is a Commit whose index swap fails: the branch already holds the reviewed commit, the index still holds the previous blob, the pane stays usable, and the agent's next `git commit` or `git restore` puts the unreviewed bytes on the tip or back into the files.
