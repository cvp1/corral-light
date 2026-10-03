I’ll check the prior repros and round 10 fixes, then probe Publish’s destination checks and run the tests in the required isolated environment.The earlier failures have regression tests, and the isolated suite is running. I’m checking a remaining destination-resolution edge: whether Git can interpret a literal URL as a legacy remote file that the guard misses.The suite finished: 342 tests, with 16 errors and one failure, all from sandbox-denied sockets. The BugBash3/4/5 Publish tests and round 10 regressions passed. I’m now checking whether crash recovery can settle an operation while that operation is still running.I reproduced a gap in `verify()`: it accepts both an index symlink to the main checkout and a worktree-local `core.worktree` pointing there. I’ll check whether normal Git commands from the pane then overwrite main-checkout work, and save a repro.Both routing probes caused loss: `git restore` from the pane overwrote an uncommitted main-checkout file, and `git add -A` made a main-checkout staged blob unreachable from the index and all refs. Both passed the dispatch gate first. I’m checking one final race around the action lock before writing the verdict.FIX FIRST

The identity gate still accepts repository-local redirection that makes ordinary Git commands from an own-branch pane destroy main-checkout work. Three findings are reproduced.

**BLOCKING FINDINGS**

1. **High · PROVEN — Effective worktree escapes identity verification.**  
   [worktrees.py:919](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/corral-light/worktrees.py:919). Enable `extensions.worktreeConfig` and set the pane’s worktree-local `core.worktree` to the main checkout. Both `verify()` and the actual dispatch predicate accept it. Running `git restore --worktree -- a` from the pane then overwrites the main checkout’s uncommitted file; the pane’s file remains untouched. No global configuration or environment change is involved.

   **Minimal fix:** require Git’s effective `--show-toplevel` to equal the registered worktree before admitting actions or dispatch.

   **Repro:** [repro_r11_routing.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/scratch/repro_r11_routing.py), `test_core_worktree_overwrites_main_uncommitted_file`.

2. **High · PROVEN — A symlinked index routes writes into the main checkout.**  
   [worktrees.py:1032](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/corral-light/worktrees.py:1032), with the missing identity check in `verify()`. Replace the pane’s admin index with a symlink to the main checkout’s index. Git resolves that symlink before `_read_index()` receives the path, so the new no-follow reader accepts the destination. Verification and dispatch both pass. An ordinary `git add -A` from the pane overwrites main-checkout staging. The repro confirms previously staged unique content disappears from the index and is unreachable from every ref, with no recovery ref created.

   **Minimal fix:** verify that the effective index remains inside the registered worktree admin directory; checking only the already-resolved file’s type is insufficient.

   **Repro:** the same script, `test_symlink_index_loses_main_staged_content`.

3. **Medium · PROVEN — Overlapping Review bypasses an `unknown` operation.**  
   [sessions.py:2587](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/corral-light/sessions.py:2587). Registry and identity checks precede acquisition of `_action_lock`. Suspend Review after those checks; let Commit acquire the lock, move the branch, fail its index replacement, and record `unknown`; then release Review. Review acquires the lock and succeeds, writing its snapshot ref while the commit remains `unknown`.

   **Minimal fix:** reread the entry and repeat the operation/identity gates after acquiring the action lock, before executing the action.

   **Repro:** [repro_r11_gate.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/scratch/repro_r11_gate.py). Uses production Commit and Review methods, a deterministic thread barrier, and an injected index-replacement failure.

**NON-BLOCKING**

- Prescribed suite: **342 tests; 325 passed**. All 16 errors and one failure stemmed from sandbox-denied sockets.
- Named Publish suites, BugBash6 tests, and round 10 regression tests passed; they miss the cases above.
- [Evidence and isolated commands](/home/cvande/.cache/corral-bugbash-2026-10-03-r11/astra/scratch/README.md) are saved; production source is unchanged.

**ONE SENTENCE:** The likeliest first-week problem is an agent changing Git’s checkout configuration while troubleshooting, after which Corral still admits commands that affect Craig’s main checkout.
