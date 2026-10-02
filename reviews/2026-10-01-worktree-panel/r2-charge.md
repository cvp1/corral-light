ROUND 2 — same panel, same rules (text only; no files or commands; label platform knowledge vs guesses).

WHAT HAPPENED SINCE ROUND 1
The author folded the round-1 findings into plan v2 (pasted in full below). Its §8 table lists every change and who raised it. Main moves: merge into the main checkout cut from v1; never automatic prune; Discard = recovery ref + `git worktree move` to a trash folder, real deletion only by typed purge; summary is write-free (`git diff --numstat <base_sha>` + `ls-files --others`), and a tree snapshot is written only when review opens so actions bind to an immutable tree OID; hub Commit uses `commit-tree` + compare-and-swap `update-ref` (no hooks; reviewed equals committed); actions refused while the pane is busy and its send queue held; a durable registry with intents and an op journal resolved by postcondition checks after a crash; port refused for worktree panes; git routing env vars stripped and no safe.directory wildcard; lanes enabled one by one after a Phase 0 matrix; Claude worktree path pre-trusted in the pane's private config dir.
From Grok: snapshot from a copy of the worktree's real index; roster slot reserved before git; a failed start keeps a dead pane that owns the worktree; plain creates into a worktree path refused; fully qualified push refspec, GH_PROMPT_DISABLED and explicit --repo; fast refusal when commit.gpgSign is set; discard pauses the owner first; edit events outside the worktree cancel the turn; a first-message preamble telling the agent not to push, rebase or touch other branches.
Rejected: a `safe.directory=*` wildcard (disables a safety check for every repo). Undecided, put back to you: §7 questions 1–4.

Below: the three round-1 reviews (yours included), then plan v2.

YOUR JOB — converge
1. CROSS-EXAMINE. For each other reviewer: the finding of theirs you most agree with, and the claim you think is WRONG (factually or as design), with why. Change any of your round-1 verdicts if warranted and say so.
2. RE-VERDICT on v2, one line each: (a) approach, (b) worktrees.py, (c) lifecycle, (d) review UI, (e) test plan — BUILD / RESHAPE / KILL.
3. REMAINING LOST-WORK PATHS in v2, ranked, at most 6, each with trigger and fix. New problems introduced by the v2 changes count double (look hard at: commit-tree + read-tree into the real index after commit; worktree move into trash and back; the registry/journal; held send queues; pre-trusting Claude).
4. GIT FACT CHECK: any v2 git claim you believe is wrong (flags, exit codes, what `git diff <commit>` compares, what `worktree remove`/`move` do with ignored files, update-ref CAS semantics, merge-tree -z output). At most 6.
5. ANSWER §7 Q1–Q4, one short paragraph each, with a recommendation.
6. SHIP GATE: is §5.4 sufficient? Add or remove at most 3 items.
7. ONE-LINE BOTTOM LINE for Craig.

FORMAT: Markdown, under 1,300 words. No preamble.


==== ROUND-1 REVIEW: SOL (gpt-6.1-sol) ====
**(a) Overall — BUILD:** Worktrees fit parallel agents, provided isolation is described as separate checkouts, not enforced filesystem confinement.  
**(b) worktrees.py — RESHAPE:** Its digest and Python lock cannot make mutations atomic against agents, editors, hooks, or external git.  
**(c) Lifecycle — RESHAPE:** Ownership transfer and crash recovery lack durable transactions and process quiescence.  
**(d) Review UI — RESHAPE:** Actions need explicit reviewed revisions, incomplete-review warnings, and recoverable failure states.  
**(e) Test plan — RESHAPE:** Broad happy-path coverage misses the concurrent writes and partial failures most likely to destroy work.

## Lost work

- **Agent writes after preview:** Digest validation succeeds, then `add -A`, merge, or removal consumes newer work. **Fix:** Block new turns, stop and await tool subprocesses, serialize hub actions, revalidate immediately before mutation. External editors remain uncontrolled; use immutable commit/tree targets and recoverable disposal rather than promising atomicity.
- **Discard deletes ignored files:** A “clean” worktree can contain `.env`, datasets, generated artifacts, and ignored agent output; normal worktree removal can delete these. **Fix:** Inventory ignored files and nested repositories explicitly; archive the complete directory before destructive disposal, or refuse pending manual cleanup.
- **Reviewed branch changes identity:** Agent checkout, reset, or branch rename leaves metadata pointing at another ref. Merge/push/delete may affect different work than the preview. **Fix:** Verify registered worktree, symbolic HEAD, branch identity, and expected OIDs on every action; quarantine mismatches.
- **Hooks change the commit:** A hook edits/stages additional content after review, or failure leaves the real index changed. **Fix:** Record pre-operation HEAD/index, show actual resulting commit and residual changes, and require renewed review before merge/publish. Never silently reset staged work.
- **Main checkout changes during merge:** Clean-check passes, then Craig edits or runs git. Automatic abort can interfere with his edits or an unrelated merge. **Fix:** Cut main-checkout merge from v1; later require explicit exclusive-use acknowledgment and transaction-specific recovery.
- **Timeout/crash after mutation:** Commit, merge, push, or PR creation can succeed before the response disappears. Retrying can duplicate actions; hook children may survive. **Fix:** Durable operation journal, process-group termination, postcondition reconciliation, and “outcome unknown” states; never infer rollback from an exception.
- **Creation before pane persistence:** A crash leaves a branch/worktree with no record. `reconcile(records)` cannot discover a previously unknown repository. **Fix:** Persist a creation intent and registry containing common directory, path, branch, and creation OID before git runs; enumerate registry plus root after restart.
- **Port transfers ownership too early:** Starting the target before stopping the source permits simultaneous writes; failed handshake or crash strands ownership. **Fix:** Durable handoff state machine: quiesce source, reserve ownership, start target, commit transfer; recover failures without allowing two writers. Enforce lane eligibility on port too.
- **Wrong checkout/base:** Selecting a repository subdirectory loses the relative cwd; selecting a linked worktree does not identify the main checkout. Detached/unborn HEAD also lacks the proposed merge target. **Fix:** Preserve relative cwd, resolve checkout registrations explicitly, reject unborn HEAD, and require an explicit target for detached starts.
- **Publish misplaces work:** Push excludes uncommitted edits even though review includes them; changing remote configuration can redirect publication. Deleted local branches leave remote branches/PRs alive. **Fix:** Require clean, reviewed commits; bind confirmation to destination URL, ref and OID; report local/remote outcomes separately.
- **Cache hides edits:** `(HEAD, index mtime)` stays unchanged during ordinary unstaged edits, so review readiness can disappear. **Fix:** Never use that cache for action validation; refresh working content and label summaries stale.

## Git correctness — ranked

These are findings from git internals knowledge, not code inspection.

1. **Temporary-index digest is not a byte snapshot.** Clean filters, CRLF normalization, ignored files and staged-only content break T-DIF-5. Fix: define the digest as the proposed Git tree plus HEAD, separately track real-index state and excluded content, and bind each action to its own additional inputs.
2. **Temporary index is not read-only.** `add` runs clean filters and writes objects; `merge-tree --write-tree` writes objects and can invoke configured merge drivers. Fix: document these effects, supervise subprocesses, and avoid calling either a harmless probe. `GIT_OPTIONAL_LOCKS=0` suppresses optional locks only.
3. **Prune cannot select paths.** `git worktree prune` operates repository-wide; “only hub paths” is not an available filter. Fix: omit automatic prune; retain stale registrations for explicit repair.
4. **`branch -d` proves the wrong thing.** With an upstream, it checks integration into upstream, which may merely be the published agent branch. Fix: explicitly test ancestry against the recorded target; delete only after verifying the expected branch OID and retaining a recovery ref.
5. **No transaction spans check and merge.** Git’s internal locks do not lock working files throughout preflight; hooks/timeouts complicate abort. Fix: remove automatic checkout merge initially; never abort a merge without proving operation ownership.
6. **Environment and config redirect behavior.** Inherited `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, etc. can target another repository. Fix: sanitize routing environment variables; explicitly set required temporary-index values. Respect `safe.directory` errors without installing a wildcard exemption.
7. **Registration and ownership exceed prefixes.** `corral/*` plus containment does not prove hub creation; symlink checks have replacement races. Fix: durable ownership registry, canonical common-directory identity, registration/ref verification, controlled root permissions, and revalidation before deletion.
8. **Diff machinery needs stronger controls.** `--no-ext-diff` does not disable textconv; newline filenames cannot safely identify files through patch headers. Fix: add `--no-textconv`, derive identities from NUL-delimited raw metadata, and attach hunks to those identities.
9. **Sparse checkout, submodules and LFS need explicit support boundaries.** Generic temporary-index `add -A` is insufficient for every sparse/index state; submodules contain independently dirty repositories. Fix: reject unsupported sparse/submodule cases in v1; detect filters and fail clearly when required tools are absent.
10. **Version/output and resource contracts are incomplete.** `merge-tree` conflict output needs structured parsing; 2.55 fixtures do not establish 2.38 compatibility. `capture_output=True` is unbounded before truncation. Fix: test minimum versions, request structured output where supported, stream bounded output, and cap the encoded JSON response.

## Agent realities

- **Claude:** Git understands linked worktrees; tracked instructions appear normally. Trust behavior and ACP prompt handling are **unverifiable from the text**. Without filesystem confinement, Claude can access the main checkout by absolute path. Committing depends on adapter permissions and hooks.
- **Codex:** Given the stated cwd sandbox, shared Git administrative paths may block staging/committing; exact protected-path behavior is version-specific and **unverifiable from the text**. Prefer hub commits. Making the entire common directory writable also exposes other branches’ refs and administrative state.
- **Grok:** Linked-worktree detection, trust and commit behavior are **unverifiable from the text**. Require the lane spike before enabling it; correct `git status` alone does not prove all tools stay confined.
- **Gemini/Antigravity:** Same uncertainty; adapter-specific tool roots and trust handling need measurement. Successful edits do not demonstrate sandbox isolation.
- **host:** Given the verified cwd behavior, refuse creation **and handoff**.
- **Ollama:** Given no tools, refuse ownership. Chat feedback can remain available without granting a checkout.

## §7 recommendations

**1. Location:** Keep the state-directory default, with configurable root. Persist ownership independently of pane metadata and preserve selected subdirectory cwd. Location does not establish containment.

**2. Merge:** Cut automatic main-checkout merge from v1. Updating an unchecked-out base ref later is viable using an expected-old-OID compare-and-swap, but only after checking all worktree registrations and defining crash recovery.

**3. Untracked files:** Use a temporary index; never `add -N` into the agent’s real index. Preserve/report staged state separately and acknowledge filter execution and ignored-file exclusion.

**4. Ownership:** One writing owner. Defer additional read-only agent panes: an instruction to avoid writes is not enforcement. Browser review requires no agent ownership.

**5. Missing tests:** Prioritize concurrent writers, operation uncertainty, ignored-file disposal, crash-safe ownership, and mutable branch/remote identities over more parser fixtures.

## Additional tests

- **T-SAFE-1:** Agent writes between validation and mutation; no unreviewed commit/publication or irreversible disposal.
- **T-SAFE-2:** Clean worktree with ignored secrets, nested repo and dirty submodule survives refused disposal.
- **T-SAFE-3:** External checkout/reset/ref replacement makes merge, publish and deletion refuse.
- **T-SAFE-4:** Hook stages extra content or fails; resulting commit/index state is surfaced accurately.
- **T-SAFE-5:** Kill hub at every journal boundary; recover creation, commit, push and PR without duplicate effects.
- **T-SAFE-6:** Port failure/restart leaves exactly one recoverable owner and no surviving source writer.
- **T-SAFE-7:** Published upstream with no base integration never qualifies for ordinary branch cleanup.
- **T-SAFE-8:** Remote URL changes after confirmation; publication refuses.
- **T-SAFE-9:** Unstaged edit with unchanged index mtime invalidates actionable review state.
- **T-SAFE-10:** Malicious inherited Git routing variables and unrelated stale registrations remain untouched.

**Listed tests needing repair:** T-DIF-5 is false as written; T-CRT-9 cannot guarantee rollback after timeout; T-REC-5 requires unavailable selective-prune semantics; T-ISO-1 cannot prove absence of arbitrary external writes from a real-root snapshot. T-LIF-16 needs a real subprocess crash harness, not its listed in-process level.

## Scope

**Cut:** Automatic main-checkout merge; automatic pruning; port handoff until transactional; unverified lanes; mandatory PR creation alongside push.

**Cannot ship without:** Durable ownership/operation registry; quiescence and action-specific revision binding; recoverable disposal including ignored files; identity checks on every action; truthful partial-failure and incomplete-review UI.

## One sentence

Craig will discard an apparently clean agent worktree and discover that its ignored configuration or generated output disappeared with it.

==== ROUND-1 REVIEW: GEMINI (gemini-3.8-flash-high) ====
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

==== ROUND-1 REVIEW: GROK (grok-4.7) ====
(a) Overall approach — **RESHAPE**. Opt-in worktrees, one git module, and digest-bound review are the right shape, but as specified Merge and Discard can rewrite the live checkout or drop the only copy of uncommitted work.
(b) `worktrees.py` — **RESHAPE**. The boundary, path guards, and "never `rm -rf`" rule are sound; the temp-index, prune, commit, and merge sequences are wrong git.
(c) Lifecycle — **RESHAPE**. Cap-check, lock discipline, and meta round-trip are right; a failed start or port leaves a worktree with no owner, or two writers.
(d) Review UI — **BUILD**. A pill, a diff dialog, and a separate badge fit the wall; it needs fail-closed buttons, not a new screen.
(e) Test plan — **RESHAPE**. The catalogue is wide, but the ship gate never runs the races that lose work, and three tests as written demand impossible or harmful behavior.

## Lost work

1. **Merge "recovery" runs in the checkout he types in.** Trigger: Merge while that checkout is on the base, a hook or GPG prompt fails, the hub is killed, or a later attempt sees `MERGE_HEAD`. `merge --no-ff` already edits those files. T-MRG-5 then requires the pre-merge HEAD and a clean tree, which is `reset --hard` plus maybe `clean`. That drops unsaved buffers, conflict resolutions, and untracked files, and an auto-abort on restart aborts a merge Craig started himself. **Fix:** compare-and-swap the previewed base sha against a porcelain-empty tree, including untracked non-ignored files. Persist `{pre_head, agent_sha}` first. `merge --abort` only if `MERGE_HEAD` equals that marker. Never `reset --hard` or `clean`. If the merge commit exists, keep it and show the hook output.

2. **The reviewed tree is not the committed tree.** Trigger: Commit or Publish after the agent `git add -f`s a secret, or writes one more byte after the re-diff. The temp index is `read-tree HEAD` then `add -A`, so staged ignored files never enter the diff, while `commit()`'s second `add -A` on the real index commits them. Publish pushes `HEAD` only, so a dirty review is not the PR. The per-repo lock does not include the agent. **Fix:** copy `git rev-parse --git-path index` (not `common_dir/index`), `add -A` only on the copy, digest `(HEAD, tree, base_tip)`. Commit that exact tree. Refuse Publish and Merge when dirty or when `HEAD`'s tree ≠ the digest. Disable both while a turn is in progress.

3. **Discard deletes the only copy.** Trigger: Discard or Forget-with-removal, including a confirm typed on a stale digest, including while the agent is still running. `worktree remove --force` deletes untracked and unstaged files, which have no blob; `branch -D` deletes the branch reflog. On Linux the agent keeps writing into the unlinked directory. **Fix:** refuse while the owner process is alive; recheck the digest under the lock; write `refs/corral-graveyard/<slug>/<time>` first; move the directory aside under the hub root; only then remove and `-D`.

4. **A failed start orphans the branch.** Trigger: two creates racing at 11 panes, the browser giving up during the 180s handshake, or `start()` raising after the agent wrote files. T-LIF-6 removes the pane and keeps the branch, then says "Forget removes it" when there is no pane. A retry cuts a second branch. **Fix:** reserve the roster slot under `Manager._lock` before any git; create the worktree outside the lock; on handshake failure keep a failed pane that owns the path.

5. **Port has two writers or zero.** Trigger: the new pane starts before the source process has stopped, or the new start fails. Both agents share the tree, or the source is `handed_off` — a status the record's enum does not list — and T-LIF-18 forbids resume. A normal New conversation whose cwd is the worktree path bypasses F8. **Fix:** stop the source, recheck the digest, then start; on failure set the source back to `active`. Refuse any create whose cwd resolves inside a hub worktree unless it is that owner resuming.

6. **The digest ignores the base.** Trigger: detached HEAD at creation, or `main` moves after the dialog is drawn. Merge-tree said clean, then `merge` conflicts inside the live checkout, or Merge is offered with no branch to land on. **Fix:** put the current `base_ref` tip in the digest. If detached, hide Merge. If `base_ref` is gone, say so.

7. **The agent leaves the worktree.** Trigger: Claude, unsandboxed, with a permission rail that has no path rules, follows the `gitdir:` pointer into the main repo, or runs `push --force`, `reset --hard`, or `branch -D`. The warning looks at the tool event after the write. **Fix:** fail that turn visibly when a tool location is outside the worktree. Seed the instruction "do not push, rebase, or touch other branches." Do not ask the agent to rebase.

8. **Prune and symlink swap.** Trigger: `reconcile` calls `git worktree prune`, or the hub path is replaced with a symlink between `lstat` and remove. Prune has no path filter, so a user worktree whose directory is only unmounted loses its admin entry. Remove can follow the symlink. **Fix:** never prune globally. Delete a hub admin dir only after its `gitdir` file points at that dir and `O_NOFOLLOW` `lstat` is not a symlink.

9. **A timeout leaves `index.lock`.** Trigger: the default 20s `subprocess` timeout SIGKILLs commit, merge, or a hook. Python kills the child, not the group. The repo he uses from the shell stays locked. **Fix:** `start_new_session` and kill the group. Timeouts: status 20s, commit and push 120s, `worktree add` 300s. Unlink a lock only when its recorded pid is dead and is the child you spawned.

## Git correctness

1. **Abort-to-clean in the live checkout.** Fix as in Lost work §1. Knowledge: `git update-ref` on a checked-out branch moves the ref and not the files, so the next commit looks like a mass revert. Never `update-ref` a checked-out base.
2. **Temp index from `read-tree HEAD`.** Drops staged and `add -f` state, and `git add` on every summary writes unreachable blobs into his object store. Fix: copy the per-worktree index; build that tree only when Review opens; header counts come from `status --porcelain=v2 -z` and `diff --numstat`.
3. **Commit via a private `GIT_INDEX_FILE` leaves the real index on the old HEAD.** The same diff returns and a second Commit duplicates it. After the ref moves, `git read-tree HEAD` into the real index under the lock.
4. **`git worktree prune` cannot be scoped.** Delete one verified admin directory, or leave the stale entry and mark the pane `missing`.
5. **`GIT_OPTIONAL_LOCKS=0` in the shared env.** It does not suppress `index.lock`. It makes status skip optional refresh and can hide a racy-dirty file. Set it only on status and summary. The cleanliness check must take the real lock and fail closed if the lock is busy.
6. **`merge-tree --write-tree` is version-sensitive.** Exit 1 still prints a tree oid; `--messages` differs across 2.38–2.55; argument order is ours then theirs. Freeze argv from the Phase 0 fixtures. A conflicted tree is a refusal, not a result. Pass the explicit base tip.
7. **`commit.gpgsign` and hooks.** `GIT_TERMINAL_PROMPT=0` and `GIT_EDITOR=true` do not stop pinentry or a hook that execs an editor. If `commit.gpgsign` is set, fail in under a second with "the hub cannot unlock your key." Put hook stderr in the 409.
8. **Unqualified push refspec, and `gh` prompts.** A tag can collide with `corral/<slug>`. On a fork, `gh pr create` asks which repo and opens the PR against the fork. Push `refs/heads/corral/<slug>:refs/heads/corral/<slug>`, never `--force`. Set `GH_PROMPT_DISABLED=1`, pass `--repo` and `--base`, show that URL in the confirm. If the PR exists, return `gh pr view`.
9. **Sparse and LFS are not warnings.** Sparse is per worktree, so `worktree add` materializes a full tree. LFS smudge can hang the add, or a later commit can replace blobs with pointer files. `probe` refuses both in v1, same as bare.
10. **Namespace and detached base.** A branch named exactly `corral` makes `refs/heads/corral/<slug>` impossible. Detached HEAD has no `base_ref`. Refuse that collision. Require `base_ref` to be a branch at that sha. Accept a slug only if it matches `^corral/[a-z0-9-]{1,40}$` after `check-ref-format --branch`. Use the user's `core.autocrlf` for both diff and commit so they agree.

## Agent realities

**Codex — worktree layout is knowledge; the sandbox is the plan's fact.** Index, `HEAD`, and objects live under `<repo>/.git`, outside cwd. Workspace-write, network off, will take edits in the worktree and deny `git add` / `git commit` when they create `index.lock` outside cwd. Guess, and Phase 0 must split it: the CLI sandboxes `show-toplevel` (isolation holds) or it sandboxes the main worktree from `git-common-dir` (edits are denied or land in the real checkout). A first-seen trust prompt under `~/.local/share` is a guess.

**Claude — knowledge.** No sandbox, and the permission rail has no path rules, so an approved command can write the main repo or force-push. `show-toplevel` is the worktree, so ordinary edits stay there. A per-pane `CLAUDE_CONFIG_DIR` does not inherit trust for the new path; a prompt with no TTY burns the 180s handshake. Tracked `CLAUDE.md` and `.claude/` do appear. Commit works, including hooks and signing.

**Gemini — guess.** If it calls `rev-parse`, it stays in the worktree. A walker that wants a `.git` directory skips the gitfile, decides there is no repo, and still edits cwd. Trust is per directory. The sandbox is off unless he enabled it. Commit probably works. Phase 0 owns this row.

**Grok — guess.** The plan names no sandbox. Shelling out to git means commit works; recognizing only a `.git` directory means it never sees the repo. It can write outside the worktree if it tries.

**`host:` and Ollama — the plan's facts.** Remote shells ignore cwd. Ollama has no tools. Leaving them out is correct.

**All lanes — knowledge.** `node_modules`, `.venv`, and `.env` are absent; shared `info/exclude` and `.git/config` do apply. The first turn often fails until an install. Say that in the dialog and stop.

## Open questions

**1. Where the worktree sits.** Keep D2, on the home filesystem, never `/tmp` and never inside the project. A sibling `<repo>.corral/` is easier for editors and easier for walk-up search or `git clean` to hit. Linked worktrees share objects by path, so another filesystem is fine. The tooltip path you already specified is enough.

**2. Where Merge runs.** Do not make D6 the v1 default. Land with Push & PR. If you keep a local integrate: when the base is checked out nowhere, `merge-tree` + `commit-tree` + compare-and-swap `update-ref` touches no checkout; when it is checked out, refuse with that path, or merge there under Lost work §1. Never move a checked-out ref. Never auto-abort later.

**3. Untracked files.** Reject `git add -N`. It writes the agent's index and the agent will commit those intents. A private index copied from the real one is the right review tree. Do not rebuild it on a timer.

**4. A second pane.** One writer. No read-only join in v1. Port is an ownership transfer with rollback, not a second cwd.

**5. Tests.** The catalogue never locks the agent out of commit and remove, never puts the base tip in the digest, never keeps a graveyard, and never asserts the main index survives a failed merge. A green suite can still ship those. IDs below.

## Tests

- **T-SAF-1** Failed merge or a dead-pid lock: main `HEAD`, index bytes, and untracked files unchanged; a `MERGE_HEAD` the hub did not write is not aborted.
- **T-SAF-2** Digest binds `HEAD`, reviewed tree, and base tip; base movement or one extra byte is a 409 and does not commit, push, or merge.
- **T-SAF-3** A staged `git add -f` ignored file is in the review tree; the commit's tree equals that id; the real index matches `HEAD`; a second Commit is a no-op.
- **T-SAF-4** Force discard with a live fake agent is refused; after stop, the graveyard ref and trash copy exist before the branch name is deleted.
- **T-SAF-5** Eleven panes, two concurrent creates: never more than twelve slots, and every path is owned by a pane the roster or the error names.
- **T-SAF-6** If the new pane's `start()` raises, the source stays `active` on that worktree; a non-worktree create with that cwd is 400.
- **T-SAF-7** Reconcile, with a user worktree whose directory is missing, does not change `git worktree list` for that path.
- **T-SAF-8** `commit.gpgsign=true` fails fast and leaves no `index.lock`.
- **T-SAF-9** Publish with uncommitted changes pushes nothing.
- **T-SAF-10** A tool path outside the worktree is a visible failure and does not clear `seen_digest`.

Untestable as written:

- **T-SUM-8.** Optional locks do not do what the sentence claims. Assert a private index and an unchanged real index while `index.lock` is held.
- **T-MRG-5.** A post-merge hook failure cannot both keep the commit and restore a pre-merge clean tree. As written it will be implemented with `reset --hard`.
- **T-ISO-1**, the clause "any repo it did not create." No suite can see every repo on the machine. Log git argv and guard the temp root.
- **T-GIT-2** as a live auth-required push. It hangs or depends on the network. Use a stub askpass.

## Scope

Cut from v1: in-checkout merge as the default land path; hunk Send-feedback; 60s dialog polling and the header `merge-tree`; worktree size and doctor disk warnings; asking the agent to rebase.

v1 cannot ship without: graveyard-before-delete; a roster owner that survives a failed start and a failed port; commit and publish of the exact reviewed tree, with the base tip in the digest; single-owner enforcement including a plain cwd; probe refusals for bare, sparse, LFS, and a `corral` branch collision, plus no global prune and no `reset --hard`.

## One sentence

The likely week-one failure is Merge or Discard treating `~/aios` as disposable: the only recovery the plan specifies runs in the checkout Craig is typing in, or it `-D`s a `corral/*` branch whose uncommitted edits were never a commit.


==== PLAN v2 (docs/worktree-review-plan.md) ====
# Isolated branches and review — implementation and test plan

Status: PLAN, v2, 2026-10-02. Nothing built. v1 went to a three-vendor panel
(Sol, Gemini, Grok); v2 folds in all three round-1 reviews. The record is in
`reviews/2026-10-01-worktree-panel/`. §8 lists what changed and why.

**What this adds.** A pane can start on its own git worktree, on its own
branch, cut from the repository the user picked. Several agents can then work
on one codebase at once without overwriting each other's files. When an agent
finishes, the user reviews what it changed as a diff and commits it, pushes it
and opens a pull request, or discards it, from the wall.

**Why.** Every comparable tool (Conductor, Claude Squad, Crystal, Vibe Kanban,
Codex cloud, Claude Code on the web) isolates parallel agents and puts the
diff at the centre of review. Corral Light has neither. Today two panes on one
repo share one working tree, and the only record of what an agent did is its
transcript.

**What it is not.** A worktree is a separate checkout, not a sandbox. An agent
with shell access can still write anywhere the user can, by absolute path.
The feature prevents accidental collisions between agents; it does not
confine a hostile one. The README says so in those words.

**The one rule above all others.** No action in this feature may make work
unrecoverable. Every destructive step first writes a recovery ref or moves
files to a trash folder under the worktree root; deletion for real is a
separate, explicit, typed-confirmation purge.

---

## 0. Decisions (proposed; Craig confirms)

| # | Decision | v2 choice | Why |
|---|---|---|---|
| D1 | Opt-in or default | Opt-in per pane, remembered per repository | Many panes are chat or ops work; a surprise branch is worse than no isolation |
| D2 | Where worktrees live | `$CORRAL_LIGHT_WORKTREES`, default `~/.local/share/corral-light/worktrees/<repo>-<hash6>/<slug>`. Refused on tmpfs or a different filesystem from the repo | One place to audit, out of the user's tree. Same-filesystem keeps trash moves atomic. Panel split; see §7 Q1 |
| D3 | Branch name | `corral/<slug>`, slug from title or first prompt, else pane id; unique against refs **and** `.git/worktrees/<name>` | Namespaced, readable, never collides with a stale admin dir |
| D4 | Base | The branch checked out in the repo. **Refused** for detached or unborn HEAD in v1 | A detached base has no merge or PR target |
| D5 | Who commits | Agents may commit. The hub's Commit writes **exactly the reviewed tree** with plumbing (`commit-tree` + compare-and-swap `update-ref`). Repo hooks do not run on hub commits; they run when the branch is pushed or merged by the user | Reviewed equals committed; a hook cannot change content after review, and a missing `node_modules` cannot brick Commit |
| D6 | Merge | **Cut from v1.** Review offers Push & PR, and "Copy merge command" for the user's own terminal | Both reviewers: merging into a checkout an editor has open can destroy unsaved work, and no lock spans check and merge |
| D7 | Push and PR | Push the reviewed commit OID to `refs/heads/corral/<slug>` on a remote whose URL is shown and bound into the confirmation. PR via `gh` when signed in, else a compare URL. Never force | Uses the user's credentials; publication cannot be redirected after confirmation |
| D8 | Lanes | Enabled per lane only after that lane passes the Phase 0 matrix. Never `host:` (cwd ignored) or Ollama (no tools) | Successful edits do not prove a lane stays in its worktree |
| D9 | Deletion | Discard = recovery ref + `git worktree move` into `<root>/.trash/`. Purge (real deletion) is a separate typed-confirmation action. Never `rm -rf`, never automatic `git worktree prune` | Ignored files (`.env`, datasets, outputs) have no git history; prune is repository-wide |
| D10 | Display state | Unchanged; review status is a separate badge | `displayState` is pinned by the cross-product parity table |
| D11 | Busy panes | Every review action is refused while the pane's turn is running or a tool call is open; during an action the pane's send queue is held | An agent writing between preview and action is the commonest lost-work path |
| D12 | Port | **Refused for worktree panes in v1** | A safe hand-off needs a transactional ownership change; v1.1 |
| D13 | Ownership registry | A durable registry under the state dir, written **before** git runs, separate from pane metadata | A crash between `git worktree add` and the pane's first save must not orphan work invisibly |
| D14 | Single owner, enforced | Any create whose cwd resolves inside the worktree root is refused unless it is the owner resuming; a failed start leaves a dead pane that still owns its worktree | A plain New conversation into a worktree path would otherwise bypass ownership; a pane-less worktree has nobody to clean it up |
| D15 | Agent instructions | Worktree panes get a short first-message preamble: work only in this folder; do not push, rebase, reset, or touch other branches; the user reviews and publishes | Cheap, and it removes the commonest ways an agent damages shared refs |

## 1. Requirements

### 1.1 Functional (v1)

- F1. In New conversation, when the folder is inside a git work tree with a
  named branch checked out and the lane is enabled, an "Own branch" checkbox
  shows the base (`main @ 3f2a1c9`) and any warnings.
- F2. Starting the pane registers intent, creates the worktree and branch,
  and starts the agent in the worktree, **at the same relative subdirectory**
  the user chose.
- F3. The header shows files changed and lines added and removed. Ahead and
  behind are computed only when review opens.
- F4. Review opens a snapshot: file list and unified diff, untracked files
  included, ignored files counted and named but not included, binaries named.
- F5. From review: Commit (message prefilled, editable), Push & PR, Copy
  merge command, Discard, Refresh.
- F6. A "Ready to review" rail card appears when a worktree pane ends a turn
  with changes the user has not opened since.
- F7. Close keeps the worktree. Reopen restores it. Forget offers Discard.
- F8. After a hub restart, every worktree pane comes back with its branch.
  Missing or tampered worktrees are reported, never repaired silently.
- F9. `corral-light worktrees` lists every registry entry (live, archived,
  orphaned, trashed) and can restore from trash or purge with confirmation.

### 1.2 Non-functional

- N1. No git call holds `Manager._lock`. Every git call has a timeout and runs
  in its own process group, killed as a group on timeout.
- N2. Output from git is read with a byte cap while streaming; no response
  to the browser exceeds 2 MiB of encoded JSON.
- N3. Git runs without a shell, with `--` before paths, a sanitised
  environment (§2.2), `LC_ALL=C`, no pager, no editor, no prompts.
- N4. Every action is bound to identities checked immediately before it runs:
  the registered worktree path, its symbolic HEAD, the branch OID, and the
  snapshot tree OID the user reviewed.
- N5. Outward actions require cookie, same origin, and a confirmation that
  names remote URL, ref and OID.
- N6. Minimum git 2.38. Below that, the checkbox is hidden with the reason.
- N7. Mobile (≤820px): review is a full-screen dialog.

### 1.3 Out of scope for v1, with the reason

| Item | Why not now |
|---|---|
| Merge into a checkout | D6 |
| Port of a worktree pane | D12 |
| Send hunk feedback from review | Selection-to-composer plumbing; copy-paste covers it |
| Branches palette | The CLI in F9 covers recovery; a UI list is v1.1 |
| Submodules, sparse checkout | Refused at probe with the reason; independently dirty nested repos need their own design |
| LFS | Allowed only when `git lfs` is installed; refused otherwise |
| Copying gitignored setup files into a new worktree | Opt-in per-repo copy list in v1.1; a setup *script* is a separate code-execution decision |
| Scheduler and rigs creating worktree panes | Same `create` argument later |

## 2. Architecture

```
browser                     hub.py                              sessions.Manager / Pane        worktrees.py (only git caller)
New dialog ──probe──────▶ GET  /api/worktree/probe ─────────────────────────────────────────▶ probe()
           ──start──────▶ POST /api/session/new {worktree:true} ─▶ create() ─▶ registry intent ▶ create()
Pane head  ◀─SSE "worktree"── emit("worktree", summary) ◀── on turn_end ◀───────────────────── summary()
Review     ──open───────▶ POST /api/session/worktree/snapshot ─▶ hold queue, refuse if busy ─▶ snapshot() → tree OID
           ──commit─────▶ POST /api/session/worktree/commit {tree} ─▶ verify identities ─────▶ commit_tree()
           ──publish────▶ POST /api/session/worktree/publish {oid, remote_url} ─────────────▶ push(), gh pr create
           ──discard────▶ POST /api/session/worktree/discard {tree, confirm} ───────────────▶ recovery ref + move to trash
```

### 2.1 The registry (D13)

`STATE/worktrees/registry/<wt-id>.json`, one file per worktree, written
atomically (tmp + `os.replace` + fsync of file and directory):

```json
{
  "v": 1, "id": "wt-7c1e9a", "owner_pane": "11d02ed1a81d",
  "phase": "intent | active | trashed | purged | missing | tampered",
  "path": "/home/cvande/.local/share/corral-light/worktrees/aios-3f2a1c/fix-login",
  "subdir": "scheduler",
  "branch": "refs/heads/corral/fix-login",
  "admin_name": "fix-login",
  "repo_top": "/home/cvande/aios",
  "common_dir": "/home/cvande/aios/.git",
  "common_dir_id": "<dev:inode of common_dir>",
  "base_ref": "refs/heads/main", "base_sha": "3f2a1c9…",
  "created": "2026-10-02T14:10:00Z",
  "last_commit": null, "published": null, "recovery_refs": [],
  "ops": [ {"op": "commit", "state": "intent|done|unknown", "expect_old": "…", "new": "…", "at": "…"} ]
}
```

- Written with `phase: intent` before `git worktree add`. A crash leaves a
  findable intent; restart checks whether git registered the path and
  completes or marks it `missing`.
- Every mutating op appends an `ops` entry in `intent` before running git and
  sets `done` after verifying the postcondition. On restart an `intent` op is
  resolved by **checking the postcondition** (the ref value, the remote ref,
  the PR list), never by assuming rollback, and shown as "outcome checked
  after restart" or "outcome unknown".
- The pane's `meta.json` stores only `worktree_id`; `META_KEYS` gains that one
  key and `from_meta` reads it.

### 2.2 `worktrees.py` — the only module that runs git

Stdlib only.

**`git(args, cwd, timeout=20, check=True, max_out=8 MiB, env_extra=None)`**
- `subprocess.Popen(["git", *args], start_new_session=True)`; stdout and
  stderr read by threads into byte-capped buffers; on timeout `killpg`
  (TERM, 2 s, KILL). Returns `(rc, out, err, truncated)`; `check=True` raises
  `GitError(cmd, rc, err[:400])` on non-zero.
- Timeouts by operation: probe, status and summary 20 s; snapshot 60 s; commit and push 120 s; `worktree add` 300 s.
- Environment: start from `os.environ`, **remove** `GIT_DIR`,
  `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_OBJECT_DIRECTORY`,
  `GIT_ALTERNATE_OBJECT_DIRECTORIES`, `GIT_NAMESPACE`, `GIT_CEILING_DIRECTORIES`,
  `GIT_COMMON_DIR`, `GIT_CONFIG_PARAMETERS`, `GIT_CONFIG_COUNT` and its
  `GIT_CONFIG_KEY_*`/`VALUE_*`, `GIT_EXEC_PATH`, `GIT_EXTERNAL_DIFF`,
  `GIT_DIFF_OPTS`; then set `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`,
  `GIT_EDITOR=true`, `GIT_ASKPASS=` (empty), `SSH_ASKPASS=` (empty),
  `LC_ALL=C`, `GH_PROMPT_DISABLED=1`. Only the temp-index call sets `GIT_INDEX_FILE`, explicitly. `GIT_OPTIONAL_LOCKS=0` is set only on `summary` and `probe` calls, never on a cleanliness check that guards an action.
- A stale `index.lock` is never unlinked by the hub unless it was created by a child the hub spawned and that child's process group is dead.
- `safe.directory` errors are reported as they are, with the fix in words.
  No wildcard exemption is ever installed.

**Public functions.** Each one's docstring states what it never does.

| Function | Does | Never |
|---|---|---|
| `probe(path)` | `{inside, top, subdir, branch, head, detached, unborn, dirty, bare, submodules, sparse, lfs_needed, lfs_ok, git_version, same_fs_as_root, root_tmpfs}` | write anything |
| `plan_slug(title, repo)` | `[a-z0-9-]{1,40}`; unique against `refs/heads/corral/*` **and** existing `<common_dir>/worktrees/<name>` and paths under root; full ref must match `^refs/heads/corral/[a-z0-9-]{1,40}$` after `check-ref-format --branch`; refuses the repo if a branch named exactly `corral` exists (it would block the namespace) | trust user text |
| `create(reg)` | `git worktree add -b corral/<slug> -- <path> <base_sha>`; verify registration via `worktree list --porcelain -z`; set `phase: active` | reuse a path; create outside root; run in a repo with submodules or sparse checkout |
| `verify(reg)` | path registered to this common dir; worktree HEAD is symbolic and equals `reg.branch`; branch OID read; path not a symlink; path under root (resolved) | repair anything |
| `summary(reg)` | `git diff --numstat -z --no-renames <base_sha>` (tracked, working tree vs base; no index writes, no objects) + `git ls-files --others --exclude-standard -z` with sizes | write objects or touch any index |
| `snapshot(reg)` | **copy** the worktree's real index (`git rev-parse --git-path index`, which for a linked worktree is under `<common_dir>/worktrees/<name>/`) to a temp file under the pane dir, then with that temp index `add -A` and `write-tree` → **tree OID**. Starting from the real index keeps files the agent force-added (`add -f`) and intent-to-add entries. Returns the tree OID, HEAD, the base tip, and the ignored-file inventory (`ls-files --others --ignored --exclude-standard --directory -z`) | touch the real index; rebuild on a timer |
| `diff(reg, tree)` | `git diff-tree -r -z --raw -M --no-textconv --no-ext-diff -c diff.renameLimit=1000 <base_sha> <tree>` for identities, then per-file patches with `-p --no-color --src-prefix=a/ --dst-prefix=b/`, each capped; files over 512 KiB or binary listed without hunks | follow symlinks; render textconv |
| `commit_tree(reg, tree, message, expect_head)` | `verify`; if `commit.gpgSign` is true, refuse in under a second with "the hub cannot unlock your signing key; commit in the worktree yourself" (the Copy command gives the line); `commit-tree <tree> -p <expect_head> -F -` (message on stdin); `update-ref <branch> <new> <expect_head>` (compare-and-swap); then, under the repo lock, `read-tree <new>` into the worktree's real index so a second Commit is a no-op | run hooks; amend; reset the work tree; prompt for a key |
| `push(reg, remote_name, remote_url, oid)` | refuse if the worktree is dirty or HEAD's tree differs from the last reviewed tree; re-read remote URL, refuse if it differs from the confirmed one; `git push <remote> <oid>:refs/heads/corral/<slug>` (fully qualified, so a tag of the same name cannot match); verify with `ls-remote` | `--force`, `--force-with-lease`, push a ref other than `corral/*` |
| `open_pr(reg, title, body)` | `gh auth status`; resolve the target repo from the remote URL and show it in the confirmation; `gh pr list --repo R --head …` first, and `gh pr view` if one exists (idempotent); else `gh pr create --repo R --head … --base … --title … --body-file -` | pass text through a shell; let `gh` choose between a fork and its parent |
| `discard(reg, tree)` | the owning pane is paused first (its process group stopped), so nothing writes into a directory being moved; `verify`; re-snapshot under the lock and compare to `tree`; write `refs/corral/recovery/<wt-id>/<ts>` → a commit of `tree` with parent HEAD; inventory ignored files into the registry; `git worktree move <path> <root>/.trash/<wt-id>-<ts>`; `phase: trashed`; branch kept | delete files; delete the branch; prune |
| `restore(reg)` | `git worktree move` back from trash; `phase: active` | overwrite an existing path |
| `purge(reg, confirm)` | requires `confirm == branch short name`; `git worktree remove --force <trash path>`; delete the branch only if its OID equals the recorded one; keep the recovery ref | touch a path outside `<root>/.trash/`; delete a branch not `corral/*`; delete recovery refs |
| `reconcile(registry)` | for each common dir in the registry: `worktree list --porcelain -z`; mark `missing`/`tampered`; list unknown paths under root as orphans | prune; delete; edit |

**Per-repository lock.** Keyed by `common_dir_id` (device and inode, so two
spellings of one repo share a lock), held for every mutating function.

**Why two diff paths.** `summary` runs after every turn and must be cheap and
write-free: `git diff <commit>` compares the working tree to a commit without
an index or object writes, and untracked files are counted from `ls-files`.
`snapshot` runs only when the user opens review; it writes objects once per
open (so the reviewed content has an immutable tree OID that Commit,
Discard and Publish bind to). Objects from abandoned snapshots are loose and
unreferenced; ordinary `git gc` collects them.

**`merge-tree`** is used only by the on-demand "would this merge cleanly"
check in review, with `check=False`: rc 0 clean, rc 1 conflicts (parse the
`-z --name-only` conflicted-file section), anything else an error. It writes
objects and can invoke configured merge drivers; the docstring says so.

### 2.3 Manager and Pane changes

- `Manager.create(..., worktree=False)`: under `_lock`, **reserve** the roster
  slot (so two concurrent creates at 11 panes cannot both pass); outside the lock: lane enabled? → `probe` → refusals (detached,
  unborn, submodules, sparse, LFS without git-lfs, tmpfs root, different fs)
  → registry intent → `worktrees.create` → `Pane(cwd = path/subdir,
  worktree_id=…)` → `start()`. If `start()` fails, the pane is **kept as a dead
  pane that owns the worktree** (D14), with the error as its dead cause; Resume
  retries in the same worktree and Forget offers Discard.
- Every `create` (with or without `worktree`) resolves its cwd and refuses one
  inside the worktree root unless the caller is the owning pane resuming (D14).
- **Claude trust.** Before `start()`, the hub marks the worktree path trusted
  in the pane's private `CLAUDE_CONFIG_DIR` (the hub already seeds that dir).
  Phase 0 confirms the exact key and that the adapter never blocks on a trust
  prompt.
- `Pane.from_meta` reads `worktree_id`; the registry is loaded once by the
  Manager. A pane whose entry is `missing`, `tampered` or `trashed` comes back
  detached with a note, and `resume` refuses with the reason.
- `Manager.restore()`: resolves `intent` entries and ops, then runs
  `reconcile` on a background thread and emits notes.
- `Pane` turn end: enqueue `summary` on a single worker thread per Manager
  (coalescing: at most one pending per pane).
- `Manager.port`: refuses worktree panes (D12).
- `forget` offers Discard; it never deletes anything itself.

**Quiescence for actions (D11).** An action route takes the pane's action
lock, refuses with 409 `busy` if the pane state is `busy`, a tool call is
open, or permissions are pending; sets `p.held = True` so `send` queues
instead of dispatching; runs; clears `held` and drains the queue. Tool
subprocesses the agent started in the background (watchers, dev servers) are
outside the hub's knowledge; review shows "files changed since you opened
review" when a fresh `summary` disagrees with the snapshot, and Commit
refuses then.

### 2.4 Routes (`hub.py`)

All behind cookie and same-origin checks. User errors raise `ValueError`
(400); state conflicts return 409 with a machine-readable `reason`.

| Route | Body / query | Returns |
|---|---|---|
| `GET /api/worktree/probe?cwd=` | | probe dict + `lane_ok` map |
| `POST /api/session/new` | existing + `worktree: true` | as today; snapshot has `worktree` |
| `POST /api/session/worktree/snapshot` | `{pane}` | `{tree, head, files:[{path, old_path, status, add, del, binary, too_big, hunks}], ignored:{count, sample}, summary, merge_check?}` |
| `POST /api/session/worktree/commit` | `{pane, tree, head, message}` | `{ok, commit}`; 409 `busy`, `changed`, `identity` |
| `POST /api/session/worktree/publish` | `{pane, oid, remote, remote_url, pr:{title, body}|null}` | `{ok, pushed, pr_url | compare_url}`; 409 `remote_changed`, `uncommitted`, `non_ff` |
| `POST /api/session/worktree/discard` | `{pane, tree, confirm?}` | `{ok, recovery_ref, trash_path}` |
| `GET /api/worktrees` | | registry entries (no `common_dir`) |

Snapshot is a POST because it writes objects and holds the pane.

### 2.5 Browser

- **New conversation.** A row under the folder field, filled by the probe
  (debounced 300 ms): checkbox "Own branch", "cut from `main @ 3f2a1c9`",
  warnings (dirty main: "your uncommitted changes in ~/aios stay there"), or a
  refusal reason in place of the checkbox. Remembered per repo top in
  `localStorage`. Hidden for lanes not enabled.
- **Header.** `button.pill.wt` after `.meta`: `⎇ fix-login · 4 files +120 −8`.
  `.meta` shows the original repo path; the tooltip has the worktree path.
  `headSignature` gains the pill's fields.
- **Review dialog** `#revdlg`. Opening it calls snapshot. Layout: file list
  (status letter, +/−, filter) and unified diff with line numbers and sticky
  hunk headers; add and delete colours from `.perm pre`. A banner when
  ignored files exist ("12 ignored files are not in this review; Discard
  keeps them in trash"). Footer: Commit, Push & PR, Copy merge command,
  Discard, Refresh. Disabled buttons say why. A 409 refreshes the snapshot and
  shows the reason. Under 820 px it is full-screen with the file list above
  the diff.
- **Rail.** A card when a worktree pane is your-turn and its summary digest
  differs from the last one the user opened. Not counted in `blocked`.
- **Keys.** `r` on a focused worktree pane opens review (not in text fields).
- **SSE.** One line mirrors `worktree` events into `p.worktree.summary`.
- `parseUnified` is a pure function for the Node selftest. Text goes through
  `textContent` only.

### 2.6 Agent realities (Phase 0 decides; nothing here is assumed)

| Lane | Expectation from the panel | Phase 0 check |
|---|---|---|
| Claude | Trust prompt possible for a new path; worktree recognised by git | Pre-trust works; no handshake stall; edits stay in the worktree |
| Codex | Workspace-write sandbox likely blocks `git add`/`commit` (admin dir is outside cwd) | Confirm; hub Commit covers it. Do not widen the sandbox to the common dir (that exposes every branch) |
| Grok | Unknown; tools that test `isdir(".git")` would misdetect a worktree | Edits, `git status`, commit |
| Gemini | Reported to work; unverified | Same |

A lane is enabled only when its row passes. Also checked for every lane: no
tool event's `locations` fall outside the worktree during the matrix run. In
production, such an event raises a one-line warning on the pane (cheap; uses
data the hub already has).

**Edits outside the worktree.** A tool event of kind edit, delete or move
whose `locations` resolve outside the worktree (reads are ignored) cancels the
turn, raises a needs-you card naming the path, and leaves the review digest
unseen. The write has already happened; the point is that the user hears
about it at once, not at review.

## 3. Workstreams

House style per task: failing test, minimal code, focused run, full run, one
commit. Test IDs are in §5.

### WS0 — Phase 0 (no product code)
0.1 Lane matrix (§5.3) on a scratch repo under `~/wt-spike`, worktrees made by
hand, each lane started in one through the existing UI.
0.2 Claude trust key and adapter behaviour.
0.3 `merge-tree --write-tree -z --name-only` fixtures (clean, conflict, rename/delete) on git 2.38 (container or static build) and 2.55.
0.4 `summary` and `snapshot` timings on ~/aios and a 50k-file repo.
0.5 Confirm `git worktree remove` deletes ignored files and `move` keeps registration.
- Exit: `docs/worktree-phase0.md` with results; plan amended where reality differs.

### WS1 — `worktrees.py`
1.1 `git()` wrapper: env sanitising, caps, process-group timeout. T-GIT-*.
1.2 Registry with atomic writes and op journal. T-REG-*.
1.3 `probe`. T-PRB-*.
1.4 `plan_slug`. T-SLG-*.
1.5 `create` + `verify`. T-CRT-*, T-VER-*.
1.6 `summary`. T-SUM-*.
1.7 `snapshot` + `diff`. T-SNP-*, T-DIF-*.
1.8 `commit_tree`. T-CMT-*.
1.9 `push` + `open_pr`. T-PUB-*.
1.10 `discard`, `restore`, `purge`. T-RMV-*.
1.11 `reconcile` and restart resolution of intents. T-REC-*.

### WS2 — lifecycle
2.1 `worktree_id` in meta; registry load. 2.2 `create(worktree=True)` order and failure. 2.3 Quiescence and held queue. 2.4 Summary worker. 2.5 Restore. 2.6 Port refusal, forget → discard. 2.7 Lane gating. T-LIF-*, T-SAFE-*.

### WS3 — routes
3.1 Probe. 3.2 Snapshot. 3.3 Commit, publish, discard with identity and 409 reasons. 3.4 Auth and origin. 3.5 Registry listing. T-RTE-*.

### WS4 — browser
4.1 Dialog row. 4.2 Header pill. 4.3 `parseUnified` and review dialog. 4.4 Actions and disabled reasons. 4.5 Rail card and key. 4.6 Mobile and themes. T-UI-*, T-VIS-*.

### WS5 — CLI, docs, release
5.1 `corral-light worktrees` (list, restore, purge). 5.2 `doctor` reports git version, root filesystem, trash size. 5.3 README section with the "not a sandbox" sentence and the recovery rules. 5.4 Panel synthesis filed.

## 4. Sequence

| Phase | Work | Exit |
|---|---|---|
| P0 | WS0 | phase-0 table; lanes enabled list |
| P1 | WS1 | all core tests green |
| P2 | WS2 + WS3 | API-only feature works end to end with the fake lane |
| P3 | WS4 | Node selftests and visual checks green |
| P4 | Dogfood a week | ≥ 3 parallel panes on one repo twice; PR used for real; no lost work |
| P5 | WS5 | released |

Kill switch: `CORRAL_LIGHT_WORKTREES_ENABLED=0` hides the checkbox and refuses
new worktree panes; existing ones still resume and can still be discarded.

## 5. Test plan

### 5.1 Levels

| Level | File | Runs | How |
|---|---|---|---|
| Core | `test_worktrees.py` | every commit | real git in `tempfile.mkdtemp(prefix="corral-wt-test-")` repos; `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, fixed author env |
| Crash | `test_worktrees_crash.py` | every commit | real hub **subprocess** (`start_hub` pattern), killed with SIGKILL at injected points (`CORRAL_WT_CRASH_AT=<op>:<boundary>`, honoured only when a test env var is set) |
| Lifecycle | `test_worktrees.py::Lifecycle*` | every commit | in-process Manager, fake ACP lane |
| Routes | `test_worktree_routes.py` | every commit | `ThreadingHTTPServer` + minted cookie |
| Browser logic | `selftest_review.mjs` | every commit | `fn()`/`constant()` extraction |
| Visual | `selftest_visual.mjs` | before merge | headless Chromium over CDP; skips loudly without Chromium |
| Version | `test_worktrees.py` under git 2.38 | before release | `CORRAL_TEST_GIT=/path/to/git-2.38` |
| Lane matrix | manual | P0, P4 | §5.3 |
| Regression | existing suites | every commit | unchanged |

All three new Python files are collected by `test_corral_light.py`. Every
test points `CORRAL_LIGHT_WORKTREES` and `CORRAL_LIGHT_STATE` at temp dirs.
Timing-sensitive checks use spies on the `git()` wrapper, never wall-clock
sleeps.

**Fake agent verbs** (`testkit/fake_acp_agent.py`): `write <rel> <text>`,
`rm <rel>`, `commit <msg>`, `bg-write <rel> <n> <interval>` (spawns a
detached child that keeps writing after the turn ends), `checkout <branch>`,
`reset-hard <rev>`. All refuse paths containing `..`.

### 5.2 Catalogue

**git wrapper**
- T-GIT-1 non-zero rc raises `GitError` with rc and capped stderr.
- T-GIT-2 a would-prompt push to a local stub remote that demands credentials fails fast (stub askpass that records it was not called); no network.
- T-GIT-3 a hung git (stub that sleeps and spawns a child) is killed with its whole process group at the timeout.
- T-GIT-4 a path beginning `-` is treated as a path.
- T-GIT-5 `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, `GIT_CONFIG_PARAMETERS` set in the hub's env pointing at a decoy repo: no call touches the decoy.
- T-GIT-6 output beyond `max_out` is truncated while streaming; memory stays bounded (a stub emitting 200 MiB).
- T-GIT-7 `merge-tree` rc 1 with conflicts is parsed as conflicts, not an error; rc 128 is an error.

**registry**
- T-REG-1 atomic write: a killed writer leaves the old file or the new one, never a partial.
- T-REG-2 intent written before `worktree add` (spy ordering).
- T-REG-3 unknown future `v` is refused read-only, never rewritten.

**probe**
- T-PRB-1 plain dir; T-PRB-2 repo top; T-PRB-3 subdir gives `subdir`.
- T-PRB-4 detached HEAD refused; T-PRB-5 unborn HEAD refused; T-PRB-6 bare refused.
- T-PRB-7 submodules refused; T-PRB-8 sparse checkout refused; T-PRB-9 LFS attributes without git-lfs refused.
- T-PRB-10 root on tmpfs or another filesystem refused.
- T-PRB-11 probe writes nothing (file list and mtimes of `.git` unchanged).
- T-PRB-12 a cwd that is itself a linked worktree resolves `repo_top` to the main worktree and the base to the linked worktree's branch.

**slug**
- T-SLG-1..5 as v1 (ascii, unicode fallback, `-2` suffix, refused names, length).
- T-SLG-6 a stale `<common_dir>/worktrees/<slug>` admin dir forces a new name.

**create and verify**
- T-CRT-1 worktree on `corral/<slug>` at `base_sha`, under root.
- T-CRT-2 main checkout files, index, HEAD unchanged (byte compare).
- T-CRT-3 dirty main: worktree starts from HEAD; dirt stays in main.
- T-CRT-4 two repos spelled differently (symlinked path) share one lock.
- T-CRT-5 twelve concurrent creates on one repo give twelve branches.
- T-CRT-6 root is a symlink → refused. T-CRT-7 target path exists → refused.
- T-VER-1 agent checks out another branch in the worktree → every action 409 `identity`.
- T-VER-2 branch moved by `reset --hard` outside the hub → commit refuses (`expect_head` mismatch).
- T-VER-3 worktree path replaced by a symlink → refused.
- T-VER-4 admin dir points elsewhere (tampered `.git` file) → `tampered`.

**summary**
- T-SUM-1 zero changes; T-SUM-2 modify/add/delete counted; T-SUM-3 untracked counted.
- T-SUM-4 no objects written: `.git/objects` file count unchanged over 20 calls with a 10 MiB untracked file.
- T-SUM-5 the worktree's real index is byte-identical before and after.
- T-SUM-6 an unstaged edit with unchanged index mtime changes the summary.

**snapshot and diff**
- T-SNP-1 the tree OID equals what `git add -A && git write-tree` would give in a scratch copy.
- T-SNP-2 the same content gives the same tree OID; one changed byte gives a different one.
- T-SNP-3 ignored files are inventoried and excluded from the tree.
- T-SNP-4 snapshot refused 409 `busy` while the fake agent is mid-turn.
- T-DIF-1 parseable unified output; worktree-relative paths.
- T-DIF-2 per-file and total caps; JSON response ≤ 2 MiB.
- T-DIF-3 binary and > 512 KiB files named without hunks.
- T-DIF-4 a symlink pointing outside the worktree shows as a symlink change, never followed.
- T-DIF-5 user config `diff.external`, `color.ui=always`, a textconv driver: none affect output.
- T-DIF-6 names with spaces, quotes, newlines and non-UTF-8 bytes round-trip via `-z` identities.
- T-DIF-7 a rename is shown as a rename; with > 1000 files, rename detection is skipped, not slow.

**commit**
- T-CMT-1 the new commit's tree equals the reviewed tree exactly.
- T-CMT-2 a file changed after snapshot → 409 `changed`; nothing committed.
- T-CMT-3 branch moved after snapshot → 409 `identity`; nothing committed.
- T-CMT-4 repo has a pre-commit hook that would fail → hub commit still succeeds and the hook is not run (documented behaviour).
- T-CMT-5 after commit, `git status` in the worktree is clean for exactly the reviewed paths; an untracked file created after snapshot is still there, untracked.
- T-CMT-6 empty message refused; nothing-to-commit is a clear no-op.
- T-CMT-7 a file the agent force-added (`add -f`, otherwise ignored) is in the reviewed tree and in the commit; the real index equals the new HEAD afterwards; a second Commit is a no-op.
- T-CMT-8 `commit.gpgSign=true` fails in under a second, leaves no `index.lock`, and names the reason.

**publish**
- T-PUB-1 push to a local bare remote creates `corral/<slug>` at the exact OID.
- T-PUB-2 remote URL changed after confirmation → 409 `remote_changed`.
- T-PUB-3 uncommitted changes → 409 `uncommitted`.
- T-PUB-4 remote branch diverged → 409 `non_ff`; never forced.
- T-PUB-5 no `gh` → compare URL for GitHub remotes. T-PUB-6 `gh` signed out → reason names `gh auth login`.
- T-PUB-7 stub `gh` success → `pr_url` stored; a second call finds the existing PR and does not create another.
- T-PUB-8 a title containing `$(touch x)` reaches `gh` literally.
- T-PUB-9 a tag named `corral/<slug>` on the remote does not receive the push.
- T-PUB-10 a fork remote: `gh` is called with `--repo` set to the repo shown in the confirmation; `GH_PROMPT_DISABLED` is set.

**discard, restore, purge**
- T-RMV-1 discard writes `refs/corral/recovery/<id>/<ts>` whose tree equals the snapshot tree (untracked files included).
- T-RMV-2 discard moves the directory into `.trash/`; ignored files (`.env`, a nested repo, a 50 MiB data file) are still present there.
- T-RMV-3 the branch still exists after discard.
- T-RMV-4 restore brings the worktree back to its path and the pane can resume.
- T-RMV-5 purge without the typed branch name is refused.
- T-RMV-6 purge never deletes a branch whose OID differs from the recorded one, nor one outside `corral/`.
- T-RMV-7 purge never touches a path outside `<root>/.trash/` (tampered registry).
- T-RMV-8 recovery refs survive purge.
- T-RMV-9 AST test: no `shutil.rmtree`, `os.remove`, `os.unlink` or `Path.unlink` in `worktrees.py` outside the temp-index helper, which may only unlink its own temp file.
- T-RMV-10 a published branch with upstream but not integrated into base is never treated as merged.
- T-RMV-11 discard with a live fake agent first pauses it; a `bg-write` child started by the agent is gone before the move.

**reconcile and restart**
- T-REC-1 healthy registry → no notes.
- T-REC-2 worktree dir deleted by hand → `missing`, reported once, no prune.
- T-REC-3 branch deleted by hand → reported.
- T-REC-4 an unknown dir under root → orphan.
- T-REC-5 the user's own worktrees and stale registrations elsewhere → untouched and unlisted (spy proves no `prune` call).
- T-REC-6 repo moved or deleted → entries `missing`; hub keeps running.

**crash (real hub subprocess, SIGKILL at each boundary)**
- T-CRS-1 during create, after intent / after `worktree add` / after pane save: restart yields either a live pane on the branch or a listed orphan; never an unlisted worktree.
- T-CRS-2 during commit, before / after `update-ref`: restart reports the true outcome by reading the ref; no duplicate commit on retry.
- T-CRS-3 during push, after push / before record: restart reads the remote ref and records it.
- T-CRS-4 during PR creation: restart finds the PR via `gh pr list` and does not create another.
- T-CRS-5 during discard, after recovery ref / after move: restart shows `trashed` or `active` truthfully; nothing lost.

**lifecycle**
- T-LIF-1 `worktree_id` round-trips through meta. T-LIF-2 metas without it load as before.
- T-LIF-3 the agent's cwd is the worktree plus the chosen subdir (fake agent reports cwd).
- T-LIF-4 roster full → refused before any registry entry or branch.
- T-LIF-5 agent start fails → pane gone, worktree listed, error names it.
- T-LIF-6 `Manager._lock` never held during a git call (`NestCheckLock`-style instrumentation).
- T-LIF-7 turn end → one `worktree` event; summary failure → a note, pane unaffected.
- T-LIF-8 no git call on the observe tick (spy over several ticks driven directly, no sleeps).
- T-LIF-9 restart: pane detached with its worktree; resume runs there.
- T-LIF-10 missing worktree at restart → resume refused with the reason.
- T-LIF-11 close keeps; reopen restores; forget offers discard and deletes nothing.
- T-LIF-12 port of a worktree pane refused; port of other panes unchanged.
- T-LIF-13 `host:` and Ollama refused; a lane not yet enabled refused.
- T-LIF-14 Claude pane's config dir marks the worktree path trusted before start.
- T-LIF-15 two concurrent creates at eleven live panes: one succeeds, one is refused before any git call.
- T-LIF-16 a failed start leaves a dead pane owning the worktree; Resume retries there.
- T-LIF-17 a plain create (no `worktree`) whose cwd is inside the worktree root → 400.
- T-LIF-18 the D15 preamble is the first thing the agent receives.
- T-LIF-19 an edit-kind tool event with a location outside the worktree cancels the turn and raises a needs-you card; a read-kind one does nothing.

**safety under concurrency**
- T-SAFE-1 fake agent writes between snapshot and commit → 409 `changed`.
- T-SAFE-2 `bg-write` child keeps writing after the turn ends → review shows "files changed since you opened review"; Commit refused.
- T-SAFE-3 a send arriving during an action is queued and delivered after it.
- T-SAFE-4 two actions on one pane at once → the second gets 409 `busy`.
- T-SAFE-5 the user edits the main checkout during every action → the main checkout is never read or written by any action (spy on cwd of every git call).

**routes**
- T-RTE-1..3 probe (repo, non-dir 400, no writes).
- T-RTE-4..6 snapshot (shape, size cap, non-worktree pane 400).
- T-RTE-7..12 commit, publish, discard: success emits a `worktree` event; each 409 reason is machine-readable.
- T-RTE-13 every new route 401 without the cookie; every POST 403 with a foreign Origin.
- T-RTE-14 `/api/worktrees` never returns `common_dir` or absolute paths outside root and repo.

**browser logic (Node)**
- T-UI-1 probe row hidden for non-repos and disabled lanes; refusal reasons shown in place of the checkbox.
- T-UI-2 checkbox remembered per repo top; T-UI-3 `worktree` sent only when checked and visible.
- T-UI-4 pill text; T-UI-5 `headSignature` changes exactly when pill fields change.
- T-UI-6 `.meta` shows the repo path.
- T-UI-7 `parseUnified` fixtures: add, delete, rename, mode change, binary, no newline at end, CRLF, a 3-hunk file with line numbers.
- T-UI-8 source contract: no `innerHTML` in review code.
- T-UI-9 commit posts the tree and head it rendered; publish posts the OID and the remote URL it showed.
- T-UI-10 409 refreshes the snapshot and shows the reason.
- T-UI-11 ignored-files banner rendered from the snapshot.
- T-UI-12 rail card logic and that it does not count toward `blocked`.
- T-UI-13 `r` key outside text fields only.
- T-UI-14 files over the size cap render a placeholder, never a DOM node per line.

**visual (before merge)**
- T-VIS-1 pill at 1600, 1100, 420 px; T-VIS-2 review at 1600 px side by side; T-VIS-3 review at 420 px full-screen.
- T-VIS-4 a 5,000-line diff: the dialog opens and scrolls; render time is recorded in the report, not asserted.
- T-VIS-5 ink, parchment, nocturne: add/del text contrast ≥ 4.5:1, computed from the CSS variables.

**isolation**
- T-ISO-1 the suite runs with `HOME` pointed at a temp dir; any write under the real state dir or any `git` call with a cwd outside the temp roots fails the run (spy on the wrapper plus a final listing of the real root).

### 5.3 Lane matrix (P0 and P4)

Scratch repo with a small Python project and one failing test.

| Step | Claude | Codex | Grok | Gemini |
|---|---|---|---|---|
| Starts with Own branch; no trust stall | | | | |
| Fixes the test; every tool `location` inside the worktree | | | | |
| Runs the test suite in the worktree | | | | |
| Agent `git commit` (record; Codex expected to fail) | | | | |
| Review shows the diff; hub Commit | | | | |
| Push & PR to a throwaway repo | | | | |
| Pause, restart hub, resume on the branch | | | | |
| Discard, restore from trash, purge | | | | |

Plus: three lanes, three tasks, one repo, at once; all three published.

### 5.4 Ship gate

1. All automated tests green, including the existing suites and T-CRS-*.
2. Lane matrix: only passing lanes enabled.
3. T-ISO-1, T-RMV-*, T-SAFE-* green — release blockers.
4. One week of dogfood with no lost work.
5. Panel synthesis filed; every accepted finding fixed or deferred with a reason.

## 6. Risks

| Risk | Mitigation |
|---|---|
| An agent writes outside its worktree | Not a sandbox, said plainly; lane gating; out-of-worktree `locations` warning |
| Agent or background process writes during an action | D11 quiescence; snapshot tree binding; "changed since review" refusal |
| Discard loses ignored files | D9: move to trash, recovery ref, typed purge |
| Crash mid-operation | Registry intents and op journal resolved by checking postconditions |
| Codex cannot commit | Hub Commit (D5) |
| Hooks skipped on hub commits surprise a team repo | Stated in the Commit button's tooltip and README; pre-push hooks still run on push |
| Disk growth | `doctor` shows root and trash size; purge is explicit |
| Large repos make summary slow | Write-free summary on a coalescing worker; Phase 0 timings |
| Claude trust prompt stalls start | Pre-trust in the pane's config dir; Phase 0 |
| An agent force-pushes or rewrites shared refs | D15 preamble; the rail still asks for Claude's shell commands; edit events outside the worktree cancel the turn |
| Commit signing configured | Hub Commit refuses fast and offers the command to run by hand |

## 7. Open questions for round 2

1. Location: Gemini argues for a sibling of the repo (`<repo>/../.<repo>.corral/<slug>`) so tools, editors and relative paths behave; Sol for the state dir. v2 keeps the state dir and requires the same filesystem. Which, and why?
2. Is hub commit by plumbing (no hooks) right, or should Commit run hooks with an explicit "skip hooks" box (Gemini's v1 suggestion)?
3. Is snapshot-on-open (objects written once per review) acceptable, versus a write-free diff that cannot give an immutable tree to bind actions to?
4. Is "Copy merge command" enough for v1, or is an in-worktree "sync with base" (ask the agent to merge base into its branch) needed to make PRs mergeable?

## 8. What changed from v1, and why

| v1 | v2 | Raised by |
|---|---|---|
| Merge into the main checkout | Cut; Push & PR and Copy merge command | Sol, Gemini |
| `git worktree prune` for hub paths | Never automatic; prune is repository-wide | Sol, Gemini |
| `remove` deletes the worktree | Discard = recovery ref + move to trash; typed purge | Sol, Gemini |
| Digest of HEAD + temp-index tree on every summary | Write-free summary; snapshot tree only on review open; actions bind to tree OID | Sol, Gemini |
| `git commit` with hooks | `commit-tree` + CAS `update-ref`; reviewed equals committed | Sol (hooks change commits), Gemini (hooks fail without deps) |
| Actions allowed any time | Refused while busy; send queue held | Sol, Gemini |
| Pane meta held the record | Durable registry with intents and an op journal | Sol |
| Port hands off the worktree | Refused in v1 | Sol, Gemini |
| Env inherited | Routing variables removed; no `safe.directory` wildcard | Sol (Gemini suggested a wildcard; rejected: it disables a safety check for every repo) |
| `merge-tree` with `check=True` | rc 1 = conflicts | Gemini |
| Subdirectory cwd lost | Preserved | Sol |
| Detached HEAD allowed | Refused in v1 | Sol, Gemini |
| Push of a branch name | Push of the reviewed OID; remote URL bound | Sol |
| `--no-ext-diff` only | Plus `--no-textconv`, `-z` identities, rename limit | Sol, Gemini |
| All four lanes on day one | Per-lane enablement after Phase 0 | Sol |
| Claude trust not considered | Pre-trust in the pane's config dir | Gemini |
| Palette Branches, hunk feedback, live ahead/behind | Deferred; CLI for recovery; ahead/behind on review open | Gemini |
| T-DIF-5, T-CRT-9, T-REC-5, T-SUM-8, T-LIF-11, T-LIF-16, T-VIS-4, T-ISO-1, T-GIT-2 | Rewritten as testable (spies, real crash harness, stub askpass, recorded not asserted timings) | Sol, Gemini, Grok |
| Snapshot from `read-tree HEAD` | Copy of the worktree's real index, so force-added files are reviewed | Grok |
| Failed start removed the pane | Dead pane keeps ownership; plain creates into a worktree refused | Grok |
| Roster checked, not reserved | Slot reserved under the lock before any git | Grok |
| One 20 s timeout | Per-operation timeouts; process-group kill; no unlinking of locks the hub did not create | Grok, Gemini |
| `push <oid>:corral/<slug>`; `gh` defaults | Fully qualified refspec; `GH_PROMPT_DISABLED`; explicit `--repo`; idempotent PR | Grok |
| Signing not considered | Fast refusal when `commit.gpgSign` is set | Grok |
| Discard while the agent runs | Owner paused first, then re-snapshot under the lock | Grok |
| Out-of-worktree writes only warned | Edit events outside the worktree cancel the turn and raise a card; D15 preamble | Grok |
| Branch named `corral` not considered | Probe refuses the repo with the reason | Grok |
