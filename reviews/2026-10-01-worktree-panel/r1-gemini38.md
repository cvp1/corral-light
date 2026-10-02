### 1. Verdict

- **(a) Overall approach: RESHAPE** — Parallel worktrees are the right primitive, but executing `--no-ff` merges inside the user's active, editor-attached checkout violates the cardinal rule of non-destructive agent tooling.
- **(b) `worktrees.py` design: RESHAPE** — The temporary-index trick bloats `.git/objects` with unreferenced loose objects on every summary tick, and `merge-tree` exit code 1 handling is broken as designed.
- **(c) Lifecycle integration: BUILD** — Managing worktrees outside `Manager._lock`, retaining them across crashes, and tying them strictly to pane lifecycle and `from_meta` is disciplined and fits Corral Light.
- **(d) Review UI: BUILD** — Clean, predictable ES-module UI preserving the parity table; diff caps and preview digests prevent browser freezing and race conditions.
- **(e) Test plan: RESHAPE** — Missing critical data-loss scenarios (abort semantics, dirty merge failures, hook failures, object leak checks) and contains untestable assertions.

---

### 2. Lost Work

1. **Discard on uncommitted agent edits**
   - *Trigger:* User clicks "Discard" in Review on a worktree containing uncommitted files.
   - *What happens:* `remove()` runs `git worktree remove --force` followed by `git branch -D`. Uncommitted working tree files have no git object history and no reflog; they are erased permanently.
   - *Fix:* Before removal, stash or synthesize a dangling commit via temporary tree write (`git write-tree` or `refs/corral/discarded/<pane-id>`) with a 30-day expiry. Never `--force` delete uncommitted edits without capturing an emergency tree.

2. **In-flight agent writes during Review "Commit"**
   - *Trigger:* The user clicks "Commit" while an agent is mid-turn streaming file writes to disk.
   - *What happens:* `diff()` digest was captured milliseconds earlier. The digest matches if checked against index/HEAD, but `git add -A` stages half-written, syntactically broken agent files. The agent's subsequent write fails or causes corrupted commits.
   - *Fix:* Hub must reject "Commit", "Merge", and "Discard" if the pane status is `running` / `turn_active`. Require the turn to end, or explicitly pause/stop the agent prior to staging.

3. **Silent clobbering via merge into main checkout**
   - *Trigger:* User approves "Merge", main checkout is on base and `git status` reports clean, but Craig has unsaved buffers open in VS Code / Vim / Emacs.
   - *What happens:* `git -C repo merge --no-ff` modifies files on disk under the editor. The editor's auto-save overwrites the merged code, or editor reloads wipe Craig's unsaved undo history.
   - *Fix:* Do not merge into the user's primary working tree. Perform the merge inside the agent worktree or a detached ephemeral worktree, push the result to `base_ref` if not checked out, or instruct the user to run `git merge` in their own terminal.

4. **Untracked collisions on merge into main**
   - *Trigger:* Craig has untracked scratch files in his main checkout that match file paths created by the agent in the worktree.
   - *What happens:* `git merge` aborts with `error: The following untracked working tree files would be overwritten by merge`. The plan calls `git merge --abort`, leaving the checkout half-dirty or causing confusion. If `--abort` misbehaves, files are overwritten.
   - *Fix:* `merge_preview()` must compare agent-added files against untracked files in the main checkout (`git ls-files --others --exclude-standard`), reporting collisions in the 409 response before touching git.

5. **Worktree handover race during "Port"**
   - *Trigger:* User ports a conversation from Lane A to Lane B.
   - *What happens:* Source pane is paused, new pane is given the same worktree directory. If the source agent process has running sub-processes (build watchers, compiler, test runner, LSP), they continue running and mutate the worktree while Lane B executes.
   - *Fix:* Port must terminate the agent subprocess and all process group descendants (`SIGTERM`, wait 2s, then `SIGKILL`) before the new pane attaches.

6. **Agent git operations wiped by `reconcile`**
   - *Trigger:* Hub restarts; `reconcile()` finds a worktree path missing or detached if the disk mount lagged or path changed, and calls `git worktree prune`.
   - *What happens:* Prune deletes `.git/worktrees/<name>`, disconnecting the working directory. Even if the files remain on disk, the branch linkage and staging area are corrupted.
   - *Fix:* `reconcile()` must never run `git worktree prune` automatically on startup; it should only flag entries as `orphaned` or `unreachable` in the API.

---

### 3. Git Correctness

1. **`merge-tree --write-tree` exit code 1 handling (Fatal)**
   - *Problem:* In git 2.38+, `git merge-tree --write-tree` exits with return code `1` when there are merge conflicts (stdout still emits the conflict tree and conflict messages). The plan's `git()` wrapper specifies `check=True`, which raises `GitError` on any non-zero exit code.
   - *Fix:* In `merge_preview()`, call `git(["merge-tree", "--write-tree", ...], check=False)`. Treat rc `0` as clean, rc `1` as conflicts (parse stdout for `CONFLICT`), and rc > 1 as an actual error.

2. **Temporary index bloats `.git/objects` on every summary tick (Severe)**
   - *Problem:* The temporary index trick runs `git add -A` and `git write-tree` to include untracked files. `git add -A` hashes and writes every untracked file, generated asset, and build artifact into `.git/objects` as loose objects on every turn end and every 60s in review.
   - *Fix:* Do not use `git add -A` with a temporary index for summaries. For untracked files, run `git ls-files --others --exclude-standard` to obtain counts and names. For the diff preview, generate diffs of tracked files normally (`git diff <base> HEAD`), and for untracked files run `git diff --no-index /dev/null <untracked_file>`.

3. **Executing `--no-ff` merge in main checkout (Severe)**
   - *Problem:* `git -C repo merge --no-ff corral/<slug>` executes in the user's primary repo. This executes git hooks (`pre-merge-commit`, `post-merge`) in Craig's main environment and invalidates IDE file watchers, language servers, and compiler caches.
   - *Fix:* Fast-forward in the main checkout if base has not diverged, or perform the merge inside the agent's worktree (`git merge base`), verify tests, then fast-forward the base branch.

4. **Slug collision vs `.git/worktrees/<name>` directory clash (Moderate)**
   - *Problem:* `git worktree add -b corral/<slug>` derives the worktree admin dir name from the directory basename (`slug`). If a prior worktree was deleted via file removal without `git worktree prune`, `.git/worktrees/<slug>` still exists. `git worktree add` fails with `fatal: '<slug>' already exists`.
   - *Fix:* `create()` must pass a unique path and name, or inspect `.git/worktrees/<slug>` before creation and invoke `git worktree prune` if stale.

5. **`GIT_OPTIONAL_LOCKS=0` does not protect against write locks (Moderate)**
   - *Problem:* The plan claims T-SUM-8 proves `GIT_OPTIONAL_LOCKS=0` prevents index lock contention. `GIT_OPTIONAL_LOCKS=0` only disables background maintenance operations from acquiring locks; it does not prevent git commands that modify refs or write objects from locking.
   - *Fix:* Rely on the per-repo lock in `worktrees.py`, and run read-only git operations strictly using plumbings that do not lock the main repo index.

6. **Pre-commit and commit-msg hooks failing during Review "Commit" (Moderate)**
   - *Problem:* `commit()` runs `git commit -m` without `--no-verify`. The worktree lacks `.venv`, `node_modules`, or global tools. Linters/formatters invoked by pre-commit hooks will crash or fail.
   - *Fix:* Ensure `commit()` captures hook stdout/stderr with clean error reporting. Provide an explicit UI checkbox in the review dialog: "Bypass hooks (`--no-verify`)".

7. **Submodule de-synchronization (Moderate)**
   - *Problem:* `git worktree add` does not initialize or checkout submodules. Submodule folders appear empty in the worktree, breaking project builds and tests.
   - *Fix:* `probe()` must detect `.gitmodules`. If present, warn the user prominently in the "New conversation" dialog that submodules are uninitialized.

8. **`safe.directory` violations across differing user contexts (Minor)**
   - *Problem:* When `$CORRAL_LIGHT_WORKTREES` is placed under `~/.local/share/...`, if the hub or any agent runs under an alternate UID or container mount, git 2.55 halts with `detected dubious ownership in repository`.
   - *Fix:* Run all git commands with `-c safe.directory=*` or explicitly register the worktree root.

9. **Detached HEAD base branches (Minor)**
   - *Problem:* If the main checkout has a detached HEAD (T-PRB-4), `create()` records `base_ref = base_sha`. Later, `merge()` fails because `not_on_base` checks cannot match a ref, and base cannot be updated.
   - *Fix:* Disallow "Own branch" if main is in a detached HEAD state, or restrict such panes to "Push/PR only" (disable "Merge into base").

10. **File rename detection limits in large diffs (Minor)**
    - *Problem:* `diff()` runs with `-M` without specifying `diff.renameLimit`. On repos with >1000 files modified, git disables rename detection or takes seconds to calculate.
    - *Fix:* Pass `-c diff.renameLimit=1000` and cap file comparisons.

---

### 4. Agent Realities

*(Self-knowledge represents confirmed knowledge of agent/CLI behaviours; Guess represents inferences regarding ACP harness implementations).*

- **Claude Code:**
  - *Directory Trust:* Claude Code checks directory trust based on canonical path. A worktree path in `~/.local/share/...` is an unknown directory; it prompts the user to trust the folder on startup. *(Self-knowledge)*.
  - *ACP Hang:* If Claude's ACP adapter does not pipe or handle the trust prompt, the handshake blocks up to 180s and dies. *(Self-knowledge)*.
  - *Repo Root:* Claude Code correctly identifies the worktree root using `git rev-parse --show-toplevel`. Committing works if hooks succeed. *(Self-knowledge)*.

- **Codex (ChatGPT):**
  - *Sandbox Containment:* Codex workspace-write restricts filesystem writes to `cwd`. In a linked worktree, the `.git` file points to `<common_dir>/worktrees/<name>`. Git write operations (`git add`, `git commit`, `git status` refreshing index) attempt to write to that common directory outside `cwd`. *(Self-knowledge)*.
  - *Failure Mode:* Agent-side `git commit` and `git add` will fail with an OS `EPERM` / sandbox violation. *(Self-knowledge)*.
  - *Committing:* Codex *must* rely on hub-side committing via Review (D5). Launcher configuration must not attempt to expose the entire `.git` tree to the sandbox as that compromises containment. *(Self-knowledge)*.

- **Gemini / Antigravity:**
  - *Repo Root:* Antigravity respects `cwd` passed via ACP session initialization. Tools that locate git root via `git rev-parse --show-toplevel` work correctly inside the worktree. *(Self-knowledge)*.
  - *Committing & Editing:* Edits remain inside the worktree. Commits initiated by the agent succeed because write permissions in the Gemini ACP runner cover the git directory linked by the worktree. *(Self-knowledge)*.

- **Grok:**
  - *Path Traversal:* Grok tool runners typically resolve `cwd`. If Grok uses custom scripts that check for `os.path.isdir(".git")` instead of `os.path.exists(".git")`, git detection fails (since `.git` in a worktree is a text file, not a directory). *(Guess)*.
  - *Escaping:* If Grok relies on standard git CLI, it remains contained in the worktree. *(Guess)*.

---

### 5. §7 Open Questions Answered

**1. Worktree location (`~/.local/share/...` vs `<repo>/../<repo>.corral/<slug>`):**
Place them beside the repo: `<repo>/../.<repo_basename>.corral/<slug>`. Putting worktrees in `~/.local/share` breaks relative paths, toolings, language servers, Docker mounts (`docker run -v $(pwd):...`), and `.env` references. Placing them in a sibling hidden directory allows Craig to easily inspect them in an editor, keeps them on the same filesystem/mount as the repo (preventing cross-device link issues), while keeping the main directory clean.

**2. Merge in main checkout (D6) vs base ref directly:**
Do **not** merge in the main checkout if it is currently checked out. If the main checkout is on `base_ref`, merging directly touches Craig's active workspace. Instead, perform the merge inside the agent's worktree (`git merge main`), resolve cleanly, and if fast-forwardable, fast-forward `main` only if clean. If base has diverged, require the user to pull/merge, or merge via `git merge-tree` and update refs only when main is *not* currently active on that branch.

**3. Temporary index vs `git add -N`:**
Use neither. `git add -N` mutates the agent's real index, causing race conditions with the agent's own git operations. The temporary index writes unreferenced loose objects into `.git/objects` on every summary. Instead, generate diffs for tracked files via `git diff <base> HEAD`, and gather untracked files via `git ls-files --others --exclude-standard`, streaming untracked contents directly or via `git diff --no-index /dev/null <file>`.

**4. One owner per worktree (F8) vs join read-only:**
Strictly **one owner per worktree**. Allowing multiple agent panes to bind to a worktree (even read-only) leads to lock contention, index corruption if both agents run git, and confusing review flows. Read-only exploration belongs in a scratch directory or via ACP context tools, not shared worktrees.

**5. Missing tests:**
Detailed in Section 6. The catalogue omits tests for in-flight write collisions during commit, loose object buildup from the temp index, `merge-tree` exit code 1 handling, and worktree git lock contention.

---

### 6. Test Plan Audit

#### Missing Tests (New IDs)
1. **T-GIT-5 (`merge-tree` conflict rc=1):** Verify `git merge-tree --write-tree` on conflicting branches does not raise `GitError` and correctly parses conflict markers.
2. **T-SUM-9 (Object DB leak check):** Verify running `summary()` 20 times on a repo with a 10MB untracked file does not create 200MB of loose blobs in `.git/objects`.
3. **T-CMT-6 (In-flight agent lock):** Verify `commit()` returns 409 Conflict if the pane's agent subprocess is actively running a turn.
4. **T-CMT-7 (Hook failure bypass):** Verify commit failure due to a pre-commit hook returns hook stderr and allows a subsequent commit with `no_verify: true`.
5. **T-MRG-10 (Untracked main collision):** Verify merge returns 409 `untracked_collision` when an agent-added file matches an untracked file in the main checkout.
6. **T-MRG-11 (No dirtying of main on merge abort):** Verify an aborted merge leaves the main working tree byte-identical to pre-merge state.
7. **T-RMV-9 (Discard emergency stash):** Verify discarding a dirty worktree records its state in a recovery ref (`refs/corral/discarded/...`) before deleting.
8. **T-LIF-27 (Port process termination):** Verify porting a pane sends `SIGTERM` to the source process group and ensures all descendant processes release file descriptors.
9. **T-PRB-8 (Detached HEAD merge disabled):** Verify `probe()` flags detached HEAD and disables the "Merge into base" action in the review payload.
10. **T-UI-24 (Large file binary guard):** Verify the browser review UI renders a placeholder and suppresses DOM rendering for files exceeding 500KB.

#### Untestable Tests as Written
- **T-SUM-8:** *"runs with GIT_OPTIONAL_LOCKS=0: a concurrent agent git add never sees index.lock contention from us"*. Untestable as written because `git add` locks `.git/index.lock` regardless of `GIT_OPTIONAL_LOCKS=0`; you cannot assert the absence of lock contention without race condition timing fixtures.
- **T-VIS-4:** *"5,000-line diff scrolls without the page freezing (time to first paint < 500 ms)"*. In headless Chromium running on CI/tmpfs with unknown CPU throttling, 500ms time-to-first-paint is flaky and non-deterministic.
- **T-LIF-11:** *"no git runs on the 5 s observe tick (count calls over 30 s idle)"*. Testing negative assertion over a 30s sleep causes massive test suite slowdown (~770 existing tests need to run fast). Mock or spy the git wrapper directly instead of wall-clock sleeping.

---

### 7. Scope Adjustments

#### Cut from v1
1. **Direct merge into main checkout (`POST /api/session/worktree/merge`):** Too dangerous for v1. Cut local auto-merging into main; rely on "Push & PR" or supply a one-click "Copy merge command" for Craig to run in his terminal.
2. **Review Dialog "Send feedback" hunk quoting:** High UI complexity; hunk quoting into composer requires tricky selection/cursor management. Users can copy-paste diff text.
3. **Palette "Branches" manager:** Extra surface area. Listing orphaned worktrees in `corral-light doctor` is sufficient for v1.
4. **Ahead/behind live git calculation on SSE header:** Requires recurring `merge-base` / `rev-list` calculations. Keep the header pill to `files changed (+/-)` and compute ahead/behind only on-demand when opening Review.
5. **Auto-pruning of orphan worktrees:** Dangerous in v1. Report orphans in API/CLI; require manual pruning.

#### Must Add to v1
1. **Recovery ref on Discard:** Never delete uncommitted or unmerged work without writing a backup tree ref (`refs/corral/discarded/<id>`).
2. **Explicit Hook bypass option (`--no-verify`):** Without this, missing dev tools in new worktrees will brick the Review Commit action.
3. **Turn-state lock:** Absolute refusal of Commit/Merge/Discard while an agent turn is actively executing.
4. **Sibling worktree pathing:** Move worktree paths to `<repo>/../.<repo>.corral/<slug>` to preserve toolchains and build caching.
5. **Git merge conflict parser:** Correct exit-code handling for `git merge-tree` on git 2.38+.

---

### 8. One-Sentence Summary

The single most likely way this feature hurts Craig in its first week is by running a merge into his main checkout that quietly stomps unsaved editor buffers or executes failing git hooks that leave his primary working tree in an unexpected, half-merged state.