# Grok (grok-4.7) — review of docs/RESILIENCE-REVIEW-2026-09-28.md

pane 5018c4e4929f · model grok-4.7 · complete True

I'll read the hub, session, and adapter code the plan cites before judging any item.The plan's line citations need checking against the live spawn, resume, and shutdown paths.TL;DR 1–4 are K1, K2, K3, and the sequence. Verdicts below.

**K1 — RESHAPE.** A hub exit closes every adapter pipe (`corral_core/acp.py:224`). `Restart=always` is set and `KillMode` is not (`corral-light.service:37`), so Linux does kill the cgroup. The Mac host is launchd (`KeepAlive`, `com.cvp1.corral-light.plist:81`) and `start_new_session=True` puts the adapter in its own session (`acp.py:227`). Launchd's process-group kill misses it. Grok dies because the spawner thread dies (`acp.py:214`). Any adapter blocked in a model call keeps running until its next stdin read.

**K2 — KEEP.** `resume()` accepts only `detached` (`sessions.py:1043`), `send()` raises on `dead` (`sessions.py:1375`), and a dead row offers only dismiss (`static/app.js:1449`). This repairs a local adapter crash whose `acp_session` is still valid. A vendor-side session kill stays dead after the same call.

**K3 — RESHAPE.** `serve()` installs no handler (`hub.py:548`). The running prompt is already popped (`sessions.py:1455`). `pause()` then deletes whatever is left (`corral_core/sessions.py:659`). The user line is already in `events.jsonl` (`sessions.py:1418`, flushed at `corral_core/sessions.py:501`). On Linux the cgroup SIGTERM hits the adapters in the same moment as the hub, so the handler runs beside a dying child.

**K4 — RESHAPE.** Ollama advertises `loadSession: true` and then says the context is gone (`ollama_acp.py:136`, `:148`). Codex's flag is an advertisement. Measure `session/load` per lane before any boot path calls it.

**K5 — KEEP.** Create refuses at the caps (`sessions.py:1890`). Restore's overflow is `notRestored` and the roster shows it (`sessions.py:2095`, `app.js:1528`).

**K6 — KEEP.** A full subscriber queue becomes one `resync` (`corral_core/sessions.py:758`). The pane stays up.

**K7 — RESHAPE.** One spawner, and `_spawn` waits on `Future.result()` with no timeout (`corral_core/acp.py:158`). A hung `Popen` stalls every later start and resume. `/health` stays fresh: the observer never touches that thread (`hub.py:129`).

**K8 — RESHAPE.** `restore()` runs from `Manager.__init__` (`sessions.py:1765`), invoked at import (`hub.py:113`; the plan's `:138` is the observer's `except`). One bad `meta.json` is skipped (`sessions.py:1946`). An unreadable `panes/` directory is not, and `Restart=always` then loops.

**K9 — KEEP.** `remember_catalog` uses `write_text` (`sessions.py:1850`). `save_meta` already does tmp plus `os.replace` (`corral_core/sessions.py:333`). Same fix. It does not take a pane down.

**K10 — KEEP.** `SESSION_TTL` is 12h (`auth.py:46`). The stream re-checks the cookie (`hub.py:363`). Panes stay.

**P0-a — RESHAPE.** The gate change is the right fix, and it is larger than a copied `if`. `resume()` overwrites `self.client` without closing the old one (`sessions.py:1056`). `agent_exit` leaves `_queue` in place (`sessions.py:1174`). The next `send` would drain stale prompts into the new process. Close the old client and park the queue first. `pause()` already leaves the `detached` shape this path expects (`corral_core/sessions.py:667`).

**P0-b — RESHAPE.** A transcript note is worth writing. Persisting `_queue` into the composer duplicates a `user` event already on disk and misses the popped prompt. `pause()` is the wrong primitive. Write the popped text and the queue from the main thread. Set `KillMode=mixed` if that handler is supposed to run before the adapters are signaled.

**P0-c — DROP.** Boot re-attach opens a new adapter onto `acp_session` while a the Mac host orphan may still hold it. Twelve panes at 2s is 24s plus handshakes; the verify step expects 10s. It is also a rate-limit stampede, which is K2. Ollama resumes into an empty context and says so.

**P0-d — DROP.** A timer that restarts the unit is another K1. `tick_age_s` is `-1` until the first tick (`hub.py:241`) and freezes when any `snapshot` throws, because `_TICK` updates only after the whole loop (`hub.py:137`). Page when the main process is gone.

**P0-e — RESHAPE.** `subscribers` means an SSE queue is registered (`corral_core/sessions.py:783`). A backgrounded tab keeps one, which is the case where you are not looking. Notify on `permission` and `dead` when that pane's stream has not been read. Quiet hours as written.

**Seq 1 — RESHAPE.** Bundling c and d into the first day ships the two items that add kills.

**Seq 2 — KEEP.** The lane matrix is the measurement K4 lacks. It needs `cancel` in the verb list before the table means "every lane."

**Seq 3 — KEEP.** Roles, schedule, search, and port sit off the kill path.

**Seq 4 — DROP** from this roadmap. The floor strip does not keep a pane alive.

**Seq 5 — RESHAPE.** A week of `was_live` notes measures a boot path that should not be on. Count orphan pids and interrupted turns after plain restarts.

### Missed kill

`meta.json` stores `acp_session` and no pid (`corral_core/sessions.py:246`). `from_meta` forces `detached` (`sessions.py:1024`). On the Mac host the old adapter is still alive after the hub exits, still holding the vendor session. The next `resume` starts a second adapter on that same id. Linux cgroup stop hides this. The daily driver does not. Separately, from the `pop` at `sessions.py:1455` until `turn_end`, the prompt text exists only inside `client.prompt`. SIGKILL of the hub skips both `agent_exit` and `_drain`'s drop note (`sessions.py:1474`).

### Sequencing

P0-a before a pane host is right: a dead adapter inside a live host is still K2. P0-c and P0-d are the wrong predecessors. They spawn and restart, and c is the inverse of "the adapter must still be there to reattach." The host is the only design that answers K1. It is not the only thing that matters. Ship a corrected a, a note-only b, and the orphan reap. Hold c, d, and the host until the pid is on disk.

### Pane host

Two failure modes, both worse than today.

Unsupervised tools. The host outlives the hub, `PERMISSION_TIMEOUT` is `None` (`corral_core/acp.py:111`), and an already-approved shell keeps editing with nobody attached. Today a hub death closes the pipe and, on Linux, the cgroup. The socket is the same uid as the agent. A pane with a shell can open `STATE/panes/<id>/sock` and answer its own permission unless the protocol rejects a peer in the adapter's group.

Two writers on one `acp_session`. Seq is assigned in the hub (`corral_core/sessions.py:491`) and the durable log is flushed there. A reconnect that replays, plus any boot `session/load`, runs a second loader against the host's in-flight `session/prompt`. You get a duplicate `permission` or `turn_end`, or a gap the browser treats as `resync`. A bounded buffer either blocks the adapter for a long outage or drops events and the replay lies. Today the failure is blunt and single-writer.

### CLI

`cancel`. The route is already there (`hub.py:496`) and it is absent from the verb table. `consult.wait_turn` cancels on timeout (`consult.py:457`) and on `needs-you` only writes to stderr (`consult.py:438`). A foreground `say` that inherits that wait kills the turn the human was supposed to approve. `ok` in a second process loses the race. The same terminal has to print the pending payload and take `ok`, `no`, or `cancel` before any timer fires. `watch` on `/api/stream` is forward-only (`hub.py:331`); the backlog is `/api/state` and `/api/session/history`.

### Build this

Write `pid` and `pgid` into `meta.json` at spawn, and have `restore()` SIGTERM that group when it is still the adapter, before any `session/load`. The Mac host's `setsid` children outlive launchd. Both P0-c and the pane host double-attach until this exists. It is smaller than the host, and it is the kill path the inventory missed.