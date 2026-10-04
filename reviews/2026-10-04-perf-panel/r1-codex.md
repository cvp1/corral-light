I’ll read the full plan first, then trace the hand-off and browser paths and check its numbers against the code. I’ll keep this read-only and offline.The code supports removing expensive state work, but two proposed interactions need correction: the lightweight availability request would omit `agents`, and `_await_ready` uses a sequence sentinel that would prevent event-based long-poll wakeups. The plan also treats HTTP wait time as CPU time without evidence.**AMEND — PROVEN:** the direction is useful, but the proposed delta and long-poll contracts conflict with existing callers, and the measurements do not establish the claimed CPU savings.

## DISCONFIRMING EVIDENCE

- **PROVEN — reproduced arithmetic:** 2,250 polls × 68 ms = **2.55 minutes of elapsed request time**, not CPU time. The 130.5 MB estimate follows from 58 KB per response. Ollama’s socket wait releases the GIL; neither CPU consumption nor GIL occupancy follows from HTTP latency (`ollama_acp.py:25–27`). **SUSPECTED:** state serialization competes materially with reader threads, but its CPU share needs separate measurement.

- **PROVEN — independently checked transcript:** `reviews/2026-10-02-own-branch-bugbash/r1-grok.raw:17–18` gives 4,341.6 s total and 2,566.8 s needs-you: **42.78 minutes blocked, 1,774.8 s residual**. This supports a large permission stall. **SUSPECTED:** the residual is entirely model time. `wall_s` also includes polling, transport and cancellation; permission duration is sampled from state (`consult.py:329–436`).

- **PROVEN:** the claimed panel startup improvement, 10 s → 3 s, is **3.33×**, not 10×. The stated trivial-turn overhead target, 3.5 s → under 1.5 s, also does not establish 10×.

- **PROVEN:** “cached and refresh off-request” is too broad. A cold `lane_probe.probe()` executes a handshake synchronously (`lane_probe.py:31–41,69–77`). Expired `claude_auth.status()` synchronously reads credentials (`claude_auth.py:140–146`). **SUSPECTED:** these cause significant cold-start or expiry stalls on the reviewed deployment.

- **PROVEN:** P2’s lightweight availability read contradicts P1: it needs `agents`, which P1 omits without `full=1` (`consult.py:272–280`). Also, `since={}` defaults every pane’s event cursor to zero (`sessions.py:2730–2733,1861`); it does not suppress transcript history.

- **PROVEN:** “broadcast never blocks a producer” only means it avoids blocking queue insertion. It acquires a lock and drains an overflowing subscriber’s backlog synchronously (`corral_core/sessions.py:1081–1102`).

- **SUSPECTED:** the 37K-token context is the largest contributor to Claude’s four-second response. One pong observation cannot distinguish context processing, vendor queueing and inference. The stale-process diagnosis likewise cannot be independently verified from this clone.

## MISSED FRICTION

- **PROVEN:** ACP notifications execute application callbacks directly on the stdout reader (`corral_core/acp.py:240–255,296–309`). Tool events synchronously serialize, write and flush transcript records (`sessions.py:1153–1161`; `corral_core/sessions.py:649–674`). Worktree guards also resolve filesystem paths there (`sessions.py:1649–1668`). **SUSPECTED:** slow storage or large tool payloads delay subsequent RPC responses more than polling does. Measure callback duration; preserve event ordering if persistence moves to a bounded worker.

- **PROVEN:** accepted sends call `fsync` (`ledger.py:54–62,70–83`). **SUSPECTED:** storage tails materially affect hand-off latency. Preserve durable acceptance, but measure it separately.

- **PROVEN:** `/session/new` blocks its handler through creation and handshake (`hub.py:604–625`; `sessions.py:1225–1269`). `session/new` can wait 120 s (`corral_core/acp.py:386–391`). Parallel opening still leaves a barrier before prompts if implemented around the existing fanout phases (`consult.py:574–586`). **SUSPECTED:** one hung adapter would continue delaying all healthy arms. Each arm should open **and send** independently.

- **PROVEN:** the observer performs synchronous automatic resumes (`hub.py:106–124`; `sessions.py:2035–2037`), including initialize/load (`sessions.py:913–935`). Moving Ollama probing into that same thread adds another blocker. **SUSPECTED:** availability and liveness freshness can degrade far beyond five seconds. Use separate bounded workers.

- **PROVEN:** every appended SSE event changes `logSignature`; the browser replaces the transcript DOM and reads layout dimensions (`static/app.js:837–842,877–895`). Frame coalescing does not avoid rebuilding during sustained streaming. **SUSPECTED:** this dominates wall responsiveness. Measure complete frame duration, layout and all-pane work, not only `renderLog`.

## PER-ITEM VERDICT

These verdicts are recommendations; evidence is labeled individually.

| Item | Verdict |
|---|---|
| **P0** | **ADOPT conditionally. SUSPECTED:** stale runtime is the reported cause. Verify loaded version and confirm surfacing after restart; allow for detached-session resume costs. |
| **P1 availability cache** | **AMEND. PROVEN:** `create()` checks files, not current vendor/server health (`sessions.py:2188–2211`). Serve cache immediately, refresh independently, expose checked time/unknown state, and invalidate after spawn failures. Ten seconds is not a freshness guarantee when underlying probes have their own caches. |
| **P1 archive cache** | **AMEND. PROVEN:** parent-directory mtime does not track child `meta.json` changes. Invalidate on **reopen** too: it changes roster membership and closed metadata (`sessions.py:2388–2420`). Use an explicit generation; retain reconciliation for external changes. |
| **P1 delta omission** | **AMEND. PROVEN:** `Object.assign` preserves existing pane fields, but new panes have no previous values; `updatePane` reads `config`, and commands events carry only a count (`static/app.js:847,2766–2775,2882–2885`). Include initial pane metadata and explicit metadata versions. Preserve complete roster semantics and version global data. `_pane_record` reads model/effort directly, so those fields need not require full state (`consult.py:440–448`). |
| **P1 long-poll** | **AMEND. PROVEN:** pane count does not bound HTTP waiter count. Add admission limits, per-target filtering, atomic predicate-check/wait, roster/global generations and disconnect handling. Recheck cookies before responding and at the 30-second boundary, matching SSE (`hub.py:554–561`). **SUSPECTED:** global `notify_all` makes every streaming event wake every arm, defeating near-zero load. |
| **P1 HMAC cache** | **ADOPT. PROVEN:** each `_secret()` reads the key (`auth.py:42–47`). Define rotation behavior; keep priority low. |
| **P2 parallel opens** | **AMEND:** independent open-and-send pipelines, bounded concurrency and per-arm startup failure reporting. **PROVEN:** the current fanout waits for all opens before sending (`consult.py:574–586`). |
| **P2 lightweight availability** | **REJECT as specified. PROVEN:** it omits the required `agents` and still returns history. Use a dedicated cached capabilities endpoint. |
| **P2 consult long-poll** | **AMEND. PROVEN:** `_await_ready` uses `since=1<<40`, so an event-past-cursor predicate cannot wake normally (`consult.py:456`). Fetch current state immediately, then wait on actual sequence/state generations. Bound wait by remaining deadline and leave HTTP timeout margin; transport failure currently cancels the turn (`consult.py:424–431`). |
| **P2 seat long-poll** | **AMEND. PROVEN:** wait records drive reply admission (`corral_core/sessions.py:1439–1451,1715–1723`). Track active requests with cleanup and a bounded lease; merely enlarging the heartbeat window retains stale waits after disconnection. |
| **P2 asynchronous close** | **AMEND. PROVEN:** close already removes roster membership before stopping (`corral_core/sessions.py:1794–1810`), and shutdown can exceed three seconds (`corral_core/acp.py:442–464`). Add a closing registry counted against process capacity, serialize reopen/forget, retain persisted PID identity until reaped, and drain workers on shutdown. `stop()` writes closed metadata after teardown, which could overwrite a concurrent reopen (`corral_core/sessions.py:861–867`). |
| **P2 streaming/quorum** | **ADOPT with amendments. PROVEN:** `_parallel` waits for all workers (`consult.py:496–514`). Publish completion immediately; define successful-arm quorum, failures, interruption and ownership of remaining turns. |
| **P3** | **DEFER. PROVEN:** cwd and seat credentials enter `session/new`, after initialize (`sessions.py:1233–1236`). Standby adoption must also handle spawn-time environment, callbacks and process accounting. |
| **P4** | **AMEND:** measure whole frames and refresh bursts first, then incremental rendering. **PROVEN:** seen POSTs occur only when sequence advances (`static/app.js:2729–2733`); they are not unconditional per-second traffic. |
| **P5** | **ADOPT as an experiment. SUSPECTED:** savings require controlled lean-versus-normal measurements. Preserve the operator’s explicit configuration choices. |

## SEQUENCING

**SUSPECTED — recommended order:** verify runtime and restart recovery; instrument CPU versus wait time, reader callbacks and browser frames; remove request-path probes; implement coherent metadata/delta contracts; publish independent arm results; then add bounded, targeted long-poll and asynchronous teardown.

Cut the global-condition design and sentinel-based waits. Defer seat changes until that path is exercised. P3 is unlikely to justify its lifecycle complexity for a measured initialize saving of roughly one second.

**SUSPECTED:** P1/P2’s “idle hub load near zero” will under-deliver most because globally waking waiters on every streamed event converts polling into repeated state rebuilding.