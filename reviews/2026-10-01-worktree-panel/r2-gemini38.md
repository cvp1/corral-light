### 1. Cross-Examine

#### Sol (gpt-6.1-sol)
- **Most agree with:** Cutting main-checkout merge from v1 and requiring an immutable, OID-bound operation journal resolved by postcondition verification rather than rollback.
- **Wrong claim:** Sol insisted on the state-directory default (`~/.local/share/...`) claiming location provides the same isolation. *(Platform knowledge)*: Relocating outside the repository root breaks language server boundary detection (Pyright/gopls looking for root markers), monorepo workspace packages (pnpm/cargo paths), `.env` resolution, and tooling like Docker volume mounts (`-v $(pwd)`).
- **Verdict changes:** In Round 1 I rated Overall Approach as RESHAPE due to active checkout `--no-ff` merges. With D6 cutting main-checkout merges and D5 moving to immutable plumbing commits, my Approach verdict changes to **BUILD**.

#### Grok (grok-4.7)
- **Most agree with:** Copying `git rev-parse --git-path index` to capture force-added (`add -f`) and intent files, plus using `commit-tree` + compare-and-swap `update-ref`.
- **Wrong claim:** Grok recommended: *"After the ref moves, `git read-tree HEAD` into the real index under the lock."* *(Platform knowledge)*: Bare `git read-tree <new>` into the worktree's real index overwrites the index without cache-tree validation or stat refresh. It does not update stat dirtying (`mtime`/`ctime`/`ino`), causing every tracked file in the worktree to appear racy-modified on the very next `git status`. More critically, if an agent process wrote a file between snapshot and commit, `read-tree` un-stages or desynchronizes that file without a merge pass.
- **Verdict changes:** None.

---

### 2. Re-Verdict on v2

- **(a) Overall approach:** **BUILD** — Safe boundaries, opt-in worktrees, zero mutations in the user's primary checkout, and strict OID binding.
- **(b) `worktrees.py`:** **RESHAPE** — Plagued by `git worktree move` failing on untracked files without `--force`, and raw `read-tree` index corruption.
- **(c) Lifecycle:** **BUILD** — Pre-registered intent, dead-pane tombstone ownership, held send queues, and postcondition reconciliation are solid.
- **(d) Review UI:** **BUILD** — Clean diff viewing with byte caps, 409 re-snapshot flow, and actionable disabled states.
- **(e) Test plan:** **BUILD** — Well-stratified crash testing, concrete race spies, and rigorous failure-mode catalogue.

---

### 3. Remaining Lost-Work Paths in v2

1. **`git worktree move` without `--force` fails on dirty discard**
   - *Trigger:* User clicks "Discard" on a worktree containing untracked or uncommitted files.
   - *What happens:* `git worktree move <path> <dest>` *(Platform knowledge)* fails with `fatal: '<path>' contains modified or untracked files, use --force to move it`. The discard crashes mid-operation, leaving the worktree in an ambiguous half-discarded state in the registry while files remain at the original path.
   - *Fix:* Pass `--force` to `git worktree move`: `git worktree move --force <path> <trash_path>`.

2. **`read-tree <new>` desynchronizes the real index and clobbers post-snapshot agent writes**
   - *Trigger:* Hub executes `commit_tree()`. It commits `<tree>` and runs `git read-tree <new>` directly into the real index.
   - *What happens:* `read-tree` replaces the index wholesale. Any file written or staged by an agent tool or background watcher between `snapshot()` and `commit_tree()` is deleted from the index without warning. Furthermore, because `read-tree` does not run stat updates, all existing worktree files show as unstaged modifications until an external `git status` re-hashes them.
   - *Fix:* Replace `read-tree <new>` with `git reset --mixed <new>` or run `git update-index --refresh` immediately after `read-tree` under the repo lock, explicitly aborting if unmerged entries exist.

3. **Pre-trusting Claude path bypasses user confirmation on unsandboxed filesystem access**
   - *Trigger:* Hub seeds `CLAUDE_CONFIG_DIR` marking the worktree path as trusted. Claude runs unsandboxed.
   - *What happens:* While prompt hangs are prevented, Claude Code has full read/write access to the entire machine. If Claude runs `rm -rf /home/USER/aios` or edits `.git/config` by path traversal, no prompt stops it. The D15 preamble and turn-canceling edit watcher only catch events *after* writes have hit disk.
   - *Fix:* In Phase 0, assert whether Claude's permission rails can restrict shell execution cwd via `cwd` constraints, and document that pre-trust grants autonomous disk write permissions.

4. **Held send queue releases while background processes still run**
   - *Trigger:* Review Commit completes; hub unsets `p.held` and flushes queued user messages. Meanwhile, an agent-spawned background process (build tool, test runner) is still mutating files in the worktree.
   - *What happens:* New agent instructions run concurrently with active background writes, leading to partial file overwrites and invalid diff states.
   - *Fix:* Before releasing `p.held`, verify that the worktree process group has no background children active, or kill detached process groups when turns complete.

5. **`git worktree move` across mount points fails silently or corrupts `.git` linkage**
   - *Trigger:* Repo is on an external drive or secondary volume, but `$CORRAL_LIGHT_WORKTREES` is left at default `~/.local/share/...`.
   - *What happens:* Probe checks `same_fs_as_root` against `~/.local/share`, but if the user configures a path on another mount, `git worktree move` *(Platform knowledge)* fails when attempting an atomic directory rename across filesystem boundaries (`EXDEV: Invalid cross-device link`).
   - *Fix:* Hard-abort in `create()` if `os.stat(repo_top).st_dev != os.stat(worktree_root).st_dev`.

6. **Unlinked stale `index.lock` PID collision**
   - *Trigger:* Hub crashes, leaving an `index.lock`. On reboot, a completely unrelated OS process happens to reuse the logged PID.
   - *What happens:* Hub inspects the PID, sees an active process, assumes a writer is still running, and permanently bricks the worktree with 409 `busy`.
   - *Fix:* Store both the PID and the process start time (`/proc/<pid>/stat` btime) in the registry intent record before spawning git, or verify process cmdline matches `git`.

---

### 4. Git Fact Check

1. **`git worktree move` without `--force`:** *(Platform knowledge)* `git worktree move <path> <new>` refuses to move any worktree that has uncommitted or untracked changes, exiting with code 128 unless `--force` is provided. v2 omits `--force` in §2.2 `discard()`.
2. **`git diff --numstat -z --no-renames <base_sha>`:** *(Platform knowledge)* Comparing working tree to a commit without an index does not write objects, but by default `git diff <commit>` compares the *working tree* against `<commit>` using the index as a cache filter. If files are untracked, `diff --numstat` omits them entirely (which v2 correctly offsets via `ls-files --others`).
3. **`git merge-tree -z --name-only` syntax:** *(Platform knowledge)* `git merge-tree --write-tree` does not take `--name-only`. In Git 2.38+, output with `-z` emits `<tree_oid>\0` followed by conflicted file names separated by `\0\0`. Passing invalid flag combinations causes rc 128.
4. **`git update-ref` CAS syntax:** *(Platform knowledge)* `git update-ref <ref> <newvalue> <oldvalue>` operates as an atomic compare-and-swap only if `<oldvalue>` is non-empty. If `<expect_head>` is empty or detached, `update-ref` creates or blind-overwrites the ref.
5. **`git commit-tree` with `commit.gpgSign`:** *(Platform knowledge)* `commit.gpgSign=true` in global git config does *not* automatically sign commits made via `git commit-tree` unless the `-S` flag is explicitly passed to `commit-tree`. Hub commits using raw `commit-tree` would actually succeed without invoking GPG, making the fast refusal check an intentional policy divergence rather than a git requirement.
6. **`git worktree remove` vs ignored files:** *(Platform knowledge)* `git worktree remove --force` unlinks the worktree directory and deletes all files inside it, including untracked and `.gitignore`d files. v2's statement that `git worktree remove` destroys ignored files is factually correct.

---

### 5. §7 Open Questions Answered

**Q1. Worktree Location (`~/.local/share` vs sibling `<repo>/../.<repo>.corral/<slug>`):**
**Recommend sibling directory `<repo>/../.<repo>.corral/<slug>`.** Putting worktrees in `~/.local/share` guarantees breakage for projects with relative tooling, sibling project references, language server monorepo boundaries, Docker mount scripts, and cross-filesystem storage setups. Sibling paths stay on the same filesystem by definition, making atomic `os.replace` and `git worktree move` guaranteed to succeed without `EXDEV`.

**Q2. Plumbing Commit (no hooks) vs Running Hooks with "Skip Hooks" checkbox:**
**Recommend plumbing commit without hooks (v2 choice).** Agent checkouts frequently lack full developer environments (missing virtualenvs, uninstalled `node_modules`, absent binary linters). Running pre-commit hooks will routinely fail and brick the hub Commit button. Hooks should execute when the branch is pushed or merged by the operator in his own environment.

**Q3. Snapshot-on-open (writing objects) vs Write-free diff:**
**Recommend snapshot-on-open (v2 choice).** Writing unreferenced loose objects into `.git/objects` once per review session is completely benign—modern git handles thousands of loose objects effortlessly, and `git gc` prunes them after 14 days. Having an immutable tree OID is strictly necessary to prevent race conditions during Commit, Discard, and Publish.

**Q4. "Copy merge command" vs In-worktree "sync with base":**
**Recommend "Copy merge command" for v1.** Asking the agent to run `git merge base` inside its own worktree risks agent confusion, conflicts inside generated code, and unexpected turn execution. Keep v1 clean: push the branch, open a PR, or let the user run the merge command in their shell.

---

### 6. Ship Gate

§5.4 is **insufficient**. It misses verifying `--force` discard mechanics and cross-device safety.

- **Add:** T-RMV-12: Verify `discard()` successfully moves a dirty worktree containing both untracked and modified files into `.trash/` via `git worktree move --force`.
- **Add:** T-PRB-13: Verify `probe()` refuses creation when the target worktree root resides on a different mount point (`st_dev`) than the repository.
- **Add:** T-CMT-9: Verify that `commit_tree()` followed by `read-tree` / index update does not mark clean files as modified in `git status`.

---

### 7. One-Line Bottom Line for the operator

Plan v2 is architecturally sound and protects your main checkout, but you cannot ship it until you add `--force` to `git worktree move` (otherwise dirty discards crash) and fix the post-commit `read-tree` index refresh.