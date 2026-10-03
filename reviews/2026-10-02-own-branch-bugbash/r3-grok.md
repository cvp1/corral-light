I'll review the own-branches ship decision against the round 1 and 2 findings, then check whether the new gate, discard, and restart code actually holds.The two feature commits are `6f86aa4` and `452a623`. I'll read those diffs and the gate, discard, and restart paths they touch.The gate and restart paths look tight on a first read. Next I’ll reproduce the old failures and probe the new owner check, process scan, and review cap.The ship call hinges on whether the restart PR path still adopts a pull request the user never confirmed. I am checking that code and the probe result before writing the verdict.FIX FIRST — a crash during pull-request creation settles the op as done against the first open PR `gh` lists for that branch name, so the publication record is a PR the user did not confirm.

## BLOCKING FINDINGS

1. **High, PROVEN.** `worktrees.py:1953-1965` (`resolve_op`, kind `pr`).
   - **Trigger.** A `pr` op is still `intent` (hub died after `begin_op`, during `gh pr create`) and `gh pr list --repo <journalled repo> --head <branch> --json url` returns any open PR. The journalled `head` (`me:corral/slug`, written at `open_pr` line 1514) is never read.
   - **What goes wrong.** The op becomes `done`, `published.pr_url` is set to `listed[0].url`, and reconcile reports "pull request creation interrupted; found it". `open_pr` (lines 1498-1509) keeps a listed PR only when `headRepositoryOwner.login` matches our head, because `--head` matches the branch name in any fork. This path requests `url` only, so a fork PR that shares `corral/<slug>` is stored as this branch's PR and the user's PR is never created. `corral-light worktrees` prints that URL. A later Publish still runs `open_pr`, which filters by owner and can create the right PR; the crash recovery the journal exists for has already declared success. The push op is separate and still uses the confirmed URL.
   - **Minimal fix.** Request `--json url,headRepositoryOwner`. Accept a PR only when `login` equals the owner derived the same way as `open_pr` (an empty login does not match). If none match, set `stage=not_done` and leave `published.pr_url` unset.
   - **Repro.** `scratch/probe_r3.py` section 1. The stub prints pull/1 then pull/9. Registry `scratch/probe-wt/registry/wt-8863d1.json` has that op at `state=done`, `url=https://github.com/o/r/pull/1`. Isolated suite: 301 tests, OK. `test_BB3_11` covers `open_pr` only, so this path stays green.

## NON-BLOCKING

1. `open_pr:1507` treats a missing `login` as ours. Same probe, section 2: a list item with no `headRepositoryOwner` is reused (`pull/3`) and `pr create` is skipped. Real `gh` usually includes the login.
2. Discard whose two looks agree, then change while the agent is stopped, stops the agent and refuses `changed`. The worktree stays; Resume continues (`T-RMV-11`).
3. `commit_still_unsettled` allows `resolve --op` when the worktree path is not a directory. The hint `git reset -q` is a mixed reset and drops index-only blobs if followed.
4. `rewrite_rule` ignores `git config`'s exit status, so an unreadable config skips the second-rewrite check. A matching prefix also refuses a rewrite that would leave the destination unchanged.
5. A process whose `/proc` cwd and fds are both unreadable is omitted from the discard scan. Ordinary git on this kernel is visible (`probe_r3.py` sections 3 and 4, including a live `index.lock` holder).
6. `fit_review` only shrinks the diff file list, so a huge `too_big` / `ignored` list can pass the 2 MiB review budget. The probe's ordinary diffs stayed at or under the cap.
7. `create` on `GitTimeout` after `worktree add` sets phase `missing`, and `sessions.create` drops the pane. Reconcile returns immediately for `missing`, so a slow add can leave a worktree with no pane. No agent edits exist yet.
8. `test_BB3_21` and `test_BB3_22` assert on source text. `test_BB3_11` never calls `resolve_op`.

A hub restart during Publish will report the pull request found, and `worktrees list` will show whichever open PR shares the branch name, until he publishes again.
