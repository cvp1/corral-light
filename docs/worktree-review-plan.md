# Isolated branches and review — implementation and test plan

Status: PLAN, v3.2, 2026-10-02. Nothing built. Reviewed in two rounds by a
three-vendor panel (Sol, Gemini, Grok); v3 folds in both rounds. v3.1 settles
D2 (worktree location) against this machine's facts; see §7 and §8.
Craig approved the plan and D1–D15 on 2026-10-02; Phase 0 may start. The record
and synthesis are in `reviews/2026-10-01-worktree-panel/`. §8 lists what changed and why.
v3.2 (2026-10-02) adds §9: the plan checked against dogma-2 (macOS). "This machine"
in §0–§8 means omarchy-laptop (Linux) unless §9 says otherwise.

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

## 0. Decisions (confirmed by Craig, 2026-10-02)

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Opt-in or default | Opt-in per pane, remembered per repository | Many panes are chat or ops work; a surprise branch is worse than no isolation |
| D2 | Where worktrees live | **Decided 2026-10-02.** `$CORRAL_LIGHT_WORKTREES`, default `$CORRAL_LIGHT_STATE/worktrees/<repo>-<hash6>/<slug>` (on this machine `~/.local/share/corral-light/worktrees/…`); `<hash6>` is from the realpath of the common dir. The root holds only repo dirs and `.trash/`; the registry lives beside it, not in it (§2.1). Refused on tmpfs. Trash lives under the same root, so moves never cross filesystems. The dialog says that `../` from the worktree does not reach the project. Review shows the worktree path with a Copy button for opening it in an editor | Sol and Grok: one place to audit, and nothing above the worktree belongs to the project. Gemini's dissent was checked against this machine and does not hold; see §7 |
| D3 | Branch name | `corral/<slug>`, slug from title or first prompt, else pane id; unique against refs **and** `.git/worktrees/<name>` | Namespaced, readable, never collides with a stale admin dir |
| D4 | Base | The branch checked out in the repo. **Refused** for detached or unborn HEAD in v1 | A detached base has no merge or PR target |
| D5 | Who commits | Agents may commit. The hub's Commit writes **exactly the reviewed tree** with plumbing (`commit-tree` + compare-and-swap `update-ref`), then reconciles the worktree's index by the protocol in §2.2. Hub commits are unsigned and skip pre-commit and commit-msg hooks; those hooks never run retroactively. Pre-push hooks still run on Publish. The Commit button says all of this | Reviewed equals committed; a hook cannot change content after review; a missing `node_modules` cannot brick Commit. Anyone needing hooks or a signature commits in the worktree with the copied command |
| D6 | Merge | **Cut from v1.** Review offers Push & PR, and "Copy merge command" for the user's own terminal | Both reviewers: merging into a checkout an editor has open can destroy unsaved work, and no lock spans check and merge |
| D7 | Push and PR | Push the reviewed commit OID to `refs/heads/corral/<slug>` on a remote whose URL is shown and bound into the confirmation. PR via `gh` when signed in, else a compare URL. Never force | Uses the user's credentials; publication cannot be redirected after confirmation |
| D8 | Lanes | Enabled per lane only after that lane passes the Phase 0 matrix. Never `host:` (cwd ignored) or Ollama (no tools) | Successful edits do not prove a lane stays in its worktree |
| D9 | Deletion | Discard = recovery ref + `git worktree move` into `<root>/.trash/`. Purge (real deletion) is a separate typed-confirmation action. Never `rm -rf`, never automatic `git worktree prune` | Ignored files (`.env`, datasets, outputs) have no git history; prune is repository-wide |
| D10 | Display state | Unchanged; review status is a separate badge | `displayState` is pinned by the cross-product parity table |
| D11 | Busy panes | Every review action is refused while the pane's turn is running or a tool call is open; during an action the pane's send queue is held. After Commit or Publish the queue drains; after Discard or any failed action it does **not** — queued messages stay visible as not sent | An agent writing between preview and action is the commonest lost-work path; a queue that drains into a trashed pane restarts a writer |
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
           ──publish────▶ POST /api/session/worktree/publish {oid, push_url} ───────────────▶ push(), gh pr create
           ──discard────▶ POST /api/session/worktree/discard {tree, confirm} ───────────────▶ recovery ref + move to trash
```

### 2.1 The registry (D13)

`STATE/worktree-registry/<wt-id>.json` (beside the worktree root, never
inside it, so `reconcile` cannot mistake it for an orphan and an override of
`$CORRAL_LIGHT_WORKTREES` never moves it), one file per worktree, written
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
- Registry writes are serialised across processes (the hub and the
  `corral-light worktrees` CLI) with an `fcntl` lock on the registry dir.
- Ops carry an `op_id`, the prepared OIDs and their stage. Restart resumes a
  journalled stage; evidence that does not fit leaves `unknown`, which blocks
  further actions on the pane and is never auto-retried.
- The pane's `meta.json` stores only `worktree_id`; `META_KEYS` gains that one
  key and `from_meta` reads it.

### 2.2 `worktrees.py` — the only module that runs git

Stdlib only.

**`git(args, cwd, timeout=20, check=True, max_out=8 MiB, env_extra=None)`**
- `subprocess.Popen(["git", *args], start_new_session=True, stdin=PIPE if input else DEVNULL)`; input (the commit message) is written and the pipe closed; stdout and
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
- A stale `index.lock` is never unlinked by the hub unless it was created by a child the hub spawned, recorded with pid **and** process start token (`corral_core.acp.process_start_token`, so a reused pid cannot match), and that child is gone.
- Exit codes: only `merge-tree` treats rc 1 as a result. `update-ref` compare-and-swap failure is rc 128 like any other fatal; the cause is read from stderr and the ref's current value, never inferred from rc.
- `safe.directory` errors are reported as they are, with the fix in words.
  No wildcard exemption is ever installed.

**Public functions.** Each one's docstring states what it never does.

| Function | Does | Never |
|---|---|---|
| `probe(path)` | `{inside, top, subdir, branch, head, detached, unborn, dirty, bare, submodules, sparse, lfs_needed, lfs_ok, git_version, same_fs_as_root, root_tmpfs}` | write anything |
| `plan_slug(title, repo)` | `[a-z0-9-]{1,40}`; unique against `refs/heads/corral/*` **and** existing `<common_dir>/worktrees/<name>` and paths under root; full ref must match `^refs/heads/corral/[a-z0-9-]{1,40}$` after `check-ref-format --branch`; refuses the repo if a branch named exactly `corral` exists (it would block the namespace) | trust user text |
| `create(reg)` | `git worktree add -b corral/<slug> -- <path> <base_sha>`; verify registration via `worktree list --porcelain -z`; set `phase: active` | reuse a path; create outside root; run in a repo with submodules or sparse checkout |
| `verify(reg)` | path registered to this common dir; worktree HEAD is symbolic and equals `reg.branch`; branch OID read; path not a symlink; path under root (resolved) | repair anything |
| `summary(reg)` | `GIT_OPTIONAL_LOCKS=0 git diff --numstat -z --no-renames --no-textconv --no-ext-diff <base_sha>` (tracked, working tree vs base; no index writes, no objects) + `git ls-files --others --exclude-standard -z` with sizes | write objects or touch any index |
| `snapshot(reg)` | **copy** the worktree's real index (`git rev-parse --git-path index`, which for a linked worktree is under `<common_dir>/worktrees/<name>/`) to a temp file under the pane dir, then with that temp index `add -A` and `write-tree` → **tree OID**. Starting from the real index keeps files the agent force-added (`add -f`) and intent-to-add entries. Untracked files over 512 KiB are not added; they are inventoried with the ignored files and named in review, so one review of a dataset cannot fill `.git/objects`. Records the **real index identity** (sha256 of its bytes) and whether any path is staged with content different from its working file. Pins the tree with `refs/corral/review/<wt-id>` (a commit of the tree) until the next snapshot or retirement, so gc cannot collect what the user is looking at. Returns the tree OID, HEAD, the base tip, the index identity and both inventories | touch the real index; rebuild on a timer |
| `diff(reg, tree)` | `git -c diff.renameLimit=1000 diff-tree -r -z --raw -M --no-textconv --no-ext-diff <base_sha> <tree>` (`-c` before the subcommand; after it, `diff-tree -c` means combined diff) for identities, then per-file patches with `-p --no-color --src-prefix=a/ --dst-prefix=b/`, each capped; files over 512 KiB or binary listed without hunks | follow symlinks; render textconv |
| `commit_tree(reg, tree, index_id, message, expect_head)` | the **commit protocol** below | run hooks; amend; touch the work tree; `read-tree -u`; prompt for a key |
| `push(reg, remote_name, push_url, oid)` | refuse if the worktree is dirty or HEAD's tree differs from the last reviewed tree; resolve the **effective push destination** (`git remote get-url --push --all <remote>`, which applies `pushInsteadOf`/`insteadOf`); refuse more than one; refuse if it differs from the confirmed one; push to that **URL**, not the remote name: `git push <push_url> <oid>:refs/heads/corral/<slug>` (fully qualified, so a tag of the same name cannot match); verify with `ls-remote <push_url>` | `--force`, `--force-with-lease`, push a ref other than `corral/*` |
| `open_pr(reg, title, body)` | `gh auth status`; resolve the target repo from the remote URL and show it in the confirmation; `gh pr list --repo R --head …` first, and `gh pr view` if one exists (idempotent); else `gh pr create --repo R --head … --base … --title … --body-file -` | pass text through a shell; let `gh` choose between a fork and its parent |
| `discard(reg, tree)` | **stop writers first**: cancel the turn, TERM then KILL the owner's process group (never SIGSTOP: a stopped process holding `index.lock` would block the move), wait until `index.lock` is gone, then scan `/proc/*/cwd` and `/proc/*/fd` (Linux; macOS uses `lsof`, §9.3) of the user's processes for anything inside the worktree (catches `setsid` grandchildren and the user's own shells) and refuse, naming them, if any remain; the held queue is kept unsent; `verify`; re-snapshot under the lock and compare to `tree`; write `refs/corral/recovery/<wt-id>/<ts>` → a commit of `tree` with parent HEAD; inventory ignored files into the registry; `mkdir -p <root>/.trash`; journal the move's source and destination; `git worktree move <path> <root>/.trash/<wt-id>-<ts>` (if Phase 0 shows `move` refuses a dirty worktree, a single `--force` is used **only after** the recovery ref exists; never the double `--force` that overrides a lock); `phase: trashed`; branch kept | delete files; delete the branch; prune |
| `restore(reg)` | `git worktree move` back from trash; `phase: active` | overwrite an existing path |
| `purge(reg, confirm)` | requires `confirm == branch short name`; `git worktree remove --force <trash path>`; delete the branch only if its OID equals the recorded one; keep the recovery ref | touch a path outside `<root>/.trash/`; delete a branch not `corral/*`; delete recovery refs |
| `reconcile(registry)` | for each common dir in the registry: `worktree list --porcelain -z`; mark `missing`/`tampered`; list unknown paths under root as orphans | prune; delete; edit |

**Commit protocol** (rewritten after round 2; all three reviewers showed the
v2 epilogue could drop staged work or leave a stale index):

1. Policy check: if `commit.gpgSign` is true, refuse at once. `commit-tree`
   would not sign (it ignores that setting), so the refusal is to avoid a
   silently unsigned commit in a repo that expects signatures.
2. Under the repo lock: refuse if the worktree's `index.lock` exists; re-run
   `snapshot` and require the **same tree OID** and the **same real-index
   identity** the user reviewed. Any difference → 409 `changed`.
3. If the snapshot flagged staged content that differs from the working file,
   preserve it: `write-tree` from an untouched copy of the real index and
   point `refs/corral/recovery/<wt-id>/<ts>-index` at a commit of it. Review
   shows "staged changes differ from files; the staged version is kept as a
   recovery ref" before Commit is allowed.
4. `commit-tree <tree> -p <expect_head>` with the message on stdin → `new`.
   Journal `{op_id, stage: prepared, new, expect_head}` **before** step 5.
5. `update-ref <branch> <new> <expect_head>`. Journal `stage: ref_moved`.
6. Reconcile the index: copy the real index, `read-tree <new>` into the copy
   (never `-u`, never the main worktree's index: the path comes from
   `rev-parse --git-path index` in the worktree), `update-index --refresh -q`
   on the copy, then atomically take `index.lock` by creating it, write the
   copy's bytes, and rename into place. Journal `stage: done`.
7. Postcondition: branch == `new` **and** `write-tree` of the real index ==
   `tree`. Restart recovery resumes from the journalled stage: `prepared`
   with the ref unmoved re-runs from step 5 using the stored `new` (no second
   commit); `ref_moved` re-runs step 6; anything that does not fit is
   `unknown` and blocks further actions on the pane until the user resolves it
   from the CLI.

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
check in review, with `check=False`: rc 0 clean, rc 1 conflicts (stdout
still begins with the OID of a conflicted tree, which is never used as a
result), anything else an error. The exact argv and the `-z` record layout
are frozen from Phase 0 fixtures on git 2.38 and 2.55; parsing follows
section boundaries, not every NUL. It writes
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
- **Claude trust.** Before `start()`, the hub marks **only the worktree's
  canonical path** trusted in the pane's private `CLAUDE_CONFIG_DIR` (the hub
  already seeds that dir). Phase 0 must attach the exact JSON diff and show
  that a shell command in the pane still reaches the approval rail. If either
  is not proven, the hub does not pre-trust and the Claude lane stays
  disabled for worktrees. The dialog says the folder is trusted for that
  pane.
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
| `POST /api/session/worktree/commit` | `{pane, tree, head, index_id, message}` | `{ok, commit}`; 409 `busy`, `changed`, `identity` |
| `POST /api/session/worktree/publish` | `{pane, oid, remote, push_url, pr:{repo, title, body}|null}` | `{ok, pushed, pr_url | compare_url}`; 409 `remote_changed`, `uncommitted`, `non_ff` |
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
whose `locations` resolve outside the worktree **and outside its own admin
dir** `<common_dir>/worktrees/<admin>/` (reads are ignored) cancels the
turn, raises a needs-you card naming the path, and leaves the review digest
unseen. The write has already happened; the point is that the user hears
about it at once, not at review. This does not cover shell commands, which
usually report only their cwd; the README says so.

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
0.6 Path-keyed state that every new worktree path meets, whatever the location: mise refuses untrusted config files per path (mise is installed here); Claude Code's auto-memory and project settings are keyed by cwd (record whether a worktree gets the repo's project memory or an empty one); `git config --show-origin` inside a worktree under the state dir picks up no `includeIf "gitdir:"` meant for the repo's folder.
0.7 macOS (dogma-2), per §9: run 0.1, 0.2 and 0.6 there too; record lane results per platform; prove the Claude lane starts in a worktree **without** pre-trust; record which auto-memory store an `~/ai-os` worktree pane loads and writes; prove the `lsof` replacement for the `/proc` scan finds a shell whose cwd is inside the worktree.
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
- T-GIT-7 `merge-tree` rc 1 with conflicts is parsed as conflicts, not an error; rc 128 is an error; the conflicted tree OID is never returned as a result.
- T-GIT-8 the commit message reaches `commit-tree` on a closed stdin pipe; no call inherits the hub's stdin.
- T-GIT-9 a stale `index.lock` whose recorded pid has been reused by another process is not treated as the hub's.

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
- T-SNP-5 an untracked file over 512 KiB is inventoried, not added; `.git/objects` grows by less than its size.
- T-SNP-6 the review ref pins the tree across `git gc --prune=now`.
- T-SNP-7 a content change that keeps numstat totals identical still changes the tree OID and blocks Commit.
- T-DIF-1 parseable unified output; worktree-relative paths.
- T-DIF-2 per-file and total caps; JSON response ≤ 2 MiB.
- T-DIF-3 binary and > 512 KiB files named without hunks.
- T-DIF-4 a symlink pointing outside the worktree shows as a symlink change, never followed.
- T-DIF-5 user config `diff.external`, `color.ui=always`, a textconv driver: none affect output.
- T-DIF-6 names with spaces, quotes, newlines and non-UTF-8 bytes round-trip via `-z` identities.
- T-DIF-7 a rename is shown as a rename; with > 1000 files, rename detection is skipped, not slow; the test asserts the production argv (`-c` before `diff-tree`).

**commit**
- T-CMT-1 the new commit's tree equals the reviewed tree exactly.
- T-CMT-2 a file changed after snapshot → 409 `changed`; nothing committed.
- T-CMT-3 branch moved after snapshot → 409 `identity`; nothing committed.
- T-CMT-4 repo has a pre-commit hook that would fail → hub commit still succeeds and the hook is not run (documented behaviour).
- T-CMT-5 after commit, `git status` in the worktree is clean for exactly the reviewed paths; an untracked file created after snapshot is still there, untracked.
- T-CMT-6 empty message refused; nothing-to-commit is a clear no-op.
- T-CMT-7 a file the agent force-added (`add -f`, otherwise ignored) is in the reviewed tree and in the commit; the real index equals the new HEAD afterwards; a second Commit is a no-op.
- T-CMT-8 `commit.gpgSign=true` fails in under a second, leaves no `index.lock`, and names the reason.
- T-CMT-9 after a hub commit, `git status` in the worktree shows no tracked file as modified (stat info refreshed).
- T-CMT-10 a staged blob that differs from its working file is preserved in a recovery ref before commit, and review said so.
- T-CMT-11 the real index changing between snapshot and commit (external `git add`) → 409 `changed`; nothing written.
- T-CMT-12 `index.lock` present → refused; the hub never removes a lock it did not create.

**publish**
- T-PUB-1 push to a local bare remote creates `corral/<slug>` at the exact OID.
- T-PUB-2 remote URL changed after confirmation → 409 `remote_changed`.
- T-PUB-3 uncommitted changes → 409 `uncommitted`.
- T-PUB-4 remote branch diverged → 409 `non_ff`; never forced.
- T-PUB-5 no `gh` → compare URL for GitHub remotes. T-PUB-6 `gh` signed out → reason names `gh auth login`.
- T-PUB-7 stub `gh` success → `pr_url` stored; a second call finds the existing PR and does not create another.
- T-PUB-8 a title containing `$(touch x)` reaches `gh` literally.
- T-PUB-9 a tag named `corral/<slug>` on the remote does not receive the push.
- T-PUB-11 a remote with a different `pushurl`, an `insteadOf` rewrite, or two push URLs: confirmation shows the effective URL; two URLs → refused; push goes to the confirmed URL only.
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
- T-RMV-11 discard with a live fake agent cancels the turn and kills its group; a `bg-write` child is gone before the move.
- T-RMV-12 a dirty worktree (modified, untracked and ignored files) moves to trash intact.
- T-RMV-13 a `setsid` grandchild or an outside shell with its cwd in the worktree → discard refused, naming the process.
- T-RMV-14 a message sent during discard is kept unsent and never dispatched into the trashed pane.
- T-RMV-15 a crash between journalling and completing the move: restart reports the true location.

**reconcile and restart**
- T-REC-1 healthy registry → no notes.
- T-REC-2 worktree dir deleted by hand → `missing`, reported once, no prune.
- T-REC-3 branch deleted by hand → reported.
- T-REC-4 an unknown dir under root → orphan.
- T-REC-5 the user's own worktrees and stale registrations elsewhere → untouched and unlisted (spy proves no `prune` call).
- T-REC-6 repo moved or deleted → entries `missing`; hub keeps running.

**crash (real hub subprocess, SIGKILL at each boundary)**
- T-CRS-1 during create, after intent / after `worktree add` / after pane save: restart yields either a live pane on the branch or a listed orphan; never an unlisted worktree.
- T-CRS-2 during commit, at `prepared`, `ref_moved` and before `done`: restart finishes from the journalled stage; the end state is branch == new and index tree == reviewed tree; retry never creates a second commit.
- T-CRS-3 during push, after push / before record: restart reads the remote ref and records it.
- T-CRS-4 during PR creation: restart finds the PR via `gh pr list` and does not create another.
- T-CRS-5 during discard, after recovery ref / after move: restart shows `trashed` or `active` truthfully; nothing lost.

**lifecycle**
- T-LIF-1 `worktree_id` round-trips through meta. T-LIF-2 metas without it load as before.
- T-LIF-3 the agent's cwd is the worktree plus the chosen subdir (fake agent reports cwd).
- T-LIF-4 roster full → refused before any registry entry or branch.
- T-LIF-5 agent start fails → a dead pane remains and owns the worktree (D14); the error names it.
- T-LIF-6 `Manager._lock` never held during a git call (`NestCheckLock`-style instrumentation).
- T-LIF-7 turn end → one `worktree` event; summary failure → a note, pane unaffected.
- T-LIF-8 no git call on the observe tick (spy over several ticks driven directly, no sleeps).
- T-LIF-9 restart: pane detached with its worktree; resume runs there.
- T-LIF-10 missing worktree at restart → resume refused with the reason.
- T-LIF-11 close keeps; reopen restores; forget offers discard and deletes nothing.
- T-LIF-12 port of a worktree pane refused; port of other panes unchanged.
- T-LIF-13 `host:` and Ollama refused; a lane not yet enabled refused.
- T-LIF-14 Claude pane's config dir gains exactly one change: trust for the worktree's canonical path (JSON diff asserted); no permission-mode change.
- T-LIF-15 two concurrent creates at eleven live panes: one succeeds, one is refused before any git call.
- T-LIF-16 a failed start leaves a dead pane owning the worktree; Resume retries there.
- T-LIF-17 a plain create (no `worktree`) whose cwd is inside the worktree root → 400.
- T-LIF-18 the D15 preamble is the first thing the agent receives.
- T-LIF-19 an edit-kind tool event with a location outside the worktree cancels the turn and raises a needs-you card; a read-kind one does nothing; a write under the worktree's own admin dir does nothing.

**safety under concurrency**
- T-SAFE-1 fake agent writes between snapshot and commit → 409 `changed`.
- T-SAFE-2 `bg-write` child keeps writing after the turn ends → review shows "files changed since you opened review"; Commit refused.
- T-SAFE-3 a send arriving during an action is queued and delivered after it.
- T-SAFE-4 two actions on one pane at once → the second gets 409 `busy`.
- T-SAFE-5 across snapshot, commit, discard and publish, the main worktree's HEAD, **index file bytes** and untracked set are unchanged, even with `GIT_INDEX_FILE` set in the hub's environment.

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
6. Commit protocol proven: T-CMT-9..12 and T-CRS-2 green, including a kill at every journalled stage.
7. Discard proven: T-RMV-11..15 green; nothing dispatches into a trashed or `unknown` pane.
8. Claude lane: the Phase 0 config diff is attached and a shell command still asks for approval; otherwise Claude ships without worktrees. On macOS the gate is §9.1 instead: no pre-trust, and start proven without it.
9. macOS: §9 items 9.2–9.5 resolved or deferred with a reason; T-RMV-13 green on darwin.

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
| Claude trust prompt stalls start | Pre-trust in the pane's config dir; Phase 0. On macOS there is no private config dir (§9.1) |
| An agent force-pushes or rewrites shared refs | D15 preamble; the rail still asks for Claude's shell commands; edit events outside the worktree cancel the turn |
| Commit signing configured | Hub Commit refuses fast and offers the command to run by hand |

## 7. Questions the panel settled

| Question | Decision | Panel |
|---|---|---|
| Worktree location | **State dir** (configurable), never tmpfs, trash under the same root. Settled 2026-10-02, see below | Sol, Grok for; Gemini against |
| Hooks on hub Commit | No hooks, no signature, said on the button; commit by hand when they matter | All three |
| Snapshot on open | Yes, pinned by a review ref, with a size cap for untracked files | All three |
| Merge in v1 | Copy merge command plus Push & PR; no agent-driven sync | All three |

### 7.1 Why the state dir, checked on this machine (v3.1)

Three places were weighed: the state dir; Gemini's sibling,
`<repo>/../.<repo>.corral/<slug>`; and inside the repo, at
`<repo>/.claude/worktrees/<name>`, which is where Claude Code's own
`--worktree` puts them.

- **Filesystem.** `~/.local/share`, `~/tools` and `~/aios` are all on one
  btrfs subvolume (`/dev/mapper/root[/@home]`). Gemini's "the sibling avoids
  EXDEV" point does not apply on this machine. Trash is under the root
  anyway, and probe already refuses a root on tmpfs or on another filesystem.
- **Gemini's tooling claims, one at a time.** Pyright and gopls root markers,
  pnpm and cargo workspace members, and `docker run -v $(pwd)` all resolve
  *inside* the worktree, because they are tracked files or the cwd, so they
  work at any location. A gitignored `.env` is missing from every new
  worktree wherever it lives (copying it is the v1.1 copy list). A reference
  that leaves the repo (`../other-repo`) breaks under Gemini's own layout
  too, because `.<repo>.corral/<slug>` is two levels down, not one.
- **What actually differs is what sits above the worktree.** In the state
  dir, nothing above it belongs to any project. As a sibling, the parent
  folder's config (`~/tools/…`) is inherited and the unsandboxed main
  checkout is reachable by a relative path. Inside the repo is worst. Tools
  that walk up (Claude's CLAUDE.md loading, Node module resolution, pytest
  `conftest.py`, mise config) would read the main checkout's files,
  uncommitted edits included. The main checkout's own watchers, `rg` and
  editor would see every worktree's files. It also needs an ignore entry
  written into the user's repo.
- **Backups are the same either way.** snapper covers only `/`, not
  `/home`. Uncommitted worktree work is protected by recovery refs and trash
  (D9), at any location. (dogma-2 differs: Time Machine includes
  `~/.local/share`; see §9.6.)
- **Cost accepted.** The path is long and hidden, so it is hard to find by
  hand. Mitigations: the review dialog shows the path with a Copy button,
  `corral-light worktrees` lists everything, and `$CORRAL_LIGHT_WORKTREES`
  overrides the root.

## 8. What changed, and why

### v3.1 to v3.2 (dogma-2 check)

| v3.1 | v3.2 | Why |
|---|---|---|
| Facts checked on omarchy-laptop only | §9 adds dogma-2 (macOS) facts and issues | The hub runs on both; four plan mechanisms are Linux-only |
| Phase 0 items 0.1–0.6 | Adds 0.7 (macOS) | §9 |
| Ship gate 1–8 | Gate 8 has a macOS form; adds gate 9 | §9.1, §9.3 |

### v3 to v3.1 (D2 settled)

| v3 | v3.1 | Why |
|---|---|---|
| D2 open, Craig to decide | State dir, default `$CORRAL_LIGHT_STATE/worktrees` | §7.1: Gemini's objections checked against this machine |
| Registry at `STATE/worktrees/registry/` (inside the worktree root) | `STATE/worktree-registry/` | Keeps the root to repo dirs and `.trash/`; `reconcile` would otherwise list it as an orphan |
| Worktree path only in the pill tooltip | Shown in review with a Copy button | Cost of a hidden path |
| — | Phase 0 item 0.6 | Path-keyed state that any new path triggers |

### v2 to v3 (round 2)

| v2 | v3 | Raised by |
|---|---|---|
| `read-tree <new>` into the real index after commit | Journalled commit protocol: same-tree and same-index check under the lock, staged-blob recovery ref, index rebuilt on a copy with stat refresh and swapped in under `index.lock`, postcondition on ref and index | Sol, Gemini, Grok (Grok withdrew its round-1 suggestion) |
| Queue drained after every action | Not drained after Discard or a failure | Sol, Grok |
| Discard paused the owner | Cancel, TERM/KILL the group, wait for `index.lock`, `/proc` scan for any process in the worktree; never SIGSTOP | Sol, Grok, Gemini |
| `remote get-url` | Effective push URL incl. `pushurl` and rewrites; one URL only; push to the URL | Sol |
| Pre-trust Claude | Exact canonical path only, JSON diff and rail proof in Phase 0, else lane disabled | Sol, Grok, Gemini |
| `diff-tree … -c diff.renameLimit` | `git -c … diff-tree` | Sol, Grok |
| `update-ref` failure by rc | rc 128; cause read from stderr and the ref | Grok |
| `commit-tree` "would prompt for a key" | It ignores `commit.gpgSign`; refusal kept as policy | Gemini, Grok |
| Wrapper had no stdin | Message on a closed pipe; DEVNULL otherwise | Grok |
| Snapshot objects unbounded and collectable | 512 KiB cap for untracked; review ref pins the tree | Sol, Grok |
| Same filesystem as the repo | Trash under the root; repo may be elsewhere | Sol, Grok |
| Admin-dir writes would cancel turns | Admin dir allowlisted; shell not covered, said plainly | Grok |
| Lock pid alone | pid plus start token | Gemini |
| T-LIF-5 removed the owner | Rewritten to match D14 | Sol, Grok |
| T-SAFE-5 cwd spy | Main index bytes asserted | Grok |

### v1 to v2 (round 1)


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

## 9. dogma-2 (macOS) check (v3.2)

Checked on dogma-2 (Mac mini M4, macOS 27, APFS) on 2026-10-02 against the
running hub's code and config. §0–§8 were checked on omarchy-laptop (Linux).
Items 9.1–9.5 change design or tests; 9.6–9.8 are facts for this host.

### 9.1 No private Claude config dir on macOS, so no pre-trust

`sessions.darwin_keychain_blocks_isolation()` returns true on darwin: setting
`CLAUDE_CONFIG_DIR` changes the Keychain service name and the login is not
found. So `posture_enforceable` is false there, and Claude panes run on the
user's real `~/.claude` and `~/.claude.json`. The §2.3 pre-trust ("only in the
pane's private config dir") has no private dir to write. Writing trust into
the real `~/.claude.json` instead would be a global config change per worktree
path, and it is **not** allowed.

- On darwin the hub never pre-trusts. T-LIF-14 is Linux-only; a darwin test
  asserts `~/.claude.json` is byte-identical across a worktree create.
- Evidence that this may not matter: corral-light's lane probes have run
  Claude in fresh `/private/var/folders/...` dirs (their `~/.claude/projects`
  entries exist) with no trust entry in `~/.claude.json`. Not yet proven for
  a start through the UI. Phase 0.7 proves start without pre-trust; if it
  stalls, the Claude lane ships without worktrees on macOS.

### 9.2 Shared `~/.claude`: cwd-keyed state lands in the user's real home

Because 9.1 shares `~/.claude`, every worktree path creates a permanent
`~/.claude/projects/<cwd-slug>/` (transcripts) in the user's real home, and
the pane's auto-memory store is keyed by the worktree path, not the repo.

- For `~/ai-os`, whose store is fold-generated by memory-mesh, an `~/ai-os`
  worktree pane gets **no** memory index. One past Claude Code `--worktree`
  session of ai-os on dogma-2 (2026-09, v2.1.219) loaded none. Memory it
  writes lands in a store that memory-mesh neither folds nor guards (the
  write guard derives its store from its own install path, i.e. the main
  checkout).
- Decision needed before WS2: warn in the dialog for repos with a
  `.mesh-generated` store, or refuse them. Recommendation: warn, and record
  the observed behaviour in Phase 0.7 first.
- `reconcile` and purge never touch `~/.claude/projects/`; the dirs are listed
  by `doctor` as left behind by purged worktrees.

### 9.3 Discard's `/proc` scan does not exist on macOS

§2.2 `discard` scans `/proc/*/cwd` and `/proc/*/fd` to refuse while any
process is inside the worktree. macOS has no `/proc`; done as written, the
scan finds nothing and the move proceeds. On darwin use `lsof` (present at
`/usr/sbin/lsof`): `lsof -nP -a -u <uid> -d cwd` for cwds plus
`lsof -nP -u <uid> +D <worktree>` for open files, each under the wrapper's
timeout. **A scan that fails or times out refuses the discard** (fail closed).
T-RMV-13 runs on darwin with the `lsof` path. `corral_core.acp.process_start_token`
already falls back to `ps -o lstart=` off Linux; its one-second resolution is
accepted for the stale-`index.lock` check (T-GIT-9) and noted there.

### 9.4 Lane results are per platform

Codex's workspace-write sandbox is Seatbelt on macOS and a different
mechanism on Linux, so a Linux pass does not enable Codex on macOS. The
`lanes-check.json` on dogma-2 shows Gemini `unknown`. D8 is amended in
practice: lane enabling is keyed by `(lane, sys.platform)`, and the §5.3
matrix is run on both hosts.

### 9.5 Case-insensitive APFS and `/private` temp paths

- `/Users` is case-insensitive here, and `os.path.realpath` does not fold
  case (`.../Ab` opened as `.../aB` resolves to `.../aB`). `<hash6>` from the
  realpath of the common dir can therefore split one repo into two root dirs.
  The per-repo lock (dev:inode) stays correct. Fix: derive `<hash6>` from
  `common_dir_id` (dev:inode), or canonicalise case first (`F_GETPATH`).
  New test T-CRT-8: one repo opened under two letter-cases gives one repo dir.
- `tempfile.mkdtemp()` returns `/var/folders/...`, which is a symlink to
  `/private/var/...`. T-CRT-6 ("root is a symlink → refused") would refuse
  every darwin test run unless the suite realpaths its temp roots. The suite
  does that, and T-CRT-6 builds its symlink explicitly.

### 9.6 Backups

Time Machine includes `~/.local/share` on dogma-2 (`tmutil isexcluded`:
Included). Worktrees and `.trash/` are backed up here, unlike on the laptop,
and trash size adds to the backup. `doctor` reports trash size (WS5) either
way; no exclusion is added without Craig.

### 9.7 Paths and tools on dogma-2

| Plan says (laptop) | dogma-2 |
|---|---|
| `~/tools/corral-light` | `~/corral-light` (launchd runs the hub from here; never a worktree) |
| `~/aios` (0.4 timings) | `~/ai-os` |
| git 2.55 | `/opt/homebrew/bin/git` 2.55.0 first on the hub's launchd PATH; `/usr/bin/git` 2.54.0 (Apple) second. The wrapper resolves git once at start and `doctor` prints which |
| gh 2.101 | gh 2.96.0, signed in as `cvp1` |
| Claude Code 2.1.287 | 2.1.285 CLI; panes use the adapter's bundled binary |
| mise installed | not installed; the mise part of 0.6 is laptop-only |
| Chromium for T-VIS | none installed; T-VIS skips loudly here, so the visual gate runs on the laptop |
| git 2.38 via container | Docker 29.6.2 running; the container route works here |
| `/home` one btrfs subvolume | `~/.local/share`, `~/corral-light`, `~/ai-os`, `~/Github/CC` all on one APFS volume (same `st_dev`) |

### 9.8 Global hooks reach Claude panes on macOS

With a shared `~/.claude` (9.1), user-level hooks run inside Claude panes. On
2026-10-02 none are wired at user level. `~/ai-os/tools/session_git_guard.py`
(Stop hook: "commit + push") exists in a settings backup only. If it is
wired again, it would tell a worktree pane to push, against D15. The guard
must skip cwds under the worktree root before it is re-wired. Repo-level
hooks (e.g. `~/ai-os/.claude/settings.json`, tracked) are present in a
worktree and run the main checkout's scripts by absolute path; Phase 0.7
records that they do not error there.
