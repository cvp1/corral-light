# Astra (gpt-6-astra, high) — own-branch bug bash

pane 186c5a4f0d8c · model gpt-6-astra · effort high · complete True · wall 570s · needs-you 0s

I’ll read the spec and lifecycle code, run the isolated tests, then reproduce the highest-risk failures in throwaway repos. I’ll keep the clone’s committed code unchanged and avoid the running hub and network.
The commit and recovery code has several safeguards, including temporary indexes and compare-and-swap branch updates. I’m now checking the gaps between those steps, especially restart recovery and whether Git configuration can change a confirmed push destination.
The Node selftest passed. Of 211 Python tests, 195 passed; 15 could not open loopback sockets in the sandbox, and the isolation test failed because its child run hit the same restriction. Those are environment limits, not product defects. I also found that restart recovery can replace the index without checking whether someone staged new work while the hub was down; I’m reproducing that next.
I reproduced staged-work loss both after a real SIGKILL/restart and during a controlled concurrent Git staging operation. In both cases, the staged-only blob became unreachable from every ref and reflog. I also confirmed that the “unknown outcome” warning does not block review actions, and that a Unicode diff can exceed the promised 2 MiB response cap. Next I’m checking Discard against the real send/resume path.
Discard has a reproduced race: a normal send can resume the pane after its process scan, leaving a live agent in the moved worktree. A later message then wrote a new file inside `.trash`. The existing test inserts directly into the queue, so it misses this resume path. I also reproduced a crash between marking Discard “done” and saving its trash location; restart classified the files as an orphan, and CLI Restore refused them.
**DO NOT SHIP — reproduced staged-work loss during commit recovery and a Discard race that leaves an agent writing into trash.**

**FINDINGS**

Reproduction scripts: [repro.py](/home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/scratch/bugbash/repro.py) and [repro_lifecycle.py](/home/cvande/.cache/corral-bugbash-2026-10-02/corral-light/scratch/bugbash/repro_lifecycle.py). Run from the clone root with the prescribed isolated `HOME` and `TMPDIR`; each creates its own scratch repository.

1. **high · PROVEN — Commit reconciliation overwrites newly staged work.**  
   **Location:** `worktrees.py:1134`, `worktrees.py:1608`.  
   **Trigger:** SIGKILL after `commit:ref_moved`; while the hub is down, stage a new version and then change the working file again. Restart reconciliation replaces that index without comparing it against its pre-crash identity. The same overwrite occurs when an external Git operation stages work between commit validation and index reconciliation.  
   **Evidence:** `repro.py restart_index` and `repro.py live_index`. Both replace `unique staged content` with `reviewed`; the staged blob becomes unreachable from all refs and reflogs, with no recovery ref protecting it from GC.  
   **Minimal fix:** Journal the expected index identity; acquire `index.lock` before validating and replacing the index. Preserve unexpected staged content and stop recovery instead of overwriting it.

2. **high · PROVEN — Sending during Discard can restart the agent inside the eventual trash directory.**  
   **Location:** `sessions.py:1320`, `sessions.py:2341`.  
   **Trigger:** Discard stops the agent and sets the pane to `detached`. A normal send arriving after the process scan calls `resume()` before checking the queue hold. The resumed agent survives the move.  
   **Evidence:** `repro_lifecycle.py discard_send` uses a real fake-ACP process and inserts a normal `send()` immediately after the real scan. Discard returns with registry phase `trashed`, pane state `ready`, and a live client. A subsequent message successfully writes `AFTER_DISCARD.txt` inside `.trash`. This new work is absent from the discard recovery ref.  
   **Minimal fix:** Serialize resume/start/clear against review actions; check the hold before implicit resume. Require an active, unblocked registry entry before every dispatch.

3. **high · PROVEN — Confirmed push URLs can be rewritten to another repository.**  
   **Location:** `worktrees.py:1256`, `worktrees.py:1266`, `worktrees.py:1276`.  
   **Trigger:** Configure chained rewrites: `alias:` → repository A, and A → repository B. `remote get-url --push` supplies A for confirmation, but passing A to Git applies another rewrite and selects B. Verification uses the same rewritten argument.  
   **Evidence:** `repro.py url_rewrite`. The displayed destination is `confirmed.git`; Git resolves that argument to `unconfirmed.git`. An actual read-only `ls-remote` against the confirmed argument returns a marker existing only in B. The push call’s exact argument path is established by the cited code; no reproduction push was performed.  
   **Minimal fix:** Refuse destinations subject to another rewrite, or isolate both publication and verification from rewrite configuration after binding the destination.

4. **high · PROVEN — The selected subdirectory can launch the agent outside its worktree.**  
   **Location:** `worktrees.py:757`, `sessions.py:1952`.  
   **Trigger:** HEAD contains `pkg` as a symlink outside the repository, but the main checkout has an uncommitted replacement of that symlink with a real directory. Select that directory for Own branch. Probe accepts it; the new checkout restores HEAD’s symlink.  
   **Evidence:** `repro.py subdir_escape`: probe returns no refusals, but `agent_cwd(entry).resolve()` points to the external scratch directory. Manager passes this unchecked path to the agent.  
   **Minimal fix:** Resolve and validate the final agent cwd against the registered worktree immediately before every start/resume.

5. **high · PROVEN — “Unknown outcome” does not block actions or an already-running pane.**  
   **Location:** `sessions.py:2249`, `sessions.py:1306`, `sessions.py:2183`.  
   **Trigger:** An operation reaches `unknown`. Only resume consults the blocking reason; `_worktree_action()` and normal sends do not. Pending `intent` operations also do not block resume during background reconciliation.  
   **Evidence:** `repro.py unknown` successfully commits through `_worktree_action()` while the registry reports an unknown operation. `repro_lifecycle.py unknown_send` makes a real agent write another file in that state.  
   **Minimal fix:** Enforce registry phase and operation-state checks for actions and dispatch, using fresh state under the relevant lock; keep panes blocked until restart reconciliation finishes.

6. **high · PROVEN — Discard’s final journal writes have an uncovered crash window.**  
   **Location:** `worktrees.py:1504`, `worktrees.py:1696`.  
   **Trigger:** Crash after marking the move operation `done`, before saving `phase="trashed"` and `trash_path`. Restart examines only `intent` operations, then checks the obsolete source path.  
   **Evidence:** `repro.py discard_done` performs a real SIGKILL at that boundary. Reconcile changes the entry to `missing`, reports the surviving trash directory as an orphan, and Restore refuses: `this worktree is not in trash`. Files survive, but supported recovery loses their association. Restore has the analogous two-write pattern at lines 1520–1521.  
   **Minimal fix:** Atomically save operation completion and the resulting registry phase/location in one registry mutation.

7. **medium · PROVEN — Git’s timeout does not cover inherited output pipes.**  
   **Location:** `worktrees.py:215`, `worktrees.py:222`.  
   **Trigger:** The immediate process exits, while a child retains stdout/stderr. `proc.wait()` completes, then the reader-thread joins wait indefinitely outside the timeout. A hook or helper can therefore strand a review action and its held queue.  
   **Evidence:** `repro.py runner_timeout`: a stub exits after spawning `sleep`; a 0.1-second wrapper timeout fails to fire, and an independent one-second watchdog interrupts the blocked join.  
   **Minimal fix:** Apply one deadline to stdin delivery, process completion, and pipe draining; terminate the process group when any stage exceeds it.

8. **medium · PROVEN — The encoded review response exceeds its 2 MiB cap.**  
   **Location:** `worktrees.py:1061`, `worktrees.py:1087`, `hub.py:249`.  
   **Trigger:** Several Unicode text files consume less than the raw patch budget but expand during default JSON escaping. File metadata and inventories are also outside that budget.  
   **Evidence:** `repro.py json_cap` produces **2,942,842 encoded bytes**, exceeding 2,097,152 even with `truncated=True`.  
   **Minimal fix:** Budget the complete encoded response, including metadata and escaping; truncate or paginate before sending.

**TEST GAPS**

- Extend T-CMT/T-CRS coverage with external staging after validation and between SIGKILL and restart; assert every displaced staged blob remains reachable.
- Replace T-RMV-14’s direct `_queue.append()` with actual `Pane.send()` calls at discard boundaries; assert no process survives in trash and later sends refuse.
- Kill between operation completion and registry phase/location updates. Current crash points miss the reproduced Discard window.
- Exercise commit and normal prompt dispatch with `unknown` and unresolved `intent` operations.
- Test chained URL rewrites, including the destination used by post-publication verification.
- Assert the byte length of the complete serialized review response using Unicode patches and large inventories.

**CHECKED AND FOUND SOUND**

- Node `selftest_review.mjs` passed. The requested Python run had **195 passes, 15 errors, and one failure**: socket creation was sandbox-denied in the HTTP tests; the isolation test failed because its child encountered those same errors.
- Ordinary snapshot/commit tests protect the main checkout’s index and use temporary indexes for reviewed trees.
- Branch compare-and-swap, stale-review refusal, and preservation of staged divergence already present at review passed their tests.
- Routing-environment sanitization, normal slug collision handling, and standard identity checks passed.
- Normal discard/restore and typed purge preserve recovery refs; no automatic pruning was found.
- Review rendering uses text construction; cookie and same-origin checks precede worktree POST dispatch.
- macOS lanes default to disabled pending platform validation; forced-platform tests do not establish real macOS lane safety.

Typing while Discard is running is the bug most likely to bite Craig in the first week: it can revive the agent and send subsequent work into trash.
