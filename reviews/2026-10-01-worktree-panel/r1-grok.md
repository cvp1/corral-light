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
