I'll read the performance plan in full, then check its numbers and hot-path claims against the code.The plan is loaded. Next I’ll re-derive the hot-path claims in the hub, consult client, and browser against the code.**AMEND.** The slow-arm gate and the per-request lane probe are real. The plan then spends its complexity on a long-poll and a delta that fight the code, and its headline 1.5 s overhead target cannot be met by P1+P2.

## Disconfirming evidence

**Poll math and "GIL-held CPU".** `wait_turn` returns when that arm ends (`consult.py:397-418`). Three arms polling the whole 1500 s is 3 × 1500 / 2 = 2250 calls. A bugbash-shaped panel (about 500 + 600 + 1800 s) is about 1450 calls, and the fast arms stop. Of the 45–68 ms, the Ollama slice is `urlopen` (`ollama_acp.py:25-27`, `CONNECT_TIMEOUT = 5` at line 16), which releases the GIL. That time occupies the handler. It is not CPU. A refused local port returns in milliseconds. The 5 s ceiling is a blackholed peer, not "Ollama is down".

**"lane_probe and claude_auth refresh off-request" is false on the cold path.** `probe()` runs `_probe_now` (initialize + `session/new` + `close`) inline when the cache is empty (`lane_probe.py:31-41`). Only a stale hit refreshes in the background (lines 36-40). `claude_auth.status()` has no background refresh: a miss calls `expiry()` on the caller (`claude_auth.py:140-146`), and on Darwin that is `/usr/bin/security` with a 5 s timeout (lines 64-67). `state()` calls it on every request (`sessions.py:2736`), separate from `available_agents()`.

**`since={}` is not a light read.** `_state` treats a falsy `since` as "no query" (`consult.py:247`), so `{}` fetches the full document. A parsed `since={}` uses seq 0 and returns every ring event (`sessions.py:1861`). P1's "any `since` omits `agents`" would also strip the field `open_pane` requires (`consult.py:277-283`).

**Checked against the tree, and they hold.** `POLL_S = 2.0` (`consult.py:47`). `PEER_WAIT_POLL_S = 1.0`, `PEER_WAIT_SEEN_S = 5.0` (`corral_core/seat_mcp.py:21`, `corral_core/sessions.py:194`). `request()` wakes on the Event; the 5 s loop is only the stall/timeout recheck (`corral_core/acp.py:170`). `broadcast` uses `put_nowait` (`corral_core/sessions.py:1081-1099`). `create()` holds `_lock` only to reserve the slot (`sessions.py:2225-2241`). Fan-out open is a serial loop (`consult.py:574-583`), and each `open_pane` calls full `lanes()` first (line 277). `surface_for_operator` is in this tree (permission at `corral_core/sessions.py:772`, question at 964). I did not inspect pid 83781.

**Reproduced locally (read-only).** `cwd_suggestions()` is 0.5–0.8 ms for 24 dirs, not a hot-path term. `archived()`-shaped meta reads: 105 pane dirs, 97 metas, 3.1 ms (the plan's "~100" and "2–30 ms" are the right order; the live count is 105). Event kinds across 105 logs, 94,698 lines: `peer` 0, `peer_result` 0. Local `_skill_commands` is 16 commands, 3.1 KB. The live "16 KB / 98 commands" figure is the post-advertisement list (cap 400 at `sessions.py:1185`); I could not see that in-memory list. Panel `wall_s` / `needs_you_s` match the table after rounding, including grok r1 at 4341.6 s with `needs_you_s` 2566.8. Every other raw in that set has `needs_you_s` 0. Container-install r2 (grok 187 s and 162 s, Codex 109 s and 111 s) is omitted from the table, so "Grok is consistently 2–4×" is stronger than those rounds. Codex r6 and r12 are `session/prompt` internal errors. Two Grok losses are `another prompt was sent to this pane` (worktree r1, and the xhigh stall).

## Missed friction

The wall does not poll. Steady updates are SSE (`static/app.js:2782-2803`). Each message calls `scheduleRender` → `render`, which wipes the roster with `innerHTML = ''` (lines 2275-2278) and rebuilds a pane's log whenever `logSignature` changes. That signature includes the last event seq (lines 837-839), and text is emitted every `TEXT_FLUSH_S = 0.15` s (`corral_core/sessions.py:135`). P1 does not touch this path. P4 measures it and then ranks it last.

The stdout reader runs `json.loads` and `_on_event` on the reader thread (`corral_core/acp.py:240-255`), and `emit` flushes the JSONL before `broadcast` (`corral_core/sessions.py:649-673`). A line may be 16 MB (`corral_core/acp.py:30`). That holds the GIL for every other reader and every handler.

`session/new` blocks the HTTP thread for up to 120 s (`corral_core/acp.py:389-391`). The pane is already in the roster as `starting`, which counts toward `MAX_PANES` (`sessions.py:2226-2228`). `create()` checks the binary and `requires` files only (lines 2196-2207). It does not call `unavailable_reason`. One hung adapter, including a stale "Ollama is up", occupies a slot and a handler for two minutes. All forks share one spawner, `max_workers=1`, and a hung `Popen` blocks every lane for `SPAWN_TIMEOUT_S` (30 s) (`corral_core/acp.py:49-86`).

`state()` also calls `worktree_entry` on every own-branch snapshot (`sessions.py:1685-1698`). Bugbash panes are worktrees. `Hub._do` opens and closes a TCP connection per poll (`consult.py:87-110`).

After a restart, `seed_catalogs` and the first `available_agents()` can both miss the probe cache and handshake Claude on that same single spawner.

## Per-item verdict

**P0 — ADOPT, propose-only.** Restart reaps recorded pgids and restores panes detached (`sessions.py:2277-2321`). Confirm the process is actually older than the tree before paying that. `needs_you_s` was non-zero on one arm in this corpus. The other Grok arms were still 1262–2285 s with `needs_you_s` 0.

**P1 cache `available_agents` — AMEND.** Put the probe on its own thread. Do not run a 5 s `urlopen` inside `_observe_once` (`hub.py:106-117`); that tick is wedge detection. A cold `lane_probe` must not run inside `state()`. A 10 s stale "available" is not a clean failure: `create()` will not re-check Ollama, Grok, or Codex, and `session/new` can sit for 120 s.

**P1 cache `archived()` — REJECT.** Reproduced at 3.1 ms for 97 files. Invalidation is not worth it unless the falsifier is still over 5 ms after the probe moves.

**P1 delta omit — AMEND.** `Object.assign` keeps omitted keys on panes the browser already has (`static/app.js:2774`). These assignments do not: `S.agents = d.agents || []`, `S.archived = d.archived || []`, `S.schedule = d.schedule || []`, `S.claudeAuth = d.claudeAuth || null` (lines 2752-2762). A delta refresh empties the picker and the archive. `commands` events carry only `{"n": ...}` (`sessions.py:1186`), and the client then `refresh()`es (app.js:2885) with `since` already past that seq, so "omit commands unless changed" never delivers the list slash-completion reads (app.js:2026). Include `commands` and `config` for any pane the client has not fully snapshotted. Keep `model` and `effort`; they are top-level snapshot keys (`sessions.py:1827`), which is what `_pane_record` reads (`consult.py:444-447`).

**P1 long-poll — REJECT.** `ThreadingHTTPServer` already holds one thread per SSE connection for the life of the page (`hub.py:542-572`, `daemon_threads = True` at 783). A long-poll blocked on `Condition.wait` does not notice a client that timed out; `HTTP_TIMEOUT_S` is 30 (`consult.py:54`), the same as the proposed cap, so a retry overlaps a live waiter. The cookie re-check is only in `_stream_body` (`hub.py:556-562`); a long-poll does not inherit it. `broadcast` would `notify_all` on every event, and `wait_turn` sets `since` to every pane (`consult.py:399-401`), so one arm's 150 ms text flush returns every waiter. During a streaming panel that is more `state()` work than a 2 s poll, which is the opposite of "near zero".

**P1 HMAC cache — ADOPT.** `verify` reads the key file per call (`auth.py:42-47`, 204).

**P2 parallel open — AMEND.** The serial loop is real. Handshakes can overlap after `Popen` returns. The one-thread spawner can turn "about 3 s" into 30 s. Add an agents-only route. Do not use `since={}`.

**P2 long-poll in `wait_turn` / `_await_ready` — REJECT**, same wake and timeout bugs. One `/api/stream` per consult process is the push path that already exists. Handle `resync` with a single `state` fetch.

**P2 `seat_wait` long-poll — REJECT for now.** Zero `peer` events in 94,698 logged events. `PEER_WAIT_SEEN_S` is stamped inside `peer_turn` (`corral_core/sessions.py:1716-1723`). A blocked handler whose `at` is not refreshed still looks idle after 5 s. Changing that constant on an unused path is how a later seat reply becomes `busy`.

**P2 async close — REJECT.** `close()` already pops the pane, then `stop()`s (`corral_core/sessions.py:1804-1806`). `client.close()` is the wait: SIGTERM, 3 s, then up to about 4 s more (`corral_core/acp.py:429-467`). Returning before that frees the `MAX_PANES` slot while the process lives. Clearing `pgid` before death hides the process from the orphan scan (`sessions.py:2314-2321`). `forget()` requires the pane to still be in the roster and `dead` (`corral_core/sessions.py:1812-1818`). Fan-out never closes, so this is off the time-to-read path.

**P2 `--stream` / `--min-arms` — ADOPT.** This is the change that lets a finished arm be read while Grok is still going. `_parallel` joins every thread today (`consult.py:509-511`). Leave the slow arms running, as specified. Codex's internal error and the "another prompt" attribution loss already end arms early. Surface those as arm results, and do not count them toward `--min-arms`.

**P3 — REJECT.** Initialize is 0.55–1.1 s of about 2.9 s in the plan's own table. `session/new` is the rest and is cwd- and seat-token-specific. A warm process still passes the single spawner and adds another orphan case on restart.

**P4 — AMEND, and do it before P3.** The rebuild is already in the code above. Batch `markSeen` (one POST per pane per second, app.js:2728-2733). The roster wipe and the full `renderLog` are the wall.

**P5 — ADOPT as a decision, no code.** `settingSources` does not appear in this repo. Corral does attach a native MCP server on every spawn that supports it (`corral_core/sessions.py:774-794`). That is a slice of the context. Whether to point a pane at a lean config dir is a product call.

## Sequencing

Restart only after the pid's start time is shown to be older than the tree. Next, ship `--stream` and `--min-arms`, plus parallel open behind an agents-only GET. Then move Ollama, launcher checks, cold `lane_probe`, and `claude_auth` off the handler thread. Then stop the browser rebuilding the roster and the open log on every text chunk. Cut long-poll, async close, the archive mtime cache, `seat_wait` changes, and P3.

P3 is not worth it. It saves the smaller spawn term, keeps the 2 s `session/new`, and adds process lifetime to a hub whose restart story is "reap every pgid we still have written down".

## Under-delivery

The "3.5 s consult overhead to under 1.5 s with P1+P2" figure will miss, because that 3.5 s is 7.5 − 4.08 and `session/new` alone is 2.2–3.0 s in this plan's own measurements, while P3 explicitly refuses to pre-run `session/new`.