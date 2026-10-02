(a) **BUILD.** (b) **RESHAPE.** (c) **RESHAPE.** (d) **BUILD.** (e) **RESHAPE.**

Round-1 (a) and (b) stand for v1. I withdraw my round-1 fix “after `update-ref`, `read-tree` the new commit into the real index.” v2 took that sentence literally, and it is not safe as written. Graveyard-before-delete, copying the real index, no prune, no in-checkout merge, the dead owner pane, and the fully qualified refspec still stand.

## 1. Cross-exam

**Sol.** Most agree: `git worktree prune` cannot be scoped to hub paths, and `worktree remove` deletes ignored files that `status` treats as clean. v2’s move-to-trash is the right response. **Wrong:** “if the base is checked out nowhere, `merge-tree` + `commit-tree` + compare-and-swap `update-ref` touches no checkout, once you have listed worktree registrations.” `merge-tree --write-tree` writes objects and can run a configured merge driver, which is an arbitrary program (**knowledge**). `update-ref` is not `branch -f`: it will move a branch another worktree has checked out, and it does not update that checkout’s files, so the next commit there looks like a mass revert (**knowledge**). `worktree list` is also racy with a checkout that starts after the check. Do not lift that sentence into v1.1 unchanged.

**Gemini.** Most agree: do not merge into a checkout an editor has open. A clean `status` does not see unsaved buffers. **Wrong:** `-c safe.directory=*` as the ownership fix. That turns off the dubious-ownership check for every repo this process touches. v2 was right to reject it. The same review’s discard fix is also wrong: `git write-tree` records the index only, so it would not have saved the untracked or ignored files Gemini was trying to protect (**knowledge**).

## 2. Re-verdict

See the line at the top. Approach is sound: opt-in worktree, no in-checkout merge, no prune, no port, actions bound to a tree OID. `worktrees.py` still has a bad commit epilogue and a bad discard synchronization story. Lifecycle’s held queue and “pause” are incomplete, and the tests contradict D14. The review UI is fit to build. The catalogue is much more honest than v1 and still has one test that would force the wrong failure behavior.

## 3. Remaining lost-work paths

New v2 holes, ranked first.

1. **`read-tree` after compare-and-swap (new).** Trigger: `commit-tree` and `update-ref` succeed, then `read-tree` is killed, hits `index.lock`, or runs against the main index because `cwd` or `GIT_INDEX_FILE` is wrong. The branch has moved; the index still has the old tree, so a later `git commit` in that checkout reverts the review. `read-tree` without `-u` still **drops** index entries that are not in the new tree (**knowledge**). **Fix:** re-snapshot under the repo lock and require the same tree OID immediately before `commit-tree`. Point `read-tree` at `<common>/worktrees/<admin>/index` only. Never `-u`. Main index bytes are an assertion, not a cwd spy. Refuse if `index.lock` exists.

2. **Crash resolver trusts the ref alone (new).** Trigger: kill after `update-ref`, before `read-tree`, or after `commit-tree` before the new OID is stored. Restart sees the ref moved, marks the op done, and leaves the stale index. A retry mints a second commit because `commit-tree` includes the committer timestamp. **Fix:** persist the new OID before `update-ref`. Postcondition is `ref == OID` **and** index tree `== OID`. Same tree on retry is a no-op. `unknown` does not auto-retry.

3. **Discard stops the owner, then the queue wakes it (new).** Trigger: discard SIGSTOPs the process group and `git worktree move`s the directory. On Linux the cwd inode follows the rename (**knowledge**). The route then clears `held` and drains a send that arrived during the move, so the agent continues inside `.trash`. A grandchild that called `setsid` was never stopped. A SIGSTOP **while the agent holds `index.lock`** makes the move’s own git sit until timeout (**knowledge**). **Fix:** `finally` clears `held`, but discard and any failed action **drop** the queue. Cancel the turn, TERM then KILL the group, wait until `index.lock` is gone, then move. No turns into a trashed pane. Do not SIGSTOP a process inside git.

4. **Claude pre-trust writes more than trust (new).** **Knowledge:** Claude Code can prompt on an untrusted directory, and a private `CLAUDE_CONFIG_DIR` does not inherit trust for a new path; with no TTY that prompt can burn the handshake. **Guess:** the exact key, and that a sloppy write can set a permission bypass (`bypassPermissions` or an accept-all default mode) or trust `repo_top` instead of the worktree. **Fix:** Phase 0 must show a JSON diff that adds directory trust for that worktree path only. A shell command must still hit the approval rail. If the key is not proven, do not pre-trust.

5. **“Changed” is a numstat, not a tree (new).** Trigger: `summary`’s `git diff <base_sha> --numstat` matches on counts while bytes differ, or the worktree was put back to match the base while the real index still holds a staged blob. Commit is allowed; `add -A` on the index copy keeps the worktree bytes; `read-tree` then throws away the staged blob. **Fix:** the pill may use numstat. Commit, discard, and publish may compare only a fresh snapshot tree OID.

6. **Out-of-worktree cancel watches the wrong paths (new).** **Guess,** lane-specific: a git write under `<repo>/.git/worktrees/<admin>/` is outside the worktree directory. If the lane reports that path, every agent `git add` cancels the turn. A shell redirect into the main checkout often reports only the worktree cwd, so the card never appears and the main file is already overwritten. **Fix:** allowlist that admin directory. Keep the card for edit events. Do not treat it as covering shell.

Also fix the spec bug, not a sixth path: **T-LIF-5** (“pane gone”) contradicts **D14** and **T-LIF-16** (dead pane keeps the worktree). A green T-LIF-5 deletes the owner.

## 4. Git fact check

1. **`git diff <commit>` is not write-free by form.** It compares the worktree to that commit, and by default it may refresh the index. Write-freedom requires `GIT_OPTIONAL_LOCKS=0`, which v2 does set on summary. It still does not show a staged blob the worktree file no longer matches (**knowledge**).

2. **`-c diff.renameLimit=1000` is illegal as a `diff-tree` argument.** `-c` belongs on `git`, before the subcommand (**knowledge**). As written, `diff-tree` rejects it or treats it as a revision. T-DIF-7 would be testing a different command than production if the test inserts `-c` correctly and the plan does not.

3. **`update-ref <branch> <new> <old>` is atomic on the ref value and does not touch the index or worktree.** A mismatch `die()`s, so the exit status is **128**, not a distinct “1 means identity” code; lock failure is also fatal (**knowledge**). Do not branch on `rc == 1`.

4. **`worktree remove` deletes ignored files** whenever it proceeds, including without `--force` if only ignored files are extra, because the dirty check uses status and status hides them. **`worktree move` keeps them** (directory rename), updates registration to the new path, and fails with no copy fallback when the worktree is locked, is the main worktree, has a populated submodule, or crosses filesystems (**knowledge**). WS0.5 is right. `mkdir` of `<root>/.trash` is unspecified and move will not create missing parents.

5. **`merge-tree --write-tree` exit 1 means conflicts, and stdout still starts with a tree OID** of a conflicted tree. That OID is not a result. With `-z`, the OID is NUL-terminated and the file list follows; the 2.38 and 2.55 record layouts are not interchangeable (**knowledge**). Freeze the bytes in Phase 0. rc 1 must not be `check=True`.

6. **`commit-tree` does not read `commit.gpgSign`, does not sign, and does not run hooks.** The fast refusal is correct policy, not because plumbing would open pinentry (**knowledge**). `commit-tree -F -` reads the message from stdin, and the `git()` wrapper as specified has no stdin pipe. Inherited stdin can block or swallow the hub’s stdin. Pipe the message and close it.

## 5. §7

**Q1. State dir.** Keep `$CORRAL_LIGHT_WORKTREES`, default under the state dir, same filesystem, never tmpfs. Gemini’s cross-device reason is wrong for `worktree add` (**knowledge**: the checkout is written through the gitdir path; it does not need to be on the repo’s filesystem). It is right for v2’s `worktree move`. A sibling `.<repo>.corral/` makes `../<repo>` a short walk into the checkout this feature does not sandbox. When the state dir is a different filesystem, refuse and name an override on the repo’s volume. Warn in the dialog that `../` will not find the project.

**Q2. Plumbing.** Keep `commit-tree` plus compare-and-swap. A “skip hooks” checkbox is a second commit path, and a hook that runs can change the tree after review or fail because the worktree has no `node_modules`. Say on the button: unsigned, hooks not run, pre-push still runs on publish. Someone who needs hooks or a signature commits in the worktree. `commit.gpgSign` stays a fast refusal.

**Q3. Snapshot on open.** Yes. Numstat cannot name the bytes Commit must reproduce. Write objects once per open; `git gc` can collect abandoned ones. Cap it: an untracked file over a fixed size (512 KiB is already the display cap) is inventoried like an ignored file and not passed to `add`. Otherwise one review of a dataset fills `.git/objects`.

**Q4. Copy merge command is enough.** Do not ask the agent to merge. That runs hooks and can destroy uncommitted work, which D15 exists to avoid. The on-demand `merge-tree` check tells him whether the PR is clean. A later sync, if ever, is plumbing onto the agent branch when that branch is checked out only in this worktree, not a prompt.

## 6. Ship gate

§5.4 is not sufficient. Add these three blockers:

- Main `HEAD`, **index file bytes**, and untracked set unchanged across snapshot, commit, discard, and publish. A cwd spy misses a redirected `GIT_INDEX_FILE`.
- T-CRS-2 includes the index: a kill between `update-ref` and `read-tree` must not look successful, and retry must not create a second commit.
- Phase 0 attaches the Claude config diff and shows a shell command still asking for approval before that lane can be enabled.

Rewrite T-LIF-5 to match D14 before “all green” counts. Do not drop the dogfood week.

## 7. Bottom line

Build v2’s shape, and do not implement Commit or Discard until `read-tree` is pinned to that worktree’s index and replayed after a crash, and Discard kills writers and drops the held queue before the directory moves.