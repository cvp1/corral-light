I’ll check the prior findings against the fixes, run the isolated tests, and probe the action gates and recovery paths for ship-blocking failures.The new guards cover the original resume and `/clear` races. I’m now checking whether the guards still hold when pane state changes between checks, and whether start and resume verify the worktree’s current branch. The isolated suite is still running.Three targeted repros now fail: initial startup can race Discard and leave a live agent writing in trash; resume allows writes on a different branch even after reconciliation reports the mismatch; and opening review with a running turn plus queued input leaves the pane stuck busy. The prescribed suite finished with 284 passes and 17 failures/errors caused by socket denial.The push-destination fix also has two reproducible gaps: an empty `insteadOf` prefix, and a URL that Git interprets as another remote’s name. Read-only local checks resolve to the unconfirmed repository; I intercepted both push calls before execution. The stuck-pane repro also confirms that subsequent permission requests arrive but never appear in the UI.DO NOT SHIP

Discard can still leave a live agent writing in trash. Four proven findings meet the ship bar.

**BLOCKING FINDINGS**

1. **High · PROVEN — Initial startup escapes Discard’s action lock.**  
   **Location:** [sessions.py:2146](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/corral-light/sessions.py:2146), `sessions.py:2482`.  
   **Trigger:** Open review and Discard while the first agent attachment is starting. Unlike resume and `/clear`, initial `start()` holds no action lock; review actions also accept `state="starting"`.  
   **Failure:** Deterministic thread barriers let startup complete immediately after Discard’s final process scan. The real ACP process survives the move. Its `/proc` cwd points into trash, and a subsequent direct ACP prompt writes `after-discard.txt` there.  
   **Minimal fix:** Serialize initial attachment with the same action lock, recheck registry state inside it, and refuse review actions while starting.  
   **Repro:** [repro_r3.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/scratch/repro_r3.py), `R3.test_initial_start_can_survive_discard`.

2. **High · PROVEN — A busy-review refusal permanently drops the live agent’s callbacks.**  
   **Location:** [sessions.py:2490](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/corral-light/sessions.py:2490), `sessions.py:1616`, `sessions.py:788`.  
   **Trigger:** An agent has a running turn and queued input; the user opens review.  
   **Failure:** The busy check refuses before establishing a hold, but `finally` still calls `release_hold(False)`. Parking the queue increments `_generation`, invalidating the running attachment’s event and permission callbacks. The completed turn leaves the pane busy. A subsequent real permission request reaches ACP but produces **zero visible cards**, leaving the agent waiting indefinitely until its attachment is restarted.  
   **Minimal fix:** Release only a hold actually established; parking queued messages on a live attachment must preserve its generation and active turn.  
   **Repro:** `R3.test_busy_refusal_strands_pane` in the script above; [permission evidence](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/scratch/repro_r3_busy.log).

3. **High · PROVEN — Confirmed push destinations still permit redirection.**  
   **Location:** [worktrees.py:1397](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/corral-light/worktrees.py:1397), `worktrees.py:1443`.  
   **Triggers:** Two independently reproduced variants:
   - Git accepts an empty `insteadOf` prefix. The new `if prefix` skips it: confirmation shows `dst/r.git`, while Git resolves that argument to `dst/dst/r.git`.
   - An origin URL of `destination` passes validation, but Git interprets that argument as another configured remote named `destination`, selecting its unconfirmed URL.

   **Evidence:** Real local `ls-remote` calls return a marker unique to the unconfirmed repository. Both library calls pass every guard and reach the corresponding push invocation, intercepted **before execution**; no reproduction push or network access occurred.  
   **Minimal fix:** Recognize empty rewrite prefixes and reject remote-name indirection, ensuring the confirmed destination is the literal destination Git will use.  
   **Repro:** [repro_r3_urls.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/scratch/repro_r3_urls.py), `test_empty_rewrite_prefix` and `test_remote_name_indirection`.

4. **High · PROVEN — Resume permits an agent to write on a different branch.**  
   **Location:** [sessions.py:2395](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/corral-light/sessions.py:2395), [worktrees.py:2073](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/corral-light/worktrees.py:2073).  
   **Trigger:** Pause an own-branch pane, switch its worktree to another branch, reconcile, then resume/send.  
   **Failure:** Reconciliation explicitly reports the branch mismatch but leaves the entry active. The shared resume/dispatch guard checks cwd containment without checking symbolic HEAD. The real agent resumes and writes on `foreign-branch` while its registered branch remains `corral/<id>`.  
   **Minimal fix:** Block attachment and dispatch on live worktree-identity mismatches; reconciliation must retain a blocking identity error.  
   **Repro:** `R3.test_resume_on_another_branch` in [repro_r3.py](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/scratch/repro_r3.py).

**NON-BLOCKING**

- **PROVEN:** `fit_review()` only shrinks diff rows; a large `too_big` inventory still returns **2,362,998 bytes** against its **2,097,152-byte** cap (`repro_r3_urls.py:test_fit_review_metadata`).
- Existing BugBash coverage misses initial-start concurrency, attachment-generation damage on refusal, both destination variants, and oversized snapshot metadata.
- Prescribed suite: **301 tests, 284 passes**; 16 errors and one failure arose from sandbox-denied sockets, not established product defects. Exact repro commands and evidence notes are in [scratch/README.md](/home/cvande/.cache/corral-bugbash-2026-10-03-r3/astra/scratch/README.md).

Craig’s most likely first-week failure is opening review while a turn and queued message are pending, then finding the pane stuck and subsequent permission cards invisible.
