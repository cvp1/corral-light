1. **AMEND**. The plan correctly identifies destructive polling and synchronous bottlenecks but fundamentally misdiagnoses the cause of browser lockups and proposes a `since={}` payload optimization that will accidentally download every pane's entire history.

2. **DISCONFIRMING EVIDENCE**
* **SUSPECTED**: The plan attributes "a little slow in responding" (the "10x improvement" goal) to the hub's 420 KB state endpoint and 2.5 CPU-minutes of GIL overhead per 1500 seconds. While inefficient, HTTP GET overhead does not cause front-end interface hangups. The actual latency is purely browser-side rendering (see Missed Friction below).
* **PROVEN** (`consult.py:192` and `hub.py:280`): The plan claims passing `since={}` to `open_pane` will fetch a "light" state. In `sessions.py`, passing an empty dictionary for `since` means the client implicitly declares sequence 0 for all panes. The hub will dump `MAX_EVENTS` (up to 4,000 events) for *every* pane, drastically increasing blocking I/O and bandwidth. The client must either use `since={"*": 1<<40}` or a dedicated `/api/lanes` route.
* **PROVEN** (`hub.py:100` and `ollama_acp.py:16`): A 10s cache for `available_agents()` is redundant. `unavailable_reason()` blocks for up to `CONNECT_TIMEOUT=5`. If the plan runs this heavy check on the 5s edge-triggered observer tick (`_observe_once`), state requests should instantly access the tick's variable (`_TICK`); a secondary 10s cache only creates overlapping staleness tracking. 

3. **MISSED FRICTION**
* **O(N²) Browser DOM Rebuilds on Stream (PROVEN)**: In `app.js` (`updatePane`, near line 2580), the client calls `rec.log.replaceChildren(...renderLog(p).childNodes)` every time `logSignature` changes. Because `TEXT_FLUSH_S` flushes stream chunks every 150ms, a transcription of 4,000 events forces parsing 4,000 markdown elements (`md()`) and repeatedly thrashing the DOM multiple times per second. This completely exhausts the browser's main thread and is the true root cause of wall sluggishness.
* **Serial `/api/state` requests in fanout (PROVEN)**: In `consult.py:246`, even if `fanout` spawns threaded waits, it iterates over `lanes_` and calls `open_pane()` serially. `open_pane` calls `lanes()`, triggering a full, synchronous `/api/state` HTTP fetch *before* each thread is dispatched. A 3-lane fanout hits the heavy state endpoint three sequential times. 
* **`cwd_suggestions()` Blocking I/O (PROVEN)**: In `sessions.py:309`, this function runs synchronous directory scans (`iterdir()` and `.is_dir()`) over the user's home folder. Even if omitted from delta polls, any `full=1` state request (or an empty `since` typo) will unnecessarily block the hub's GIL with heavy disk I/O.

4. **PER-ITEM VERDICT**
* **P0 — restart the hub**: **ADOPT**. The `needs_you` stalling bug in background panes (commit e8e7763) is already deployed to disk but requires a memory flush to take effect.
* **P1 — make a state request nearly free**: **AMEND**.
  - *10s cache*: Reject. Serve directly from the 5s observer tick.
  - *Delta omits*: Adopt. The risk here is that if `consult._pane_record` relies on a delta poll returning fields like `model`, an omit will break it. Ensure `consult.py`'s `_pane_record` polls explicitly pass `full=1`.
  - *Long-poll*: Adopt. The `ThreadingHTTPServer` thread count risk is safe because waiting clients are bound by `MAX_PANES`. The 30s session cookie re-check risk (blocking while holding an expiring cookie) is acceptable—if the session expires mid-wait, it will simply HTTP 401 on the subsequent reconnect. The `Condition` wake-up fanout from `broadcast()` is a minor thundering-herd since pollers are tightly constrained to ~10 concurrent users.
* **P2 — the consult and panel path**: **AMEND**.
  - *fanout & open_pane*: Amend. Refactor `consult.py` to execute `lanes()` exactly *once* at the top of `cmd_fanout`, then pass the cached map natively to parallel threads. Do not rely on `since={}`.
  - *Async close*: Amend. Ensure pane teardowns do not delete `meta.json` or clear `pid` on disk until the daemon teardown actually completes (`sessions.py:804`). If `close` wipes the meta before the process dies and the hub restarts immediately after, the orphan-reaping logic won't realize it needs to kill a zombie process.
  - *`seat_wait` long-poll*: Adopt. This correctly bypasses the tight 1s `time.sleep()` loop.
* **P3 — spawn latency**: **REJECT**. Saving 0.55s of initialization time is statistically invisible against standard panel times (e.g., 350s for Gemini and 2000s for Grok). Attempting to pre-boot agents creates unjustifiable orphan tracking and credential routing complexity.
* **P4 — browser (measure first)**: **AMEND**. Do not measure first. Implement incremental DOM appends for open tails immediately constraint, as full-history parsing is inherently destructive to UI frame pacing. Batching `markSeen` is PROVEN correct: `app.js:2728` contains a `for` loop that spams an individual `POST` request per pane.
* **P5 — Claude lane context**: **ADOPT**. Shredding 37K tokens of ambient project settings massively drops proxy ingest latencies for Claude's first requested token.

5. **SEQUENCING**
1. **P0** (Restart Hub): Safest, zero-code patch for immediate unblocking.
2. **P4** (Browser incremental append and batched `markSeen`): This immediately solves the wall interface locking without server adjustments.
3. **P2** (Parallel consult fanout, pulling the lane registry check outside the loop).
4. **P1** (Long-poll, delta omission, and tick-driven agent tracking).
5. **P5** (Lean Claude context config).
*(Drop P3 completely).*

6. **ONE SENTENCE**
P3 (Warm standby adapters) is most likely to under-deliver, because saving half a second of process initialization provides zero noticeable benefit on panel tasks dominated by hundreds or thousands of seconds of model generation.