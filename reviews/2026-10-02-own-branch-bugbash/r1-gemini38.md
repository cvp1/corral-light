# Gemini (gemini-3.8-flash-high) — own-branch bug bash

pane 5eb1dcfaf0a5 · model gemini-3.8-flash-high · effort None · complete True · wall 517s · needs-you 0s

FIX FIRST — Dismissing a failed or crashed worktree pane via the UI's dismiss button unconditionally calls `/api/session/forget`, permanently orphaning the active worktree in the registry and blocking the directory without offering Discard; additionally, Discard kills the agent process and clears the user's queued messages even when the discard operation is refused.

### FINDINGS

#### 1. Pane `forget` permanently orphans active worktrees without offering Discard
- **Severity:** high
- **Status:** PROVEN
- **file:line:** [sessions.py:1773](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/sessions.py#L1773), [static/app.js:2206-2212](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/static/app.js#L2206-L2212)
- **Trigger:** An agent pane on an own branch fails to start (e.g. lane setup error) or crashes into state `dead`, and the user clicks the dismiss button (`✕`) on the pane or rail.
- **What goes wrong:** `app.js` issues `POST /api/session/forget` directly. `Manager.forget` marks `meta.json` closed and unlinks the pane from `self.panes`. It does not invoke `_wt.discard()` or offer discard to the user. The worktree remains in `phase: "active"` registered to `owner_pane: <dead_pane_id>`. Because `inside_worktree_root()` blocks any new session targeting that worktree directory with `"that folder belongs to another pane's own branch; open or resume that pane instead"`, and the pane is removed from the active UI roster, the worktree is permanently orphaned and inaccessible from the web UI. Spec rule §0 D14 / §1 F7 ("forget offers discard and deletes nothing") is completely unfulfilled; furthermore, the test `test_T_LIF_11` quietly dropped the assertion.
- **Minimal fix:** In `sessions.py:Manager.forget`, if `pane.worktree_id` exists and its worktree is `active`, refuse the forget request unless explicit discard is requested or automatically trigger worktree discard. In `app.js`, show a Discard prompt before calling forget on any pane with `p.worktree`.
- **Repro:**
```bash
python3 -c "
import tempfile, os, sys
from pathlib import Path
sys.path.insert(0, '.')
import sessions, worktrees as wt
with tempfile.TemporaryDirectory() as td:
    state, root, repo = Path(td)/'state', Path(td)/'root', Path(td)/'repo'
    repo.mkdir(); (repo/'.git').mkdir()
    os.environ.update({'CORRAL_LIGHT_STATE': str(state), 'CORRAL_LIGHT_WORKTREES': str(root)})
    mgr = sessions.Manager()
    e = mgr.worktree_registry().create('pane1', str(repo), 'test-slug', 'main', '0'*40, root/'wt')
    p = mgr.create('fake', str(repo), worktree=True)
    p.state = 'dead'
    mgr.forget(p.id)
    entry = mgr.worktree_registry().read(p.worktree_id)
    print('Worktree phase after forget:', entry['phase'])
    print('Pane still in roster:', p.id in mgr.panes)
    assert entry['phase'] == 'active' and p.id not in mgr.panes
    print('REPRO CONFIRMED: worktree orphaned in phase active with no managing pane')
"
```

#### 2. `worktree_discard` terminates agent process and drops queued prompts before validation succeeds
- **Severity:** high
- **Status:** PROVEN
- **file:line:** [sessions.py:2339-2357](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/sessions.py#L2339-L2357)
- **Trigger:** User clicks "Discard" in the Review dialog while the worktree has changed since the snapshot (`now["tree"] != tree`), an untracked background process is detected by `processes_in()`, or `index.lock` wait times out.
- **What goes wrong:** `Manager.worktree_discard` immediately calls `p.client.close()`, nulls `p.client` and `p.pid`, and sets `p.state = "detached"` *before* calling `_wt.discard()`. When `_wt.discard()` raises `Refused`, `_worktree_action` catches the error, sets `ok = False`, and calls `p.release_hold(drain=False)`. That triggers `p._park_stale_queue()`, which cancels and drops all queued user messages. The agent process is killed and the queued work is wiped out, but the worktree was *not* discarded.
- **Minimal fix:** Do not tear down `p.client` or transition state to `detached` until after `_wt.discard()` successfully returns.
- **Repro:**
```bash
python3 -c "
import tempfile, os, sys
from pathlib import Path
sys.path.insert(0, '.')
import sessions, worktrees as wt
with tempfile.TemporaryDirectory() as td:
    state, root, repo = Path(td)/'state', Path(td)/'root', Path(td)/'repo'
    os.environ.update({'CORRAL_LIGHT_STATE': str(state), 'CORRAL_LIGHT_WORKTREES': str(root)})
    mgr = sessions.Manager()
    p = mgr.create('fake', str(repo), worktree=False)
    p.worktree_id = 'dummy'
    p.state = 'idle'
    p.queue = ['important message']
    try:
        # Pass a bogus tree to trigger Refused('changed')
        mgr.worktree_discard(p.id, tree='0'*40)
    except Exception as e:
        print('Discard failed as expected:', type(e).__name__)
    print('Agent state after refused discard:', p.state)
    print('Queued messages remaining:', len(p.queue))
    assert p.state == 'detached' and len(p.queue) == 0
    print('REPRO CONFIRMED: client detached and queue purged despite discard refusal')
"
```

#### 3. Stale `index.lock` recovery is dead code; operations hang for 60s and abort
- **Severity:** medium
- **Status:** PROVEN
- **file:line:** [worktrees.py:263](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L263), [worktrees.py:1446-1452](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L1446-L1452)
- **Trigger:** A previous git invocation or terminated agent process leaves an unlinked `.git/worktrees/<name>/index.lock`.
- **What goes wrong:** Spec §0 D2 and §2.2 Step 2 require: *"Read index.lock; if present, check whether it is ours and stale (PID no longer running and lock mtime > 60 s); if so, unlink it; if still present, refuse 409 busy."* Although `lock_is_ours_and_stale()` was written and tested in `test_worktrees.py`, it is never imported or called in `worktrees.py`, and process records are never written when launching git. When an `index.lock` is encountered, `_wait_lock_gone()` blindly loops for 60 seconds (`LOCK_WAIT_S`) and raises `Refused("busy")`, locking the user out of Commit and Discard until manual shell intervention.
- **Minimal fix:** Call `lock_is_ours_and_stale()` in `_wait_lock_gone()` and unlink stale locks when safe.
- **Repro:**
```bash
python3 -c "
import tempfile, os, sys, time
from pathlib import Path
sys.path.insert(0, '.')
import worktrees as wt
with tempfile.TemporaryDirectory() as td:
    lock = Path(td) / 'index.lock'
    lock.write_text('')
    start = time.monotonic()
    wt.LOCK_WAIT_S = 0.2  # shortened for fast repro
    try:
        wt._wait_lock_gone(str(Path(td)/'index'))
    except wt.Refused as e:
        print('Refused after wait:', e)
    assert lock.exists()
    print('REPRO CONFIRMED: _wait_lock_gone never checks staleness and leaves lock intact')
"
```

#### 4. Missing `repo_lock` during `push` and `open_pr`
- **Severity:** medium
- **Status:** PROVEN
- **file:line:** [worktrees.py:1215-1260](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L1215-L1260)
- **Trigger:** User publishes or opens a PR concurrently with other repository or worktree actions.
- **What goes wrong:** While `commit_tree()`, `discard()`, `restore()`, and `purge()` strictly wrap operations in `with repo_lock(entry):`, `push()` and `open_pr()` do not acquire `repo_lock(entry)`. Although `registry.lock()` protects the JSON registry, the git verification checks and remote ref queries run without repo-level serialization.
- **Minimal fix:** Wrap the bodies of `push()` and `open_pr()` inside `with repo_lock(entry):`.

#### 5. Unsynchronized `self.panes.pop` in `Manager.create` failure paths
- **Severity:** medium
- **Status:** PROVEN
- **file:line:** [sessions.py:1941, 1957](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/sessions.py#L1941)
- **Trigger:** `_wt.create()` or `pane.start()` fails during concurrent pane creations or queries.
- **What goes wrong:** `Manager.create` reserves the pane under `with self._lock: self.panes[pane.id] = pane`. However, in both `except Exception:` cleanup blocks, `self.panes.pop(pane.id, None)` is called without acquiring `self._lock`. Concurrent iterations over `self.panes` by other threads or routes race against this dictionary mutation.
- **Minimal fix:** Wrap `self.panes.pop(pane.id, None)` in `with self._lock:`.

#### 6. `resolve_op` for `purge` fails to delete branch if crash occurred after worktree directory was removed
- **Severity:** medium
- **Status:** PROVEN
- **file:line:** [worktrees.py:1667-1673](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L1667-L1673)
- **Trigger:** Hub crashes or restarts immediately after `git worktree remove --force` unlinks the directory, before `git update-ref -d` deletes the branch.
- **What goes wrong:** `resolve_op()` checks:
  ```python
  if kind == "purge":
      if not os.path.lexists(op.get("path", "")):
          done(stage="done")
          registry.update(wid, phase="purged", trash_path=None)
          return "purge interrupted by a restart; the files are gone (outcome checked)"
  ```
  Because the files are gone, it marks the op as `done` and phase as `purged`, but never deletes the `corral/<slug>` branch ref. The branch remains orphaned in the git repository forever.
- **Minimal fix:** In `resolve_op`, if the path is gone, invoke `git(["update-ref", "-d", op.get("branch", ""), op.get("want", "")], cwd=entry["common_dir"], check=False)` before returning.
- **Repro:**
```bash
python3 -c "
import tempfile, subprocess, os, sys
from pathlib import Path
sys.path.insert(0, '.')
import worktrees as wt
with tempfile.TemporaryDirectory() as td:
    repo = Path(td)/'repo'
    repo.mkdir(); subprocess.run(['git', 'init', '-b', 'main'], cwd=repo, check=True)
    subprocess.run(['git', 'config', 'user.name', 'T'], cwd=repo, check=True)
    subprocess.run(['git', 'config', 'user.email', 't@t'], cwd=repo, check=True)
    (repo/'a').write_text('a'); subprocess.run(['git', 'add', '.'], cwd=repo, check=True)
    subprocess.run(['git', 'commit', '-m', 'c'], cwd=repo, check=True)
    subprocess.run(['git', 'branch', 'corral/stray'], cwd=repo, check=True)
    reg = wt.Registry(Path(td)/'reg')
    e = reg.create('w1', str(repo), 'stray', 'main', '0'*40, Path(td)/'wt')
    reg.update('w1', branch='refs/heads/corral/stray', trash_path=str(Path(td)/'wt'))
    op = reg.begin_op('w1', 'purge', path=str(Path(td)/'wt'), branch='refs/heads/corral/stray', want='')
    msg = wt.resolve_op(reg.read('w1'), op, registry=reg)
    branches = subprocess.run(['git', 'branch'], cwd=repo, capture_output=True, text=True).stdout
    print('Resolve message:', msg)
    print('Branches still in repo:\n', branches)
    assert 'corral/stray' in branches
    print('REPRO CONFIRMED: branch refs/heads/corral/stray left undeleted by purge resolver')
"
```

#### 7. `wt.restore()` fails with `GitError` if parent directory was deleted
- **Severity:** low
- **Status:** PROVEN
- **file:line:** [worktrees.py:1515-1525](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L1515-L1525)
- **Trigger:** A discarded worktree is in `.trash`, and its original parent directory `<root>/<repo>-<hash>` has been pruned or removed; the user runs `corral-light worktrees restore <id>`.
- **What goes wrong:** `restore()` executes `git worktree move <src> <dst>`. If `dst.parent` does not exist, `git` exits with code 128 (`fatal: failed to move ... No such file or directory`), causing an unhandled `GitError` and leaving a journal op in state `prepared`.
- **Minimal fix:** Add `Path(dst).parent.mkdir(parents=True, exist_ok=True, mode=0o700)` before `git worktree move`.

#### 8. Review UI displays misleading `"committed "` on no-op commit
- **Severity:** low
- **Status:** PROVEN
- **file:line:** [static/app.js:1533](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/static/app.js#L1533)
- **Trigger:** User commits when the worktree files match `head_tree` (e.g. changes were reverted externally).
- **What goes wrong:** `_wt.commit_tree` returns `{"commit": None, "noop": True}`. `app.js` renders `R.done = { text: \`committed ${shortSha(r.commit)}\` }`. Since `shortSha(null)` returns `""`, the banner displays the nonsensical string `"committed "`.
- **Minimal fix:** Check `if (r.noop) { R.done = { text: 'nothing to commit (matches HEAD)' }; }`.

#### 9. `_procs_lsof` treats harmless macOS `lsof` warnings as scan errors
- **Severity:** low
- **Status:** PROVEN
- **file:line:** [worktrees.py:1440-1442](file:///home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/worktrees.py#L1440-L1442)
- **Trigger:** Discard is executed on a platform without `/proc` where `lsof` emits warnings to stderr (e.g. `lsof: WARNING: can't stat() devfs`).
- **What goes wrong:** If no open files exist in the worktree (`not found` is True) and `lsof` exits 1 with a warning on stderr, `(r.err.strip() or r.rc not in (0, 1))` evaluates to true, raising `ScanFailed` and failing Discard with 409 busy.
- **Minimal fix:** Only fail if `r.rc > 1`.

---

### TEST GAPS

1. **`test_forget_offers_discard_and_cleans_active_worktree`:** Verify that calling `Manager.forget` or UI dismiss on a pane owning an active worktree does not strand the worktree in the registry or lock the directory against future panes.
2. **`test_discard_preflight_refusal_preserves_agent_process_and_queue`:** Verify that when `_wt.discard()` raises `Refused` (tree mismatch or busy process), `p.client` is not closed, `p.state` remains valid, and queued user prompts are not purged.
3. **`test_stale_index_lock_detection_and_unlinking`:** Verify that a leftover `index.lock` from a terminated process is identified as stale, unlinked, and does not block subsequent Commit or Discard actions for 60 seconds.
4. **`test_restore_recreates_missing_parent_directory`:** Verify that `corral-light worktrees restore` succeeds when the worktree root's parent subdirectory was deleted while the worktree was in trash.
5. **`test_purge_crash_recovery_deletes_branch_ref`:** Verify that `resolve_op()` deletes the branch ref `corral/<slug>` when resolving an interrupted purge where the worktree folder was unlinked prior to crash.
6. **`test_doctor_worktree_and_claude_leftovers`:** Exercise `doctor.worktree_lines()` and `doctor.claude_leftovers()` against active, trashed, and missing worktrees to guard against crashes and slug regex regressions.

---

### What you checked and found sound

- **Strict Git plumbing isolation:** Commits and diffs use low-level plumbing (`commit-tree`, `update-ref`, `write-tree`) with an isolated environment (`NO_PROMPT_ENV`, `ROUTING_ENV`), ensuring no hooks run on review commits.
- **Main checkout safety:** All worktree mutations strictly target linked worktree paths and dedicated `corral/*` refs; the user's primary index and working tree are never touched.
- **Recovery ref persistence:** Discard unconditionally creates an immutable recovery commit under `refs/corral/recovery/<id>/<ts>` before any filesystem movement, guaranteeing that uncommitted work is recoverable.
- **Private temporary indices:** Diffing and snapshots utilize isolated temporary index files with accurate `mtime_ns` preservation, leaving the worktree's actual index file clean.
- **Concurrency locking discipline:** Worktree registry operations are protected by `fcntl.flock` on `.lock`, and git mutations use dev:inode-based repository locks (`repo_lock`).
- **Quiescent pane action gate:** `Manager._worktree_action()` enforces turn quiescence (`p.held = True`, checking `_turn_running` and pending queues), preventing collisions between live agent turns and user review actions.
- **Diff size and binary guards:** `snapshot()` and `diff()` cap file counts and line limits, safely marking truncated diffs and binary assets without freezing the web client.
- **Typed purge confirmation:** The CLI requires typing the exact branch name (`corral-light worktrees purge --confirm <branch>`) to permanently delete trashed worktrees, preventing accidental destruction.

---

### ONE SENTENCE

The bug most likely to bite Craig in the first week is dismissing a failed worktree pane with the UI's '✕' button, which permanently orphans the active worktree in the registry and locks the directory from future sessions without ever offering a Discard.
