You are one of three independent reviewers on a design-and-test-plan panel. The other two are different models from different vendors; you will see their reviews in a second round. The author is a Claude agent working for the operator, who owns this system and makes the final call.

WHAT YOU ARE REVIEWING
An implementation and testing plan for Corral Light — a browser "wall" of AI coding-agent panes driven by one stdlib-Python hub per machine over ACP (lanes: Claude Code, ChatGPT/Codex, Grok, Antigravity/Gemini; plus remote `host:` shell lanes and a chat-only Ollama lane). The feature: a pane can start on its own git worktree and branch, so several agents can work on one repository in parallel without colliding, and the user reviews each agent's work as a diff, then commits, merges, pushes/opens a PR, sends feedback, or discards it. This is the feature competing tools (Conductor, Claude Squad, Crystal, Vibe Kanban, Codex cloud) have and Corral Light lacks.

FACTS ABOUT THE CODEBASE (verified by the author, use them):
- Hub: stdlib ThreadingHTTPServer; one request thread per call; pane creation blocks the request thread through the ACP handshake (up to 180 s). Manager._lock guards the roster and is never held during spawn. A 5 s observe tick snapshots every pane. Max 12 live panes.
- A pane's cwd is passed both as the subprocess cwd and in ACP session/new and session/load; resume reuses it. Only check today: cwd.is_dir().
- Pane metadata persists in META_KEYS to panes/<id>/meta.json; a key not read in from_meta is blanked on the next save. Nothing is ever deleted: close archives, forget hides, transcripts stay.
- The hub never runs git today. No test creates a git repo.
- Permission rail: agents send session/request_permission; the hub records a digest and requires it to answer. No path rules.
- Codex runs workspace-write (sandboxed to cwd, network off). host: lanes ignore cwd entirely. Ollama has no tools.
- Browser: vanilla ES module, no framework; text only via textContent; pane header rebuilt only when a signature string changes; display state is pinned by a cross-product parity table.
- Port moves a conversation to another lane by creating a new pane with the source cwd, bound to a previewed sha.
- Tests: stdlib unittest (~770 tests) with a fake ACP agent process, in-process Manager, real hub subprocesses, and Node selftests that extract functions from app.js. No real browser tests in the suite.
- Machine: Linux, git 2.55, gh 2.101; /tmp is tmpfs.

Everything else you need is in the plan below. Do not open files or run commands. If a claim looks wrong but you cannot check it from the text, say "unverifiable from the text" — but you MAY draw on your own knowledge of git internals (worktrees, refs, index, merge-tree, hooks, locks), the agent CLIs' sandboxes, and gh, and should say when you do.

YOUR JOB — break it, then fix it
1. VERDICT, one line each with the strongest reason, BUILD / RESHAPE / KILL: (a) the overall approach, (b) worktrees.py design, (c) the lifecycle integration, (d) the review UI, (e) the test plan.
2. LOST WORK. Walk every path where a user could lose or silently misplace work (agent edits, commits, the main checkout, the branch, the remote). For each: trigger, what happens, fix. This is the most important section.
3. GIT CORRECTNESS. Ranked, at most 10: places where the plan's git usage is wrong, racy, version-sensitive or will surprise (worktree add/remove/prune semantics, the temporary-index trick, merge-tree, --no-ff merge in the main checkout, hooks, locks, GIT_OPTIONAL_LOCKS, renames, submodules, LFS, sparse checkout, safe.directory, core.autocrlf). Each with the exact fix.
4. AGENT REALITIES: for each lane, what will actually happen when its CLI runs inside a linked worktree (sandbox, trust prompts, repo-root detection, committing). Mark what is your knowledge vs guess.
5. ANSWER the plan's §7 open questions, one short paragraph each, with a recommendation.
6. TESTS: what is missing from the §5 catalogue that would let lost work or a safety-rule violation ship? At most 10 new test IDs with one-line definitions. Also name any listed test that is untestable as written.
7. SCOPE: what should be cut from v1, and what is missing that v1 cannot ship without? At most 5 each.
8. ONE SENTENCE: the single most likely way this feature hurts the operator in its first week.

FORMAT: Markdown, under 1,800 words. Start with the five verdict lines. No preamble.

---- THE PLAN (docs/worktree-review-plan.md, v1) ----
# Isolated branches and review — implementation and test plan

Status: PLAN, v1, 2026-10-01. Nothing built. Written for panel review; the
panel record will live in `reviews/2026-10-01-worktree-panel/`.

**What this adds.** A pane can start on its own git worktree, on its own
branch, cut from the repository the user picked. Several agents can then
work on one codebase at once without overwriting each other. When an agent
finishes, the user reviews what it changed as a diff, and then commits,
merges, pushes and opens a pull request, sends feedback, or discards it. All
of this happens from the wall.

**Why.** Every comparable tool (Conductor, Claude Squad, Crystal, Vibe
Kanban, Codex cloud, Claude Code on the web) isolates parallel agents and
puts the diff at the centre of review. Corral Light has neither. Today two
panes on one repo share one working tree, and the only record of what an
agent did is its transcript. The wall exists to run many agents side by
side, and without isolation that is safe only across different projects.

**Shape of the answer.** One small new module that is the only code allowed
to run git (`worktrees.py`). Worktree state lives on the pane record. A
handful of `/api/session/worktree/*` routes. A header pill and a review
dialog in the browser. One rail card. Everything is opt-in per pane;
a pane without a worktree behaves exactly as today.

---

## 0. Decisions proposed (for the operator to confirm)

| # | Decision | Proposed | Why |
|---|---|---|---|
| D1 | Opt-in or default | Opt-in per pane, a checkbox in New conversation, remembered per repository | Many panes are chat or ops work in ~/aios; a surprise branch there is worse than no isolation |
| D2 | Where worktrees live | `$CORRAL_LIGHT_WORKTREES`, default `~/.local/share/corral-light/worktrees/<repo>-<hash6>/<slug>` | Inside $HOME (never /tmp, which is tmpfs here), out of the user's project tree, one place to audit |
| D3 | Branch name | `corral/<slug>` where slug comes from the pane title or the first prompt, else the pane id; collisions get `-2`, `-3` | Readable in `git branch`, namespaced so cleanup can never touch a user branch |
| D4 | Base | The branch checked out in the chosen repo at creation, recorded as `base_ref` plus `base_sha` | Matches what the user sees in their editor |
| D5 | Who commits | The agent may commit; the hub also offers Commit in review. The diff always shows `base_sha` to the working tree, committed and uncommitted together | Agents differ in whether they commit; review must not depend on it |
| D6 | Merge target | The base branch, merged in the main checkout, only when that checkout is clean and on the base branch; otherwise refuse with the reason | Never rewrite a checkout the user is typing in |
| D7 | Push and PR | Push the branch to `origin`, then `gh pr create` when `gh` is installed and signed in; else print the compare URL | Uses the user's own credentials; no tokens stored |
| D8 | Lanes | Claude, Codex, Grok, Gemini. Not `host:` lanes (remote shell ignores cwd) and not Ollama (no tools) | From the lifecycle map: those two cannot use a local worktree |
| D9 | Deletion | Only `git worktree remove` and `git branch -d/-D` on `corral/*` branches the hub created. Never `rm -rf` | A bug in cleanup must not be able to delete user work |
| D10 | Display state | Unchanged. Review status is a separate badge | `displayState` is pinned by `corral_core/display_cases.json`; a new state would break parity with full Corral |

## 1. Requirements

### 1.1 Functional

- F1. In New conversation, when the folder is inside a git work tree and the
  lane supports it, an "Own branch" checkbox appears with the branch that
  would be cut from (`main @ 3f2a1c9`).
- F2. Starting the pane creates the worktree and branch, then starts the
  agent with the worktree as its cwd. The pane header shows the branch.
- F3. The header shows a live change summary: files changed, lines added and
  removed, commits ahead of base, commits the base has moved since.
- F4. A Review button opens a dialog: a file list and a unified diff, with
  untracked files included and binary files named, not rendered.
- F5. From review: Commit (message prefilled from the pane title, editable),
  Merge into base, Push and open PR, Discard, and Send feedback (quotes the
  selected hunk into the pane's composer).
- F6. A "Ready to review" rail card appears when a worktree pane ends a turn
  with changes the user has not opened since.
- F7. Closing a pane keeps the worktree; reopening it from the archive brings
  it back on the same branch. Forgetting a pane offers to remove the
  worktree; removal refuses if there are unmerged commits or uncommitted
  changes unless the user confirms by typing the branch name.
- F8. Port to another lane hands the worktree to the new pane and pauses the
  source; a worktree has exactly one owning pane.
- F9. After a hub restart, every worktree pane comes back with its branch,
  and any worktree whose directory or branch vanished is reported, not
  silently dropped.
- F10. A "Branches" palette entry (⌘K) lists every hub-made worktree, its
  owner pane, and its status, including orphans.

### 1.2 Non-functional

- N1. No git call holds `Manager._lock`. Every git call has a timeout.
- N2. The browser never receives more than 2 MiB of diff in one response;
  larger diffs are truncated per file with a visible notice.
- N3. Git is run without a shell, with arguments after `--` where paths
  appear, `GIT_TERMINAL_PROMPT=0`, `LC_ALL=C`, no pager, no editor.
- N4. Every mutating action is bound to a preview digest (the port pattern):
  the hub refuses if the tree changed between preview and action.
- N5. Outward actions (push, PR) require a cookie session, same origin, and
  an explicit confirm in the dialog that names the remote and branch.
- N6. Works with git 2.38+ (`merge-tree --write-tree`). Below that, Merge is
  hidden with a note; everything else works from git 2.25.
- N7. Mobile (≤820px): the review dialog is full-screen and usable one-handed.

### 1.3 Out of scope for v1

Submodule recursion (warned, not handled), Git LFS smudge in new worktrees
(warned), rebasing for the user (the agent is asked to do it instead),
remote hosts, the scheduler and rigs creating worktree panes (accepted later
through the same `create` argument), multi-root workspaces.

## 2. Architecture

```
browser                     hub.py routes                  sessions.Manager / Pane          worktrees.py (only git caller)
New dialog ──probe──────▶ GET  /api/worktree/probe ─────────────────────────────────────▶ probe(cwd)
           ──start──────▶ POST /api/session/new {worktree:true} ─▶ create() ─▶ wt.create() ─▶ git worktree add -b
Pane head  ◀─SSE "worktree"── Pane.emit("worktree", summary) ◀── refresh_summary() ◀────── status(), diff --numstat
Review     ──preview────▶ GET  /api/session/worktree/diff ─────────────────────────────────▶ diff(base..worktree)
           ──commit─────▶ POST /api/session/worktree/commit {sha} ─────────────────────────▶ commit()
           ──merge──────▶ POST /api/session/worktree/merge  {sha} ─────────────────────────▶ merge_preview()/merge()
           ──push/pr────▶ POST /api/session/worktree/publish {sha} ────────────────────────▶ push(), gh pr create
           ──discard────▶ POST /api/session/worktree/discard {sha, confirm} ───────────────▶ remove()
```

### 2.1 The record on the pane

New `META_KEYS` entry `worktree` (core) holding a dict, or absent:

```python
{
  "v": 1,
  "path": "/home/USER/.local/share/corral-light/worktrees/aios-3f2a1c/fix-login",
  "branch": "corral/fix-login",
  "repo": "/home/USER/aios",            # main worktree top level at creation
  "common_dir": "/home/USER/aios/.git", # git rev-parse --git-common-dir, absolute
  "base_ref": "refs/heads/main",
  "base_sha": "3f2a1c9…",                 # full sha
  "created": "2026-10-01T22:10:00Z",
  "status": "active",                     # active | merged | published | discarded | missing
  "merged_sha": null, "pr_url": null,
  "seen_digest": null                     # digest the user last opened in review
}
```

`Pane.from_meta` must read it (the lifecycle map warns that a key not read
there is blanked by the next save). `Pane.snapshot()` gains `worktree`:
the record minus `common_dir`, plus the live `summary` (2.3).

### 2.2 `worktrees.py` — the only module that runs git

Stdlib only. Public surface, each function documented with what it never does:

| Function | Does | Never |
|---|---|---|
| `git(args, cwd, timeout=20, check=True)` | `subprocess.run(["git", *args], cwd=…, env=ENV, capture_output=True, timeout=…)`; raises `GitError(cmd, rc, stderr[:400])` | shell, pager, prompts, editor |
| `probe(path)` | `{inside, top, branch, head, detached, dirty, bare, submodules, lfs, git_version, merge_ok}` | writes |
| `plan_slug(title, existing)` | slug `[a-z0-9-]{1,40}`, validated by `git check-ref-format --branch` | trust user text |
| `create(repo, slug, root)` | resolves base, `git worktree add -b corral/<slug> <path> <base_sha>`, returns record | create outside `root`; reuse an existing path |
| `status(rec)` | branch exists, path exists, HEAD, ahead/behind vs `base_ref`, dirty count | writes (uses `GIT_OPTIONAL_LOCKS=0`) |
| `summary(rec)` | numstat of base_sha → working tree incl. untracked, via a temporary index | touch the worktree's real index |
| `diff(rec, max_bytes)` | unified diff with `--no-color --no-ext-diff --src-prefix=a/ --dst-prefix=b/ -M`, per-file caps, binary markers, and `digest` = sha256 of (HEAD, temp-index tree id) | include files outside the worktree |
| `commit(rec, message, digest)` | re-diffs, refuses on digest mismatch, `add -A`, `commit -m` with the user's identity | `--no-verify`; amend |
| `merge_preview(rec)` | `git merge-tree --write-tree base_ref HEAD` → clean or conflict file list | touch any checkout |
| `merge(rec, digest)` | checks main checkout clean and on base, then `git -C repo merge --no-ff --no-edit corral/<slug>`; on failure `merge --abort` | merge with a dirty main checkout; force |
| `push(rec, remote)` | `git push -u <remote> corral/<slug>` | `--force` |
| `open_pr(rec, title, body)` | `gh pr create --head … --base … --title … --body-file -` if `gh auth status` ok | store tokens |
| `remove(rec, force_unmerged)` | `git worktree remove [--force]`, then `git branch -d` (or `-D` when forced and confirmed) | touch a branch not starting `corral/`; touch a path outside `root` |
| `reconcile(records)` | `git worktree list --porcelain` per common dir; mark missing; `git worktree prune` only for hub paths | delete anything |

`ENV` = os.environ with `GIT_TERMINAL_PROMPT=0`, `GIT_PAGER=cat`,
`GIT_EDITOR=true`, `LC_ALL=C`, `GIT_OPTIONAL_LOCKS=0` (read paths only).

**Per-repository lock.** `threading.Lock` keyed by `common_dir`, held for
every mutating call, so two panes cutting worktrees from one repo cannot race
on `.git/worktrees` or the ref lock.

**The temporary index trick** (for untracked files without disturbing the
agent): `GIT_INDEX_FILE=<tmp>` with `git read-tree HEAD`, `git add -A`,
`git write-tree`, then `git diff <base_sha> <tree>`. The tmp file is under
the pane dir, removed in `finally`.

**Path guards.** `root = Path(WORKTREES).resolve()`; every path the module
writes or removes must satisfy `resolved.is_relative_to(root)` and not be a
symlink. Branch names must start `corral/` before any delete.

### 2.3 The live summary

`summary = {files, add, del, untracked, ahead, behind, dirty, conflicts_with_base, at, digest}`.

Computed (on a worker thread, never the request thread for SSE) at:
pane creation, every `turn_end` for that pane, after each worktree action,
on `GET /api/session/worktree/diff`, and every 60 s while the review dialog
is open (the browser asks). Not on the 5 s observe tick: that would run git
for twelve panes every five seconds for no reason. Emitted as a pane event
`worktree` so the existing SSE path carries it; the browser mirrors it into
`p.worktree.summary`.

`behind` and `conflicts_with_base` use `merge-tree` and are computed at most
once per 5 minutes per pane, or on demand in the dialog.

### 2.4 Hub routes (`hub.py`)

All behind the cookie and same-origin checks the other POST routes use.
User errors raise `ValueError` (→ 400). Pane lookups via `MGR.get`.

| Route | Body / query | Returns |
|---|---|---|
| `GET /api/worktree/probe?cwd=` | | `probe()` result plus `allowed` (lane + version checks happen client-side with the agent list) |
| `POST /api/session/new` | existing body plus `worktree: true`, optional `branch` | as today; snapshot includes `worktree` |
| `GET /api/session/worktree/diff?pane=&full=` | | `{files:[{path, old_path, status, add, del, binary, truncated, hunks}], digest, summary, merge: {ok, conflicts:[]}}` |
| `POST /api/session/worktree/commit` | `{pane, digest, message}` | `{ok, sha, summary}` |
| `POST /api/session/worktree/merge` | `{pane, digest}` | `{ok, merged_sha}` or 409 `{error, reason: dirty_main|not_on_base|conflict|behind}` |
| `POST /api/session/worktree/publish` | `{pane, digest, remote, pr: {title, body} | null}` | `{ok, pushed, pr_url | compare_url}` |
| `POST /api/session/worktree/discard` | `{pane, digest, confirm_branch?}` | `{ok}` |
| `POST /api/session/worktree/feedback` | `{pane, path, hunk_header, text}` | reuses the quote path; `{ok}` |
| `GET /api/worktrees` | | every record across panes, live and archived, plus orphans from `reconcile` |

`hub.py` is a long if-chain; the new routes go in one block before the 404,
with a comment naming this plan.

### 2.5 Manager and Pane changes (`sessions.py`, `corral_core/sessions.py`)

- `Manager.create(..., worktree=False, branch=None)`: if set, validate lane
  (D8), `probe(cwd)`, then `worktrees.create(...)` **before** `Pane(...)` but
  **outside** `self._lock`; the pane is built with `cwd = rec["path"]` and
  `worktree = rec`. If `pane.start()` raises, the worktree is kept and
  reported in the error ("the branch is kept at …; Forget removes it"),
  because the agent may have been mid-handshake and deleting would be the
  surprise.
- Roster cap check happens **before** the worktree is cut, so a refused pane
  never leaves a branch behind.
- `Pane.from_meta`: read `worktree`; if `path` no longer exists, set
  `status="missing"` and keep the pane detached with a note; `resume` refuses
  with "this pane's branch folder is gone; see Branches".
- `Manager.restore()`: after the roster is rebuilt, run `reconcile` on a
  background thread and emit notes.
- `Manager.port`: if the source has a worktree, the new pane takes the record
  (same path), the source is paused and its record set to
  `{…, "status": "handed_off", "to": new_id}`.
- `Manager.forget`: unchanged default; a new `forget(..., remove_worktree=…)`
  path for F7.
- `Pane.on turn_end`: enqueue a summary refresh.
- Scheduler and rigs: pass through `worktree` only if present in their
  stored spec; no UI in v1.

### 2.6 Browser (`static/app.js`, `index.html`, `style.css`)

- **New conversation.** Under the folder field: a row hidden until
  `GET /api/worktree/probe` (debounced 300 ms on folder change) says it is a
  repo. Checkbox "Own branch", hint "cut from `main @ 3f2a1c9`", and a
  warning line when the repo has submodules or LFS, or the main checkout is
  dirty ("uncommitted changes in ~/aios stay there; the branch starts from the
  last commit"). The checked state is remembered per repo top level in
  `localStorage['corral.wt.<top>']`. Sent as `worktree: true` in `common`.
  Hidden for `host:` and Ollama agents.
- **Header.** A `button.pill.wt` after `.meta`: `⎇ fix-login · 4 files +120 −8`
  and `↓3` when the base moved. Click opens review. `headSignature` gains
  branch, status and the summary's files/add/del/ahead/behind/digest.
  `.meta` shows the original repo (`~/aios`) not the long worktree path, with
  the full path in the tooltip.
- **Review dialog** `#revdlg`: its own size override (`min(1200px, 96vw)` by
  `90vh`; full-screen under 820px). Left: file list with status letter and
  +/−, filter box. Right: unified diff, line numbers, add/del colours reused
  from `.perm pre .add/.del`, hunk headers sticky, each hunk with
  "Send feedback". Footer: Commit, Merge into `main`, Push & PR, Discard,
  Refresh, Close. Disabled buttons carry the reason in their title.
  A small unified-diff parser `parseUnified(text)` lives in app.js; it is a
  pure function so the Node selftest can pin it.
- **Rail.** One more card loop: worktree panes whose `summary.digest` differs
  from `seen_digest` and whose state is your-turn. Does not count toward
  `blocked`.
- **Palette.** "Branches · every agent branch" row → a list dialog with
  status and actions (open pane, review, remove).
- **Keys.** `r` with a focused worktree pane (not in a text field) opens
  review; documented in the `?` overlay.
- **SSE.** One `if (ev.kind === 'worktree')` line mirrors the summary.

### 2.7 Agent-side realities (to verify in Phase 0, not assume)

- **Codex sandbox.** Codex runs workspace-write rooted at its cwd. A linked
  worktree's `.git` is a file pointing into `<repo>/.git/worktrees/<name>`,
  outside the cwd. Expect `git commit` from inside Codex to fail with a
  permission error. Options, in order: (a) accept it, the hub commits on
  review (D5 already allows that); (b) add the common dir to Codex's
  writable roots for that pane via launcher config, if Codex supports it.
- **Claude.** The per-pane `CLAUDE_CONFIG_DIR` already exists; a new
  directory may trigger a trust prompt in some versions. Verify the ACP
  adapter does not block on it. Project `CLAUDE.md` and `.claude/` are
  tracked files, so they appear in the worktree.
- **Gemini and Grok.** Verify each writes inside the worktree and does not
  resolve the repo root back to the main checkout (some tools walk up to the
  first `.git` directory; in a worktree that is a file).
- **Gitignored state.** `node_modules`, `.venv`, `.env` are absent in a new
  worktree. v1 shows this in the dialog hint and lets the agent install. v1.1
  adds an opt-in per-repo `.corral/worktree-setup` list of paths to copy (not
  a script; running a repo-supplied script is a code-execution decision the operator
  should make separately).

## 3. Workstreams and tasks

Each task follows house style: failing test first, minimal code, focused
run, full run, one commit. Test IDs are defined in §5.

### WS0 — Phase 0 spike (half a day, no product code)

0.1 On a scratch repo under `~/tmp-wt-spike` (not /tmp), create a worktree by
hand and start each lane in it through the existing UI (cwd = worktree path).
Ask each to: edit two files, add one, run `git status`, and `git commit`.
Record per lane: edits landed in the worktree (yes/no), commit worked
(yes/no/error text), any trust or sandbox prompt.
0.2 Check `git merge-tree --write-tree` output format on git 2.55 for clean
and conflicting cases; save fixtures under `testkit/git_fixtures/`.
0.3 Time `summary()` on ~/aios and on a 50k-file repo to set the refresh
policy (target < 300 ms; if not, cache by `(HEAD, mtime of index)`).
- Exit: a table in `docs/worktree-phase0.md`; plan amended if Codex cannot
  commit or any lane escapes the worktree.

### WS1 — `worktrees.py` core

1.1 `git()` wrapper, env, timeout, `GitError`. Proven by T-GIT-1..4.
1.2 `probe()`. T-PRB-1..7.
1.3 `plan_slug()` and branch validation. T-SLG-1..6.
1.4 `create()` with the per-repo lock and path guards. T-CRT-1..9.
1.5 `status()` and `summary()` with the temporary index. T-SUM-1..8.
1.6 `diff()` with caps and digest. T-DIF-1..9.
1.7 `commit()`. T-CMT-1..5.
1.8 `merge_preview()` and `merge()`. T-MRG-1..9.
1.9 `push()` and `open_pr()` against a local bare remote and a stub `gh`. T-PUB-1..7.
1.10 `remove()`. T-RMV-1..8.
1.11 `reconcile()`. T-REC-1..6.

### WS2 — pane lifecycle

2.1 `worktree` in `META_KEYS`, `from_meta`, `snapshot`. T-LIF-1..3.
2.2 `Manager.create(worktree=True)`: order of cap check, cut, construct,
start; failure keeps the branch. T-LIF-4..9.
2.3 Summary refresh on `turn_end`, `worktree` event. T-LIF-10..12.
2.4 Restore and reconcile; missing worktree path. T-LIF-13..16.
2.5 Port hand-off. T-LIF-17..19.
2.6 Close, reopen, forget with removal. T-LIF-20..24.
2.7 Lane refusal (`host:`, Ollama). T-LIF-25..26.

### WS3 — routes

3.1 Probe route. T-RTE-1..3.
3.2 Diff route incl. cap and digest. T-RTE-4..7.
3.3 Commit, merge, publish, discard with digest binding and 409 reasons. T-RTE-8..16.
3.4 Auth: every new route 401 without cookie, 403 cross-origin. T-RTE-17..18.
3.5 `/api/worktrees` with orphans. T-RTE-19..20.

### WS4 — browser

4.1 Dialog probe row and checkbox; remembered per repo. T-UI-1..4.
4.2 Header pill and `headSignature`. T-UI-5..7.
4.3 `parseUnified` and the review dialog. T-UI-8..14.
4.4 Actions wired with preview digests; disabled-with-reason states. T-UI-15..19.
4.5 Rail card, palette Branches, `r` key. T-UI-20..23.
4.6 Mobile and themes. T-VIS-1..8.

### WS5 — docs and release

5.1 README section "Own branch: several agents on one repo" with the
safety rules (D9) stated plainly.
5.2 `corral-light doctor` reports git version and whether Merge is available.
5.3 `docs/worktree-phase0.md` and the panel synthesis committed.

## 4. Sequence

| Phase | Work | Entry | Exit |
|---|---|---|---|
| P0 | WS0 spike | plan approved | phase-0 table; plan amended |
| P1 | WS1 core | P0 exit | all T-GIT..T-REC green; no product wiring yet |
| P2 | WS2 + WS3 | P1 | lifecycle and route tests green; feature reachable via API only |
| P3 | WS4 | P2 | Node selftests and visual checks green |
| P4 | Dogfood | P3 | one week on the operator's machine, ≥ 3 parallel panes on one repo at least twice, merge and PR used for real |
| P5 | WS5, release | P4 with no open P1 bugs | docs merged, panel synthesis filed |

Feature flag: `CORRAL_LIGHT_WORKTREES=0` hides the checkbox and refuses
`worktree: true`, for rollback without a code revert. Existing worktree panes
still resume (their cwd is a plain directory).

## 5. Test plan

### 5.1 Levels

| Level | Where | Runs | Tooling |
|---|---|---|---|
| Unit (git) | `test_worktrees.py` (new, collected by `test_corral_light.py`) | every commit | real git in temp repos; `GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_NOSYSTEM=1`, fixed author env |
| Lifecycle | `test_worktrees.py::Lifecycle*` | every commit | in-process Manager with the fake ACP lane (`FakeLaneCase` pattern) |
| Routes | `test_worktree_routes.py` | every commit | `ThreadingHTTPServer` + minted cookie (the `test_seat_routes` pattern) |
| Browser logic | `selftest_review.mjs` | every commit | `fn()`/`constant()` extraction from app.js |
| Visual | `selftest_visual.mjs` (dev-only) | before merge to master | headless Chromium over CDP, the screenshot driver used for the 2026-10-01 layout fix; skips loudly with no Chromium |
| Lane matrix | manual, scripted checklist | P0 and P4 | real lanes on a scratch repo |
| Regression | existing `test_*.py` and `selftest_*.mjs` | every commit | unchanged |

Temp repos are made with `tempfile.mkdtemp(prefix="corral-wt-test-")`. Each
test sets `CORRAL_LIGHT_WORKTREES` to a temp root, so no test can create a
worktree under the real state dir. A guard test (T-ISO-1) fails the suite if
the real root gains an entry during the run.

**Fake agent extension** (`testkit/fake_acp_agent.py`): new prompt verbs
`write <relpath> <text>` (writes inside the session cwd, refuses `..`),
`rm <relpath>`, and `commit <msg>` (runs git in cwd). These let lifecycle
tests make an "agent" change files in its worktree.

### 5.2 Catalogue

**git wrapper**
- T-GIT-1 a failing command raises `GitError` with rc and the first 400 chars of stderr.
- T-GIT-2 a command that would prompt (push to an auth-required URL) fails fast, not hangs (`GIT_TERMINAL_PROMPT=0`), within the timeout.
- T-GIT-3 a hung git (stub `git` that sleeps) is killed at the timeout and reported.
- T-GIT-4 a path argument starting `-` is treated as a path (`--` separator), not an option.

**probe**
- T-PRB-1 plain directory → `inside: false`.
- T-PRB-2 repo top → `top`, `branch`, `head`.
- T-PRB-3 subdirectory of a repo → `top` is the repo top.
- T-PRB-4 detached HEAD → `detached: true`, base is the sha.
- T-PRB-5 bare repo → refused with reason.
- T-PRB-6 submodules and `.gitattributes` with `filter=lfs` → warnings set.
- T-PRB-7 the probe performs no writes (snapshot of `.git` mtime and file list unchanged).

**slug**
- T-SLG-1 title "Fix the login bug!" → `fix-the-login-bug`.
- T-SLG-2 unicode and emoji titles → ascii slug or pane-id fallback.
- T-SLG-3 an existing `corral/x` → `x-2`.
- T-SLG-4 names git refuses (`..`, `.lock`, trailing `/`, `@{`) never reach git.
- T-SLG-5 length capped at 40.
- T-SLG-6 a user-supplied branch not starting `corral/` is prefixed.

**create**
- T-CRT-1 creates the worktree under the root, on `corral/<slug>`, at `base_sha`.
- T-CRT-2 record fields all present and absolute.
- T-CRT-3 the main checkout's files, index and HEAD are unchanged.
- T-CRT-4 a dirty main checkout: worktree starts from HEAD; the dirt stays in main.
- T-CRT-5 two threads creating from one repo at once both succeed with distinct branches (lock works).
- T-CRT-6 a root that is a symlink to elsewhere is refused.
- T-CRT-7 a pre-existing target path is never reused or overwritten.
- T-CRT-8 git older than 2.25 (stub) → clear refusal.
- T-CRT-9 creation timeout leaves no half-registered worktree (`worktree list` clean or pruned).

**summary and status**
- T-SUM-1 no changes → zeros.
- T-SUM-2 modified, added, deleted, renamed files counted correctly.
- T-SUM-3 untracked files are counted; the worktree's real index is byte-identical before and after.
- T-SUM-4 committed and uncommitted changes are both counted against `base_sha`.
- T-SUM-5 `ahead` counts agent commits; `behind` counts commits added to base afterwards.
- T-SUM-6 binary files counted as changed with no line counts.
- T-SUM-7 the temp index file is removed even when git fails.
- T-SUM-8 runs with `GIT_OPTIONAL_LOCKS=0`: a concurrent agent `git add` never sees `index.lock` contention from us.

**diff**
- T-DIF-1 unified output parses; paths are worktree-relative.
- T-DIF-2 per-file cap truncates with `truncated: true`; total stays ≤ 2 MiB.
- T-DIF-3 more than 500 files → list complete, hunks only for the first 500.
- T-DIF-4 binary → named, no hunks.
- T-DIF-5 the digest changes when any byte of any file changes, and when HEAD moves.
- T-DIF-6 the digest is stable across two calls with no change.
- T-DIF-7 a symlink in the worktree pointing outside it is shown as a symlink change, never followed.
- T-DIF-8 user `diff.external` / `color.ui=always` config does not leak into output.
- T-DIF-9 file names with spaces, quotes and newlines round-trip (`-z` where needed).

**commit**
- T-CMT-1 commits all changes with the given message and the user's identity.
- T-CMT-2 a stale digest → refused, nothing committed.
- T-CMT-3 empty message → refused.
- T-CMT-4 a failing pre-commit hook → refused with the hook's output; no `--no-verify`.
- T-CMT-5 nothing to commit → a clear no-op answer.

**merge**
- T-MRG-1 clean merge into a clean main checkout on base: `--no-ff` commit, `status=merged`, `merged_sha` set.
- T-MRG-2 main checkout dirty → 409 `dirty_main`, nothing changed.
- T-MRG-3 main checkout on another branch → 409 `not_on_base`.
- T-MRG-4 conflicting change on base → preview reports the files; merge refused with `conflict`; main untouched.
- T-MRG-5 a merge that fails mid-way (hook) is aborted; main returns to its pre-merge HEAD and clean state.
- T-MRG-6 stale digest → refused.
- T-MRG-7 git < 2.38 → Merge unavailable, reason given.
- T-MRG-8 uncommitted worktree changes → merge refused until committed ("Commit first").
- T-MRG-9 base fast-forwardable is still merged `--no-ff` (one visible merge commit per agent branch).

**publish**
- T-PUB-1 push to a local bare remote creates the branch there with upstream set.
- T-PUB-2 a non-fast-forward remote branch → refused; never force.
- T-PUB-3 no `origin` → clear refusal naming `git remote add`.
- T-PUB-4 `gh` absent → compare URL returned for a GitHub remote, plain message otherwise.
- T-PUB-5 stub `gh` signed out → refusal naming `gh auth login`.
- T-PUB-6 stub `gh` success → `pr_url` stored on the record and shown.
- T-PUB-7 PR title and body go to `gh` via argv and stdin, never a shell string (a title with `$(…)` is literal).

**remove**
- T-RMV-1 clean merged worktree → directory and branch gone.
- T-RMV-2 uncommitted changes → refused without `force`.
- T-RMV-3 unmerged commits → refused unless confirm equals the branch name.
- T-RMV-4 a record whose branch does not start `corral/` → refused, always.
- T-RMV-5 a record whose path is outside the root (tampered meta) → refused, always.
- T-RMV-6 the path is a symlink → refused.
- T-RMV-7 directory already deleted by the user → branch handling still correct, prune run.
- T-RMV-8 no code path in the module calls `shutil.rmtree` or `os.remove` on worktree content (AST test).

**reconcile**
- T-REC-1 a healthy set → no notes.
- T-REC-2 worktree dir deleted outside the hub → `missing`, reported once.
- T-REC-3 branch deleted outside the hub → reported.
- T-REC-4 hub-made worktrees with no pane → listed as orphans.
- T-REC-5 a user's own worktrees (not under root) → never listed, never pruned.
- T-REC-6 main repo moved or deleted → every record for it marked missing, hub keeps running.

**lifecycle (fake lane)**
- T-LIF-1 `worktree` survives `save_meta` → `from_meta` round-trip.
- T-LIF-2 a meta without `worktree` loads exactly as before (regression).
- T-LIF-3 snapshot exposes the record without `common_dir`.
- T-LIF-4 `create(worktree=True)` starts the agent with cwd = worktree (fake agent reports its cwd).
- T-LIF-5 roster full → refused before any branch is cut.
- T-LIF-6 agent start fails → pane removed, branch kept, error names the path.
- T-LIF-7 `Manager._lock` is never held during a git call (instrumented lock, the `NestCheckLock` pattern).
- T-LIF-8 cwd not a repo with `worktree=True` → 400 with reason.
- T-LIF-9 twelve panes on one repo created concurrently → twelve distinct branches, no errors.
- T-LIF-10 fake agent `write` then turn end → one `worktree` event with the new counts.
- T-LIF-11 no git runs on the 5 s observe tick (count calls over 30 s idle).
- T-LIF-12 summary refresh failure emits a note, never kills the pane.
- T-LIF-13 hub restart: pane returns detached with its record; resume runs in the worktree.
- T-LIF-14 worktree dir missing at restore → pane detached, `missing`, resume refused with the reason.
- T-LIF-15 reconcile runs off the request path (restore returns before it finishes).
- T-LIF-16 kill -9 of the hub during `create` → on restart, no pane and either no worktree or an orphan listed.
- T-LIF-17 port moves the record to the new pane, pauses the source.
- T-LIF-18 the source pane cannot resume into the handed-off worktree.
- T-LIF-19 porting a non-worktree pane is unchanged (regression).
- T-LIF-20 close keeps the worktree; reopen restores it.
- T-LIF-21 forget without removal keeps it and lists it as an orphan.
- T-LIF-22 forget with removal follows T-RMV rules.
- T-LIF-23 resume after the base advanced still works (no automatic rebase).
- T-LIF-24 two panes can never own one worktree (second claim refused).
- T-LIF-25 `host:` lane with `worktree=True` → refused.
- T-LIF-26 Ollama with `worktree=True` → refused.

**routes**
- T-RTE-1 probe returns the probe dict for a repo.
- T-RTE-2 probe on a non-directory → 400.
- T-RTE-3 probe never writes (as T-PRB-7, through HTTP).
- T-RTE-4 diff route returns files, digest, summary.
- T-RTE-5 diff route caps response size at 2 MiB.
- T-RTE-6 diff for a pane without a worktree → 400.
- T-RTE-7 unknown pane → 400.
- T-RTE-8..11 commit, merge, publish, discard each refuse a stale digest with 409 and change nothing.
- T-RTE-12..15 each succeeds with a fresh digest and emits a `worktree` event.
- T-RTE-16 merge 409 bodies carry the machine-readable `reason`.
- T-RTE-17 every new route → 401 without the cookie.
- T-RTE-18 every new POST → 403 with a foreign Origin.
- T-RTE-19 `/api/worktrees` lists live, archived and orphan records.
- T-RTE-20 request bodies over 1 MiB are refused before parsing (existing `MAX_BODY`).

**browser logic (Node)**
- T-UI-1 the probe row is hidden for non-repos, `host:` and Ollama.
- T-UI-2 the checkbox state is remembered per repo top.
- T-UI-3 `common.worktree` is sent only when checked and visible.
- T-UI-4 submodule/LFS/dirty warnings render from the probe.
- T-UI-5 the pill text for a summary (`⎇ fix-login · 4 files +120 −8 ↓3`).
- T-UI-6 `headSignature` changes when any shown summary field changes and not otherwise.
- T-UI-7 `.meta` shows the repo path, not the worktree path.
- T-UI-8 `parseUnified` on fixtures: add, delete, rename, mode change, binary, no-newline-at-end, CRLF.
- T-UI-9 hunk line numbers are correct on a 3-hunk fixture.
- T-UI-10 text is only ever set via textContent (no `innerHTML` in the review code path; source-contract test).
- T-UI-11 a truncated file shows the notice.
- T-UI-12 file filter narrows the list.
- T-UI-13 feedback on a hunk quotes path, header and selected lines into the composer.
- T-UI-14 the dialog's actions post the digest it rendered.
- T-UI-15 Merge disabled with reason when `merge.ok` is false.
- T-UI-16 Push & PR shows remote and branch in the confirm text.
- T-UI-17 Discard requires the typed branch when unmerged.
- T-UI-18 a 409 response refreshes the diff and shows the reason.
- T-UI-19 a `worktree` SSE event updates `p.worktree.summary` and schedules one render.
- T-UI-20 rail card shown when digest ≠ seen and state your-turn; hidden after opening review.
- T-UI-21 the review rail card does not count toward `blocked` (tab not "hot").
- T-UI-22 palette "Branches" row present and lists orphans.
- T-UI-23 `r` opens review only when focus is not a text field.

**visual (headless Chromium, before merge)**
- T-VIS-1 pill readable and not clipped at 1600, 1100, 420 px.
- T-VIS-2 review dialog at 1600 px: file list and diff side by side, no horizontal page scroll.
- T-VIS-3 review dialog at 420 px: full-screen, file list collapses above the diff.
- T-VIS-4 a 5,000-line diff scrolls without the page freezing (time to first paint < 500 ms).
- T-VIS-5 ink, parchment and nocturne themes: add/del colours meet 4.5:1 on their backgrounds.
- T-VIS-6 the rail card renders like the existing `.ncard` variants.
- T-VIS-7 New conversation dialog with the probe row, in one-pane and multi-pane walls.
- T-VIS-8 screenshots saved beside the run for the PR description.

**isolation guard**
- T-ISO-1 no test writes under the real `~/.local/share/corral-light` or creates a branch in any repo it did not create.

### 5.3 Lane matrix (manual, P0 and P4)

On a scratch repo with a small Python project and a failing test:

| Step | Claude | Codex | Grok | Gemini |
|---|---|---|---|---|
| Start with Own branch | | | | |
| Agent fixes the test; edits land only in the worktree | | | | |
| Agent runs the test suite inside the worktree | | | | |
| Agent commits (record result; Codex expected to fail, D5) | | | | |
| Review shows the diff; Commit from review | | | | |
| Merge into main | | | | |
| Push & PR to a throwaway GitHub repo | | | | |
| Pause, restart hub, resume: still on the branch | | | | |
| Port to another lane: new pane continues on the same branch | | | | |

Plus one parallel run: three panes, three lanes, same repo, three different
tasks; all three merged in sequence, the third after a conflict that its
agent resolves on request.

### 5.4 Gate to ship

1. All automated tests green, including the full existing suite.
2. Lane matrix complete with no "edits landed outside the worktree".
3. T-ISO-1 and T-RMV-4..8 green (the safety rules are the release blockers).
4. One week of dogfood with no lost work.
5. Panel synthesis filed and every accepted finding either fixed or listed as deferred with a reason.

## 6. Risks

| Risk | Likelihood | Effect | Mitigation |
|---|---|---|---|
| A lane escapes the worktree (walks up to the main repo) | medium | edits in the user's checkout, the exact thing this prevents | P0 lane matrix; header warns if a `tool` event's `locations` fall outside the worktree (cheap check on existing data) |
| Codex cannot commit (sandbox) | high | agent errors on commit | D5: hub commits on review; Phase 0 checks writable-roots option |
| Missing gitignored deps make agents fail | high | slow first turn | hint in dialog; v1.1 copy list |
| Disk growth from many worktrees | medium | GBs on large repos | Branches view shows size; Forget offers removal; doctor warns over a threshold |
| User deletes a worktree dir by hand | medium | stale record | reconcile marks missing; prune for hub paths only |
| Merge into a checkout the user is editing | low after D6 | lost edits | refuse unless clean and on base |
| Cleanup bug deletes user work | low | severe | D9, path and branch guards, AST test against rmtree |
| git hooks slow or interactive | medium | timeouts | timeouts, `GIT_EDITOR=true`, hook output shown on failure |
| Hub killed mid-operation | low | half-made worktree, `index.lock` | create is git-atomic enough; reconcile lists orphans; merge aborts on next attempt if `MERGE_HEAD` exists |
| Large repos make summary slow | medium | sluggish UI | off request path, cached by HEAD and index mtime, P0 timing |

## 7. Open questions for the panel

1. Is a worktree under the hub's state dir right, or should it sit beside the
   repo (`<repo>/../<repo>.corral/<slug>`) where editors and tools find it?
2. Should Merge happen in the main checkout (D6), or should the hub update the
   base ref directly when the base is not checked out anywhere?
3. Is the temporary-index approach the right way to include untracked files,
   or is `git add -N` in the agent's own index acceptable?
4. One owner per worktree (F8) or allow a second pane to join read-only?
5. What is missing from the test catalogue that would let lost work slip through?
