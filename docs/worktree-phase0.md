# Worktree Phase 0 (WS0) — results

Run 2026-10-02 on omarchy-laptop (Arch, btrfs `/home`, git 2.55.0, a source
build of git 2.38.5, Claude Code 2.1.287). No product code was written. The
scratch repo is `~/wt-spike/repo`: a small Python package with one failing
unittest and a local bare remote at `~/wt-spike/remote.git`. Lane worktrees
were made by hand at the D2 default root,
`~/.local/share/corral-light/worktrees/repo-e44b92/p0-<lane>`, and each lane
was opened there with `corral-light open --posture edits`.

**Verdict.** The plan holds. Twelve changes follow from the results; they are
listed at the end and applied in plan v3.2.

## Lane matrix (§5.3)

| Step | Claude | Codex | Grok | Gemini |
|---|---|---|---|---|
| Starts on a never-trusted worktree path; no stall | pass | pass | pass | pass |
| Fixes the test; tool `locations` inside the worktree | pass | pass, but **no locations reported** | pass | pass |
| Runs the tests in the worktree | pass | pass | pass | pass |
| Agent `git commit` | succeeds | **fails**: `index.lock` in the admin dir is a read-only filesystem in its sandbox (expected) | succeeds | succeeds |
| Shell commands reach the approval rail | yes (2 asked, answered in the wall) | no prompt: sandbox auto-runs | yes (3 asked) | **no prompt: lane runs `mode = yolo` whatever the posture** |
| Hub Commit (protocol run by hand, §2.2) | — | pass, on the uncommitted fix | — | — |
| Push to a remote | — | pass (local bare remote, by URL and OID) | — | — |
| PR via `gh` | not run | not run | not run | not run |
| Pause, resume on the branch | pass (`pause`/`resume`; no hub restart) | not run | not run | not run |
| Discard, restore, purge (by hand) | — | — | pass | — |

All four lanes ran at once on one repo. Nothing was written outside the
worktrees. The main checkout's two `__pycache__` dirs predate the panes and
came from a manual test run.

**Not run, with the reason.**
- **PR via `gh`.** It needs a throwaway GitHub repo, which is an outward
  action not asked for. It belongs in P4 dogfood.
- **Hub restart.** The operator's live session runs in the same hub. Pane
  pause and resume stood in for it. T-CRS-* covers restart in the build.

## Git behaviour (items 0.3, 0.5 and the plan's claims)

| Claim | Result, on 2.38.5 and 2.55.0 |
|---|---|
| `git worktree move` on a dirty worktree | **Succeeds without `--force`**: modified, staged, untracked and ignored files all move; registration follows |
| `move` of a locked worktree | Refused, including with a single `-f` (needs `-f -f`, which D9 never uses) |
| `move` while `index.lock` exists | **Succeeds**. The lock does not stop a move, so waiting for it in D9 is the hub's own guard |
| `worktree remove` on a dirty worktree | Refused without `--force`; with `--force`, deletes everything, `.env` and ignored data included (D9 confirmed) |
| `merge-tree --write-tree -z --name-only` | Exists on 2.38; output bytes and rc identical across versions (rc 0 clean, rc 1 conflict). Fixtures in `testkit/fixtures/merge-tree/` |
| `commit-tree` with `commit.gpgSign=true` and a bogus key | rc 0, unsigned commit: it ignores the setting (D5 refusal stays as policy) |
| CAS `update-ref` with a wrong old value | rc 128; stderr names the ref's actual value |
| Linked worktree's index | `rev-parse --git-path index` gives `<common_dir>/worktrees/<name>/index` |
| Failed non-fast-forward push | **rc 1** (not 128); `[rejected] … (non-fast-forward)` on stderr |
| `remote get-url --push --all` | Applies `remote.<n>.pushurl` and `url.<base>.pushInsteadOf` |

**Commit protocol, by hand.** Run on the Codex worktree's uncommitted fix:
snapshot, re-check of tree and index identity, `commit-tree`, CAS
`update-ref`, then index reconcile on a copy. The end state matches §2.2 step
7: branch == new, `write-tree` of the real index == reviewed tree, and
`git status` clean. A scripting slip in the first attempt stopped after
`update-ref`. That reproduced the journalled `ref_moved` stage: a stale
index, with `git status` showing `MM` and phantom deletes. Re-running step 6
alone recovered it, which is the planned restart path.

**Discard, by hand.** Run on the Grok worktree after closing its pane:
recovery ref (a commit of the snapshot tree, with HEAD as parent), then
`worktree move` into `<root>/.trash/`. The branch was kept, the recovery
tree held the uncommitted file, and trash held `.env`. Restore moved it
back intact. Purge removed only the trash path, deleted the branch through an
OID-checked `update-ref -d`, and kept the recovery ref.

**`/proc` scan.** With the Grok pane open, the scan found two processes with
their cwd in the worktree: the agent (`grok agent stdio`) and Corral's own
`corral_core/seat_mcp.py`. After `close`, it found none.

## Timings (item 0.4)

Each worktree had 20 tracked files modified, 5 small untracked files and a
10 MiB untracked file (the 10 MiB file was excluded from the snapshot).

| Repo | Tracked files | summary (diff + ls-files) | snapshot (warm) |
|---|---|---|---|
| corral-light | 185 | ~40 ms | 16 ms (47 ms cold) |
| synthetic | 50,000 | ~95 ms | ~180 ms |

`summary` wrote no objects and left the real index byte-identical (T-SUM-4,
T-SUM-5 shape confirmed). Every figure is far inside the §2.2 timeouts.
`~/aios` is not a git repo, so the corral-light repo stood in for it. Script:
`testkit/fixtures/p0_time.sh`.

## Path-keyed state (items 0.2 and 0.6)

- **Claude trust.** The pane's private `.claude.json` keys trust as
  `projects["<abs path>"].hasTrustDialogAccepted`. The Claude pane started
  and ran on the never-trusted worktree path with **no trust entry and no
  stall**, and shell commands still reached the rail. Pre-trust is not
  needed, so v3.2 drops it.
- **Claude project dirs.** Transcripts go under the worktree's own project
  key. Auto-memory goes under the **main repo's** key
  (`-home-cvande-wt-spike-repo/memory`). Worktree panes therefore share the
  repo's project memory.
- **Claude "allow always" escapes the worktree.** Answering "allow always" in
  the Claude worktree pane saved its rules (`Bash(git add *)`,
  `Bash(git commit *)`, two more) to the **main checkout's**
  `.claude/settings.local.json`, not the worktree's. So one worktree pane's
  approval widens what every later Claude session in the main checkout may
  run unasked. Grok kept its answers for the session only.
- **mise.** A trusted `mise.toml` with an `[env]` block loaded in a fresh
  worktree with rc 0 and no trust prompt. The cause was not determined.
  Nothing blocks.
- **`includeIf "gitdir:"`.** None is configured on this machine. A worktree
  under the state dir gets the global identity, as the main checkout does.

## Findings that change the plan (applied in v3.2)

1. **D9, discard.** Use a plain `git worktree move`, never `--force`.
   Phase 0 shows a dirty tree moves without it. The wait for `index.lock`
   stays, because the lock does not block a move.
2. **D9, the writers to stop.** They include hub-spawned helpers that
   inherit the pane's cwd (`seat_mcp.py`), not just the agent's process
   group. The `/proc` scan catches them. Kill them with the agent, or refuse
   and name them.
3. **Claude pre-trust removed.** §2.3 drops it, T-LIF-14 asserts the config
   dir gains no trust entry, and ship-gate item 8 is met by this run.
4. **Codex gives no edit locations.** The out-of-worktree edit guard (§2.6)
   cannot see Codex edits. The Codex lane relies on its sandbox, which this
   run proved keeps it out of the admin dir. The README says so.
5. **Gemini ignores the posture.** The lane reports `mode = yolo (lane
   default)` and never asks. That is pre-existing lane behaviour, not a
   worktree issue. Under D8 the Gemini lane is held back from worktrees
   until its posture is honoured, or until Craig accepts it knowingly.
6. **Publish exit codes.** Push failure causes are read from stderr; the
   non-fast-forward case is rc 1. This applies the existing rule for
   `update-ref` to `push` as well.
7. **Generated files.** Every lane's `git add -A` swept `__pycache__` *.pyc
   into its commit, and the hub's snapshot would too, because the repo
   ignores neither. Review lists binary files first, under a banner saying
   "N binary files, often build output", so they are seen before Commit.
8. **merge-tree.** The argv and layout are frozen from the fixtures. The
   parser's tests read the fixture files.
9. **Lanes enabled after Phase 0.** Claude, Codex and Grok. Gemini is held,
   per item 5.
10. **Claude "allow always" in a worktree pane.** The rail offers only
    allow-once for worktree panes, or the hub refuses the persistent option.
    Otherwise approvals land in the main checkout's settings (above). This
    needs a check of whether the ACP adapter lets the hub filter the option.
11. **Operator notice.** The run opened four panes in the operator's wall
    without saying so, and the operator answered their prompts as real
    work. Future lane matrices title panes `TEST — …`, put "this is a
    test" in the first message, and announce the run before the panes open.
12. **Still open, moved to P4.** A `gh` PR on a throwaway repo, a real hub
    restart with live worktree panes, and three lanes published at once.

## Leftovers on disk

- `~/wt-spike/` holds the scratch repo, remote, fixtures and the git 2.38
  build, plus experiment worktrees under `~/wt-spike/root/`.
- Under `~/.local/share/corral-light/worktrees/repo-e44b92/` are
  `p0-claude`, `p0-codex`, `p0-gemini`, `p0-mise` and `p0-mise2`, all
  registered to the scratch repo.
- Four closed panes titled `wt-p0 <lane>` remain.
- Nothing is in any real repo. The one registration made in corral-light was
  removed, along with its branch.
