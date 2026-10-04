# Performance review — handoffs and responsiveness (2026-10-04)

Measured on the Mac host against the live hub (pid 83781, two Claude panes open),
plus the panel transcripts under `reviews/`. Numbers are from real requests,
not estimates, unless marked.

## TL;DR

- **The live hub is running stale code.** It started 2026-10-03 11:33; the
  deployed `hub.py`, `sessions.py` and `static/app.js` were updated at 18:00
  the same day (commits f2d3092 and e8e7763, "bulk spawns start minimized
  and surface when they need you"). The worst recorded handoff hang (43
  minutes blocked on a permission card in a hidden pane) is the case that
  commit addresses. A restart is required for it to take effect. **Restarting
  kills every open pane, including the one this review was written from**, so
  it is proposed, not done.
- **"Handoff" in practice means `consult`.** Across all 101 pane directories on
  disk there are zero `peer` events: the seat tools (`seat_send`/`seat_wait`)
  are not what panels use. Every panel arm goes through `consult.py`.
- **Corral's own overhead on a trivial Claude-lane handoff is about 3.5 s of a
  7.5 s round trip**, the rest is the model. On panels, model time is
  hundreds to thousands of seconds per arm and Grok is consistently the long
  pole (2 to 4 times slower than Codex or Gemini). No hub change moves that.
  What Corral can change is *when the operator gets to read* the fast arms.
- **A 10x is available on everything Corral controls**: hub cost per state
  request (68 ms to under 5 ms), bytes per poll (58 KB to under 3 KB), turn-end
  detection latency (about 1 s average to near zero), panel start-up (about
  10 s to about 3 s), and hub CPU burned while arms are thinking (about 10% of
  a core to near zero). End-to-end panel wall time will not drop 10x, and this
  document says so rather than promising it.

## What was measured

| Probe | Result |
|---|---|
| `/health` | 1 to 2 ms |
| `/api/state` full, 2 panes | 72 to 191 ms, 420 KB (one pane's 4000-event ring is 472 KB of it) |
| `/api/state` delta (`since` = current seqs) | 49 to 111 ms, 58 KB with 2 panes; 45 ms p50 and 95 KB with 5 panes (`testkit/perf_probe.py --n 10`, during the review panel). Full: 64 ms, 897 KB |
| of which `available_agents()` | 45 to 68 ms every call; the Ollama probe inside it is a live HTTP GET, 32 to 58 ms, with a 5 s connect timeout: every state request stalls up to 5 s while Ollama is down or restarting |
| of which `archived()` | 2 to 30 ms (reads ~100 `meta.json` files) |
| per-pane static payload sent on every response | `commands` 16 KB + `config` 2 KB per pane; `catalog` 9 KB; `archived` 6 KB |
| Claude lane, prompt "reply pong" via the API | `session/new` 2985 ms, send 2 ms, first text and `turn_end` at +4.08 s, close 452 ms, **7.5 s total** |
| context carried into that trivial turn | 37,171 tokens (user+project+local settings, 98 commands, MCP schemas) |
| spawn phases, Claude (offline) | initialize 550 ms, `session/new` 2180 ms, `set_config` 130 ms = 2.9 s; close 2.1 s |
| spawn phases, Codex | 659 + 2241 + 4 ms = 2.9 s; close 0.5 s |
| spawn phases, Grok | 1121 + 289 ms = 1.4 s; close 0.5 s |
| consult poll interval | 2.0 s (`POLL_S`), each poll a full-overhead `/api/state` |
| seat_wait poll interval | 1.0 s, and `PEER_WAIT_SEEN_S` = 5 s makes a reply's queueing depend on that heartbeat |

Panel arms, from `reviews/*/*.raw` (wall seconds per arm):

| Panel | Gemini | Codex | Grok |
|---|---|---|---|
| container-install v1/v2 r1 | 15, 17 | 120, 146 | 324, 460 |
| worktree r1/r2 | 32, 22 | 144, 149 | 420 (lost), 536 |
| own-branch bugbash r1 | 517 | 570 | **4342, of which 2567 s blocked on a permission card** |
| own-branch bugbash r2 to r12 | 325 to 599 | 365 to 797 | 1262 to 2285 |

Also in those logs: two Codex arms died with `session/prompt: Internal error`
(r6, r12) and two Grok arms were lost to "another prompt was sent to this
pane" (an operator poke mid-turn ends the consult attribution).

## Friction sources, ranked by evidence

1. **Stale hub process.** Code on disk is six and a half hours newer than the
   running process. Nothing below matters until the hub is restarted.
2. **Permission cards in hidden panes.** The 43-minute `needs_you` stall. The
   fix shipped (e8e7763) but is not running. Secondary mitigation: panels
   should run read-only unless the charge needs edits (`--posture` is already
   plumbed in `consult`).
3. **The slowest arm gates the whole panel.** `consult fanout` and the
   author's pattern of one `ask` per lane both return only when every arm is
   done. Grok at 1300 to 2300 s hides Gemini at 350 s.
4. **Every poll pays the full state price.** `MGR.state()` runs
   `available_agents()` (Ollama HTTP probe, three launcher checks),
   `archived()` (100 file reads) and `cwd_suggestions()` on every call, and
   ships 16 KB of `commands` per pane plus catalog and archive on every
   delta. A three-arm panel waiting 1500 s makes about 2250 such calls, about
   2.5 minutes of request latency and about 130 MB of JSON. **Correction
   after measurement (panel round 1):** this is wall time, not CPU. The hub
   used 0.8% of a core during the panel wait (CPU-time delta over 30 s; 55 s
   of CPU in 19 h of uptime). Most of the 68 ms is the Ollama socket wait,
   which releases the GIL. The cost is per-request latency and bytes, not
   hub CPU; the browser-side rebuild (P4) is the better candidate for "a
   little slow in responding".
5. **Polling instead of waiting.** Consult detects `turn_end` up to 2 s late
   (1 s average); `seat_wait` 1 s late, and its "a wait is in flight" rule
   depends on a 5 s heartbeat window that a slow hub can miss, turning a
   reply into a `busy` refusal.
6. **Serial pane spawn in fan-out.** `open_pane` is called in a loop; each
   costs about 3 s plus a 420 KB `/api/state` just to read lane availability.
   A three-arm panel waits about 10 s before its first prompt leaves.
7. **Synchronous close.** `/api/session/close` returns only after SIGTERM,
   a 3 s wait, and reaping. 0.5 to 2 s per arm, serial, on the consult side.
8. **Claude lane context.** 37K tokens before the first word on every turn,
   from the adapter's hard-coded `settingSources: ["user","project","local"]`
   plus 98 commands and the MCP schemas. This is the largest term in Claude's
   4 s time-to-first-token for "pong". It is the operator's configuration by design,
   not Corral code; noted so the number is not blamed on the hub.

Checked and found sound: `broadcast()` never blocks a producer
(`put_nowait`, drop-and-resync on overflow); `create()` holds the manager
lock only to reserve the slot; `request()` wakes on the event, not the 5 s
poll; `lane_probe` and `claude_auth` are cached and refresh off-request; the
browser rebuilds a pane's log only when its signature changes and coalesces
renders to one per frame; the SSE path is push, not poll.

## Plan

### P0 — restart the hub onto the deployed tree (no code)

Propose-only: it ends this session's pane and the other open pane. Afterwards
re-run the bugbash-style permission scenario once to confirm a background
pane surfaces when it needs you.

### P1 — make a state request nearly free (hub)

| Change | Where | Expected |
|---|---|---|
| Cache `available_agents()` for 10 s; move the Ollama HTTP probe and launcher checks onto the 5 s observer tick, edge-triggered | `sessions.py`, `hub.py` | 45 to 68 ms off every request; no request ever waits on Ollama |
| Cache `archived()` keyed on the panes directory mtime; invalidate on close/forget | `corral_core/sessions.py` | 2 to 30 ms off every request |
| Delta responses (any `since`) omit per-pane `commands` and `config` unless changed, and omit `catalog`, `agents`, `archived`, `cwdSuggestions`, `schedule` unless `full=1`; the browser's reconnect `refresh()` asks `full=1` | `hub.py`, `sessions.py` `state()`, `static/app.js` `refresh()` | 58 KB to under 3 KB per poll |
| Long-poll: `/api/state?since=…&wait=N` (N capped at 30) blocks on a manager `Condition` that `broadcast()` notifies, returning as soon as any pane has an event past `since` | `corral_core/sessions.py`, `hub.py` | detection latency about 1 s to under 50 ms; idle pollers go from 0.5 req/s to 1 req per 30 s |
| Cache the HMAC key in-process (`auth._secret()` reads the key file per request) | `auth.py` | sub-millisecond; tidy-up |

Falsifier: a `perf_probe.py` under `testkit/` that records `/api/state` delta
p50 latency and bytes before and after. Targets: under 5 ms, under 3 KB.

### P2 — the consult and panel path

| Change | Where | Expected |
|---|---|---|
| `fanout` opens panes in parallel threads | `consult.py` | three-arm start about 10 s to about 3 s |
| `open_pane` checks lane availability with a light `/api/state` (`since={}` and no `full`) | `consult.py` | one 420 KB read per arm gone |
| `wait_turn` and `_await_ready` use `wait=25` long-poll instead of `sleep(POLL_S)` | `consult.py` | turn end seen within tens of ms; hub load while waiting near zero |
| `seat_wait` uses long-poll on `/api/peer/turn`; `PEER_WAIT_SEEN_S` becomes `wait + margin` so a blocked long-poll is itself the in-flight wait | `corral_core/seat_mcp.py`, `corral_core/sessions.py` | reply queueing no longer depends on a 1 s heartbeat |
| `/api/session/close` removes the pane from the roster at once and tears the process down on a daemon thread; `forget` joins or waits for that teardown | `corral_core/sessions.py`, `sessions.py` | 0.5 to 2 s per arm off the consult side |
| `fanout --stream` prints each arm as JSON lines the moment it completes; `--min-arms N` returns once N arms are done and leaves the rest running (reported by pane id) | `consult.py` | the author reads Gemini and Codex at about 400 s instead of waiting for Grok at about 2000 s |

Falsifiers: time-to-first-prompt for a three-lane fan-out (target under 4 s);
consult overhead on a trivial prompt excluding model time (3.5 s now, target
under 1.5 s with P1+P2).

### P3 — spawn latency (optional, gated on P1+P2 results)

A warm standby per recently used lane: one adapter process spawned and
`initialize`d ahead of demand, adopted by the next `create()` on that lane.
Saves only the initialize phase (0.55 to 1.1 s of 2.9 s) because `session/new`
depends on the pane's cwd and its freshly minted seat token. Pre-running
`session/new` in a predicted cwd would save the other 2.2 s but holds a live
37K-token session, complicates orphan accounting, and guesses wrong whenever
the cwd differs. Recommendation: do 3a only if panels still feel slow after
P1+P2; skip 3b.

### P4 — browser (measure first)

Add a `?perf` flag that logs `renderLog` time per pane rebuild. If a long,
streaming transcript shows rebuilds above about 16 ms, switch the open tail
(events after the last `turn_end`) to incremental append and keep the closed
turns' DOM. Also batch `markSeen` into one POST carrying every pane's seq
instead of one POST per pane per second.

### P5 — Claude lane context (flag, not a change)

A "lean" pane option could point the adapter's user-level settings at a
minimal Corral-owned config dir, cutting the 37K-token floor. Worth a
decision from the operator; it changes what Claude knows in a pane, so it is not a
performance tweak.

## Risks and what would make this wrong

- Long-poll holds one handler thread per waiter. `ThreadingHTTPServer` is
  one thread per connection already; waiters are bounded by pollers (at most
  `MAX_PANES` arms plus browsers) and `wait` is capped at 30 s.
- A 10 s `agents` cache can show a lane as available for up to 10 s after it
  stopped being so; `create()` re-checks the files it needs, so the failure is
  a clear error, not a wrong spawn.
- Async close: a `close` followed at once by `forget` of the same pane must
  not race the process teardown; `forget` waits on the teardown future.
- Delta responses that omit fields rely on the browser's `Object.assign(prev,
  np)` keeping prior values, which it does today; `consult._pane_record`
  needs `full=1` for model and effort.
- The 10x claim holds for hub cost, bytes, detection latency, panel start-up
  and idle CPU. It does not hold for end-to-end panel wall time, which is
  model time.

## Panel round 1 (2026-10-04) and the plan as amended

Three independent arms, read-only on a private clone, each asked to re-derive
at least two numbers. Full transcripts: `reviews/2026-10-04-perf-panel/r1-*.md`.

| Arm | Model | Wall | Verdict |
|---|---|---|---|
| Codex | gpt-6.1-sol | 155 s | AMEND |
| Gemini | gemini-pro-agent | 178 s | AMEND |
| Grok | grok-4.7 (high) | 507 s | AMEND |

### What the panel disproved or corrected

- **CPU vs wall (Codex, Grok; confirmed by measurement).** The "2.5
  CPU-minutes" was request latency. The hub used 0.8% of a core during the
  panel wait (CPU-time delta over 30 s; 55 s of CPU in 19 h of uptime). The
  Ollama slice is a socket wait that releases the GIL. The cost is
  per-request latency and bytes, not hub CPU. Corrected above.
- **`since={}` is not light (all three).** `consult._state` treats a falsy
  `since` as no query, and a parsed empty `since` means cursor 0 for every
  pane: the full 4000-event ring of each. The plan's "light availability
  read" would have made fan-out start-up worse, and it would also have
  stripped `agents`, the one field `open_pane` needs. Replace with an
  agents-only route.
- **Long-poll: reject (Grok), amend heavily (Codex).** The hub already has a
  push path, `/api/stream` (SSE), and consult can hold one such stream per
  process. A long-poll would add waiter admission, per-pane wake filtering,
  a cookie re-check it does not inherit, and a timeout that collides with
  consult's 30 s HTTP timeout, to reinvent what SSE does. `_await_ready`
  also waits with a `since` of `1<<40`, so an events-past-cursor predicate
  could never wake it. Dropped; consult subscribes to SSE instead and
  answers a `resync` with one state fetch.
- **Async close: reject (Grok, Codex).** Fan-out never closes a pane, so it is
  off the time-to-read path. Returning before the process is reaped frees a
  `MAX_PANES` slot while the process lives, and clearing `pgid` early hides
  it from the orphan scan after a restart. Dropped.
- **Archive cache: reject (Grok).** Re-derived at 3.1 ms for 97 metadata
  files. Not worth an invalidation scheme. Dropped unless the probe still
  shows the request over 5 ms once the lane probe is off the handler.
- **`seat_wait` changes: not now (Grok).** Zero `peer` events in 94,698
  logged events. Changing `PEER_WAIT_SEEN_S` on an unused path is how a
  later seat reply becomes `busy`. Dropped from this round.
- **Lane probes and auth status are not off-request (Codex, Grok).** A cold
  `lane_probe.probe()` runs a full handshake inside the request that finds
  the cache empty; `claude_auth.status()` has no background refresh and on
  macOS a miss shells out to `/usr/bin/security` with a 5 s timeout, called
  from `state()` on every request. Both go on the availability worker.
- **The observer tick is not a safe home for probes (Codex, Grok).**
  `_observe_once` runs `auth_sweep`, which resumes panes synchronously, and
  the tick is the wedge detector. Lane probing gets its own bounded thread.
- **Delta omission would empty the browser's picker (Grok).** `refresh()`
  assigns `S.agents = d.agents || []`, likewise `archived`, `schedule` and
  `claudeAuth`, so a delta that omits them clears the UI. `commands` events
  carry only a count and the browser's follow-up `refresh()` already has a
  cursor past that seq, so "omit unless changed" would never deliver the
  list slash-completion reads. The contract below is written around that.
- **The browser rebuild is the likelier "slow to respond" (all three).**
  Each SSE message schedules a render; `render()` wipes the roster with
  `innerHTML = ''` and rebuilds it, and the streaming pane's log is rebuilt
  whenever its last seq changes, which is every `TEXT_FLUSH_S` (150 ms).
  Correction to Gemini's figure: `DEFAULT_LOG_CAP` is 300, so a rebuild
  parses at most 300 events unless the user scrolled up.
- **Reader-thread work (Codex, Grok).** ACP callbacks run on the stdout
  reader: JSON decode, transcript write and flush, and for own-branch panes a
  filesystem check, before the next line is read; a line may be 16 MB.
  Instrument before changing.
- **Spawner (Grok).** All forks share one spawner thread; a hung `Popen`
  blocks every lane for 30 s, and `session/new` holds its HTTP thread and a
  `MAX_PANES` slot for up to 120 s. Parallel opens are bounded by that.
- **P3 warm standby: reject (all three).**
- **Numbers tightened (Grok).** 105 pane directories, not about 100.
  Container-install round 2 (Grok 162 to 187 s against Codex 109 to 111 s)
  was missing from the table, so "Grok is 2 to 4x slower" is better stated
  as 1.5 to 4x. The "3.5 s to under 1.5 s" consult-overhead target cannot be
  met: `session/new` alone is 2.2 to 3.0 s and no surviving item pre-runs it.

### The plan, final

In the panel's order. Items 2 to 4 are reversible, local code changes and
need no hub contract change beyond one new read-only route.

1. **Restart the hub onto the deployed tree.** Propose-only: it ends every
   open pane, including the one this review was written from. Before paying
   that, confirm the process start time is older than the tree (it was by
   6.5 h). Add `started_at` and a code fingerprint to `/health` so this is
   one curl next time.
2. **Consult: read the fast arms first.** `fanout --stream` prints each arm
   as a JSON line the moment it completes; `--min-arms N` returns after N
   complete arms and lists the still-running panes by id without cancelling
   them. Codex internal errors and "another prompt was sent" losses are
   reported as arm results and do not count toward N. One `lanes()` read,
   then one thread per arm that opens, sends and waits independently, so a
   hung adapter delays only its own arm. Lane availability comes from a new
   agents-only `GET /api/lanes` (cached, see 3), never from `/api/state`.
3. **Hub: nothing slow inside a request.** One availability worker thread,
   period 5 s, bounded: Ollama probe with a 1 s timeout, the launcher
   checks, cold `lane_probe` handshakes and `claude_auth` refreshes all run
   there and only there; `/api/lanes` and `state()` serve its last result
   with a `checkedAt`, invalidated on a spawn failure. HMAC key cached
   in-process. Delta contract: a pane's `commands` and `config` are sent when
   that pane's `since` cursor is 0 or `full=1`; the global fields
   (`agents`, `catalog`, `archived`, `cwdSuggestions`, `schedule`,
   `claudeAuth`) are sent when there is no `since` or `full=1`; the browser's
   `refresh()` always passes `full=1` and keeps its previous value for any
   field a response omits. Own-branch panes stop reading the worktree
   registry from disk on every snapshot (cache per pane, invalidated by the
   actions that write it).
4. **Consult: subscribe, do not poll.** `wait_turn` and `_await_ready` open
   one `/api/stream` per consult process and reduce events as the browser
   does; a `resync` or a gap triggers one `/api/state` fetch with a real
   cursor. `POLL_S` survives only as the reconnect back-off.
5. **Browser.** Batch `markSeen` into one POST. Reconcile the roster by
   identity instead of wiping it each render (the grid already does this).
   Add a `?perf` flag recording whole-frame time per render. If frames
   exceed about 16 ms while streaming, append the open tail (events after
   the last `turn_end`) and keep closed turns' DOM.
6. **Instrument the reader thread.** Record `emit()` duration per event
   behind the same `?perf`-style switch on the hub side. If transcript writes
   stall the reader, add a bounded, ordered single-writer queue; otherwise
   leave it.
7. **Lean Claude context: a decision, not a change.** Point a pane at a lean
   config dir and A/B time-to-first-token before adopting; it changes what
   Claude knows in a pane.

Dropped: long-poll (hub and consult), async close, archive cache,
`seat_wait` changes, warm standby adapters.

### The 10x, restated honestly

| Metric | Now | After the plan |
|---|---|---|
| state request latency, delta | 45 to 68 ms p50 | under 5 ms |
| bytes per state poll, 5 panes | 95 KB | under 3 KB, and consult no longer polls |
| turn-end detection latency | about 1 s average, 2 s worst | tens of ms (SSE) |
| three-arm panel time to first prompt | about 10 s | about 3 s, bounded by the slowest spawn |
| when the author can read the fastest arm | when the slowest finishes | when that arm finishes (recent panels: 3 to 6x sooner) |
| consult overhead on one fresh pane, excluding the model | 3.5 s | about 3.1 s; `session/new` is 2.2 to 3.0 s of it. Repeat hand-offs should `send` to a kept pane instead: about 0.1 s |
| hub CPU while arms think | 0.8% of a core | not a bottleneck; unchanged |
| browser frame time while streaming | unmeasured | measured first; incremental append if over 16 ms |
| end-to-end panel wall time | model-bound; Grok is the long pole | unchanged by any hub change |

The 10x holds on what Corral controls: request latency, bytes, detection
latency, and when the operator gets to read. It does not hold on a single
fresh hand-off (spawn-bound) or on panel wall time (model-bound), and the
honest lever for the first is reusing panes across rounds.

## Implementation (2026-10-04, branch `perf-2026-10-04`)

Built on the code the live hub runs after the 07:28 restart (eee4d16, which
is origin/master plus 16 local commits). Those 16 commits add security-key
pairing, container support and an orphan-reaping guard. None of them touch
the state request, consult or render paths this plan changes, so the plan
stood as written. The cookie check per request was also unchanged.

### What shipped on the branch

| Plan item | Change | Where |
|---|---|---|
| 1 | `/health` reports `started_at`, a 12-hex `code` fingerprint of the served source, and `code_stale` (the files' stat differs from start) | `hub.py` |
| 2 | `GET /api/lanes` (lane list only, cookie required); `consult.lanes()` uses it, falling back to `/api/state` on a 404 from an older hub. `fanout` reads lanes once, then each arm opens, sends and waits on its own thread. `fanout --stream` prints each arm as a JSON line when it completes, then `{done, ok, running}`. `fanout --min-arms N` returns after N ok arms and lists the rest as running, never cancelled; failed arms do not count | `hub.py`, `consult.py` |
| 3 | `Availability`: one daemon thread recomputes lane availability and the Claude login status every 5 s; `state()` and `/api/lanes` serve its last result with `agentsCheckedAt`. Invalidated on a failed `session/new` and on sign-in. Without `start()` (tests, one-shot tools) every read is fresh, as before. Ollama probe timeout 1 s on this path (5 s default elsewhere). Delta contract as specified: a cursor without `full=1` omits host-wide fields and the `commands`/`config` of panes whose cursor is non-zero; an unparseable cursor gets the full document. Cookie HMAC key cached in memory, re-read when the key file's inode, mtime or size changes, so replacing or deleting the key still revokes every session at once | `sessions.py`, `hub.py`, `auth.py`, `ollama_acp.py` |
| 4 | Consult waits on the hub's push stream as a doorbell: one `/api/stream` per process wakes the waiter whose pane has news, which then reads the light delta. Wakes are coalesced to at most one read per 0.25 s per arm. A stream that is down, or a hub with none, leaves the old 2 s poll | `consult.py` |
| 5 | Browser `refresh()` asks `full=1` and keeps any omitted field's previous value. `markSeen` sends one POST carrying every moved pane (`seen: {pane: seq}`; the hub accepts both forms, bounded by `MAX_PANES`). `?perf` in the address times every render and logs p50, p95, max and over-16-ms counts to the console every 5 s; also `window.corralPerf` | `static/app.js`, `hub.py` |
| 6 | `CORRAL_PERF=1` times every `emit()` (ring append, transcript write and flush, broadcast) into fixed counters, served as `emit_perf` on `/health`. Off by default; no cost when off beyond one flag check | `corral_core/sessions.py`, `hub.py` |

### Deviations from the plan, and why

- **Item 4 is a doorbell, not a full stream reducer.** Consult keeps its
  answer attribution (own-prompt matching, cancel and death detection)
  exactly as it was and only replaces the 2 s sleep with a wake. Reimplementing
  that attribution over raw stream events would put the hand-off's
  correctness at risk for no latency gain, because the light delta read now
  costs under 1 ms.
- **The roster is still rebuilt each render.** No browser automation was
  available to measure frame cost, and a skip-or-diff bug in the roster would
  hide the "needs you" state the hang fix depends on. `?perf` now measures
  it. Do the roster change only if `?perf` shows frames over 16 ms.
- **The worktree registry cache was not built.** Own-branch panes are not
  enabled on macOS yet (`sessions.py`: no lane has passed the macOS matrix),
  so it costs nothing on this host, and a stale cache there would misreport
  branch state.
- **The poller delta misses its byte target.** 3.9 KB at five panes, against
  under 3 KB. What remains is about 0.8 KB of per-pane state (usage, display,
  worktree view) that pollers read.

### Measured: the running code against this branch

Two private hubs on ports 8191 and 8192, scratch state holding the same five
real (closed, re-opened detached) panes, `testkit/perf_probe.py --n 40`,
three alternating rounds each. The rounds agreed within 1 ms; medians shown.

| Request | Running code (eee4d16) | This branch |
|---|---|---|
| poller delta (`since`, no `full`) | 36 ms, 25.8 KB | 0.4 ms, 3.9 KB |
| browser refresh (`since` + `full=1`) | 35.7 ms, 25.8 KB | 1.3 ms, 25.9 KB |
| full document (no `since`) | 39.7 ms, 669 KB | 3.7 ms, 669 KB |
| `/api/lanes` | no such route | 0.3 ms |
| `/health` | 0.3 ms | 0.9 ms (it now stats the source files) |

Turn-end detection, measured in `test_perf_paths`: with `POLL_S` set to 5 s,
consult saw a turn that ended 0.4 s in after about 0.65 s total; with the
doorbell disabled the same test took 5.07 s.

### Not yet measured live

- Three-lane fan-out time to first prompt, and consult overhead on one real
  hand-off. Both need real lanes, so they wait for the live hub to run this
  code: `consult fanout --lane claude --lane codex --lane grok --stream`
  with a trivial prompt, and `perf_probe.py --turn`.
- Browser frame time (`?perf`) and reader-thread `emit()` cost
  (`CORRAL_PERF=1`). Both instruments ship off by default.
