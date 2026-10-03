FIX FIRST

Two proven ship-bar violations remain: a retired drain dispatches during a review action, and a valid Git remote alias bypasses destination confirmation.

**BLOCKING FINDINGS**

1. **High — PROVEN: retired drain bypasses the review hold.**  
   Location: [sessions.py:1481](/home/cvande/.cache/corral-bugbash-2026-10-03-r5/astra/corral-light/sessions.py:1481), also lines 1484–1496 and 1561.  
   **Trigger:** A drain pauses after completing its turn; the user runs `/clear`, opens Review, and types another message while Review holds the pane.  
   **Failure:** The old drain reads the *new* attachment’s generation on its next iteration and ignores `held`. The new registry gate permits dispatch because the branch is healthy. The repro demonstrates a real fake-ACP agent writing `during-review.txt` while `p.held` remains true inside `worktree_snapshot`. This directly violates the action/dispatch gate.  
   **Minimal fix:** Bind each drain worker to its original generation; check generation and hold under `_turn_lock` before dequeuing, and revalidate generation before dispatch after the Git checks. A retired worker must leave the replacement attachment’s state alone.  
   **Repro:** [scratch/repro_drain.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r5/astra/scratch/repro_drain.py).

2. **High — PROVEN: remote-name parsing still permits an unconfirmed push destination.**  
   Location: [worktrees.py:1409](/home/cvande/.cache/corral-bugbash-2026-10-03-r5/astra/corral-light/worktrees.py:1409), reaching `push` at line 1465.  
   **Trigger:** Configure `origin` with URL `release\u00a0mirror`—containing a non-breaking space—and configure that valid remote name with an identical fetch URL but a different `pushurl`.  
   **Failure:** Python’s `.split()` divides the remote name, so the name check misses it. `ls-remote --get-url` returns the confirmed alias unchanged. `rewrite_rule()` therefore returns `None`, although Git resolves that alias’s push destination to `unconfirmed.git`. The repro reaches the actual push call with the redirecting alias; it intercepts execution before any push. Real `git remote get-url --push` verifies the different destination.  
   **Minimal fix:** Parse remote names using Git’s literal newline delimiter, preserving Unicode whitespace; refuse failed or truncated enumeration. Add this counterexample to `BugBash5Publish`.  
   **Repro:** [scratch/repro_destination.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r5/astra/scratch/repro_destination.py).

**NON-BLOCKING**

- All 66 `BugBash*` regression tests passed, including `BugBash3Publish`, `BugBash4Publish`, and `BugBash5Publish`; neither counterexample is covered. [Test log](/home/cvande/.cache/corral-bugbash-2026-10-03-r5/astra/scratch/regressions.log).
- The prescribed 320-test run produced 303 passes, 16 errors, and one failure; all unsuccessful cases traced to sandbox-denied sockets, so they are not product findings.
- Production code was unchanged; repro scripts are confined to this seat’s scratch directory.

**ONE SENTENCE:** The most plausible first-week failure is queued text unexpectedly running during Review after Craig clears a pane near turn completion.
