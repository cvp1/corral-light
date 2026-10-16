Here is an adversarial design review of [docs/remote-panes-plan.md](docs/remote-panes-plan.md), grounded in the existing codebase.

---

### A. Recommendation A (`drive` grant on peer hub) vs. B (agent over SSH, local pane) — and Steelmanning B

**Verdict:** **A is the right choice** if you want full Corral Light pane semantics (own-branch worktrees, review-at-end, blind challenges, persistent vendor sessions across restarts). However, the plan’s dismissal of B understates how much complexity A introduces by splitting pane state across two hubs (**mirror pane vs. real pane**).

#### Steelmanning B (Agent over SSH, pane local)
1. **Zero split-brain state, zero mirror-pane invention:**
   In [sessions.py:1503-1556](sessions.py#L1503-L1556), `Pane.start()` simply spawns an `acp.Client(argv, cwd, ...)` speaking JSON-RPC over stdio. Under B, the ACP stdio stream is just tunneled over `ssh <host> ...` (the exact transport boundary `ssh_acp.py` already uses for shells in [ssh_acp.py:55-68](ssh_acp.py#L55-L68)).
   - There is **one** `Pane` object, **one** event ring (`events.jsonl`), **one** `_turn_lock`, **one** permission table (`self.pending` in [sessions.py:2097-2104](sessions.py#L2097-L2104)), and **one** browser SSE stream (`/api/stream`).
   - Every local feature that operates on transcripts—⌘K search, 24h digest, cross-feed ([static/app.js:4417-4457](static/app.js#L4417-L4457)), quote-into ([static/app.js:4396-4405](static/app.js#L4396-L4405)), local `port` ([sessions.py:2393-2429](sessions.py#L2393-L2429)), and `@seat` messaging (`seat_send` / `seat_broadcast` / `ask_human`)—works **out of the box** without federation changes.
2. **No new network listener or long-poll relay infrastructure:**
   - Option B works even when the remote machine is behind NAT/firewall and cannot expose port `8097` inbound, or when the remote machine shouldn't run a full Corral Light daemon at all (e.g., an ephemeral cloud GPU box or build VM reachable via `ProxyJump`).
   - Outbound SSH is already configured for `host:<name>` lanes ([sessions.py:265-320](sessions.py#L265-L320)).
3. **Preserves the P17 invariant ("permission cards never cross a link"):**
   - The ACP `session/request_permission` callback arrives directly at the local `Pane._on_permission` ([sessions.py:2018-2120](sessions.py#L2018-L2120)), which computes `permission_digest(tool_call, options)` locally and resolves the future on the single wall that owns the `acp.Client`. No two-wall card race exists.
4. **Why B still loses to A:**
   - **Process lifecycle & orphan reaping:** `Manager.restore()` ([sessions.py:2586-2633](sessions.py#L2586-L2633)) relies on local PID/PGID/start-time checks (`acp.reap_orphans`) to guarantee a restarted hub kills orphaned adapters before running `session/load`. Over SSH, a dropped TCP connection or local hub restart can leave a remote agent process running mid-turn mutating the remote checkout while `session/load` attaches a second process.
   - **Local filesystem coupling:** Worktree isolation (`_wt.create`, `_wt.verify`, `_wt.snapshot` in [sessions.py:2548-2575](sessions.py#L2548-L2575), [sessions.py:2865-2898](sessions.py#L2865-L2898)), `review_at_end` path confinement (`_in_tree_write` using `os.path.realpath` and `git check-ignore` in [sessions.py:2122-2167](sessions.py#L2122-L2167)), blind reviewer `git archive` export ([sessions.py:3039-3078](sessions.py#L3039-L3078)), and Claude posture enforcement (`_claude_settings` writing a local `--settings` JSON file in [sessions.py:424-466](sessions.py#L424-L466)) all require local POSIX filesystem access to the repo and state dir.

---

### B. Security: `drive` vs. `takeover`, R5 Card Digest Soundness, and Leaks/Escalations in R1–R7

#### 1. Is `drive` materially worse than `takeover`?
**Yes — qualitatively worse.**
- Today's `takeover` ([hublink.py:1045-1154](hublink.py#L1045-L1154)) is **exfiltration + local stop**: a compromised peer with `takeover` can stop an existing pane (`mgr.close`), bundle its git repository and transcript (`git_snapshot`), and read them. It **cannot** execute arbitrary code, run commands, or modify files on the source hub, nor can it touch directories where no pane is currently open.
- `drive` grants **interactive Remote Code Execution (RCE)** on the peer hub under the peer operator's user account and ambient credentials (SSH agent, cloud CLIs, API keys, keychain):
  - Via `POST /fed/v1/drive/open` (R3) + input relay (R4) + card answering (R5), a compromised driving hub can open a coding agent in any allowed root, prompt it to run arbitrary shell commands or edit `.git/hooks`, and **auto-approve every `strict` permission card via R5** (including `allow_always`).
  - Even with `posture="strict"`, the driving hub *is* the human in the loop for R5. Therefore, `posture` on a driven pane protects the operator against the *model*, **not** the peer hub against a compromised driving hub. The plan should state explicitly in [docs/HUB-LINKS.md](docs/HUB-LINKS.md) and the security notice that **`drive` is equivalent to granting shell/user-account execution under the allowed folder roots**.

#### 2. Is the R5 digest-bound card answer sound?
Mostly, with four subtle edge cases in the current code:
- **Good:** `permission_digest(tool_call, options)` ([sessions.py:678-693](sessions.py#L678-L693)) hashes `kind`, `title`, `rawInput`, `locations`, `content`, and `[o.optionId, o.kind]`. `Pane.answer(req_id, option_id, digest)` ([sessions.py:2182-2218](sessions.py#L2182-L2218)) pops the entry under `self._lock`, checks `hmac.compare_digest(entry["digest"], str(digest))`, and **re-inserts `self.pending[req_id] = entry` before raising `ValueError`** if the digest mismatches or `option_id` is invalid ([sessions.py:2192-2201](sessions.py#L2192-L2201)).
- **Flaw 1 — Second-wall UX on race ("first answer from either wall wins"):**
  When Wall A answers first, `self.pending.pop(req_id)` succeeds and `Pane.answer()` emits `permission_resolved`. When Wall B's operator clicks 200ms later, `Pane.answer()` raises `ValueError(f"no pending request {req_id}")` ([sessions.py:2191](sessions.py#L2191)), which surfaces as an error toast even though the card was legitimately resolved on the other wall. Worse, if the relay lags by 500ms, Wall B's card stays visible briefly. `Pane.answer()` (or the drive route) should distinguish "already answered (`outcome`)" from "unknown request ID".
- **Flaw 2 — `req_id` namespace is per-`acp.Client` process (`int` starting at `0`, or JSON-RPC id):**
  In `acp.py` / `Pane._on_permission` ([sessions.py:2089](sessions.py#L2089)), `req_id` is `str(req_id)` from the adapter's JSON-RPC request stream (typically `"0"`, `"1"`, `"2"`... reset on every adapter restart/resume). If a pane restarts (`Pane.start()`) while a delayed answer from the driving hub is in flight, `req_id="1"` could collide with the new process's first permission card. The digest check (`permission_digest`) will catch it *unless* the tool call and options are identical (e.g., running `pytest` twice). Binding `req_id` or the card digest to `Pane._generation` ([sessions.py:1032](sessions.py#L1032)) eliminates cross-generation replay.
- **Flaw 3 — `ask_human` is NOT digest-bound and NOT in `self.pending`:**
  The plan's R5 talks about "permission cards", but operators also answer `ask_human` cards in the Needs-You rail ([static/app.js:3049-3078](static/app.js#L3049-L3078)). `ask_human` does **not** go through `Pane.answer()`; it lives in `self.question` and is answered via `/api/session/answer_question` (or `Pane.answer_question`). If R4/R5 does not cover `self.question`, an agent calling `ask_human` on a driven pane will hang forever from the driving wall.
- **Flaw 4 — `allow_always` pollution of main checkout on non-worktree driven panes:**
  In [sessions.py:2026-2031](sessions.py#L2026-L2031), `allow_always` is only stripped when `self.worktree_id` is set. On a non-worktree driven pane, a driving hub answering `allow_always` mutates `.claude/settings.local.json` in the peer's checkout permanently, affecting future local panes in that repo.

#### 3. Anything in R1–R7 that leaks or escalates?
- **Symlink escape out of R3's allowed folder roots:**
  R3 says: *"The folder must lie under a root the peer's operator allowed for driving."* If validated only with lexical path prefixing, or even `Path.resolve()` at `open` time, an agent inside `/allowed/repo` can follow or create a symlink `/allowed/repo/sub -> /etc` or `~/.ssh` on subsequent operations, or the operator/driving hub can pass `/allowed/repo/symlink_outside`. And for non-worktree panes, `Pane.create()` does not confine the agent's tools to `cwd` — only `review_at_end` ([sessions.py:2122-2167](sessions.py#L2122-L2167)) and sandboxed reviewers have path guards.
- **Ownership scope ("It does not reach panes the peer did not open"):**
  - Where is `opened_by_peer` stored? If stored only in memory, a peer hub restart forgets ownership (breaking R4/R5 after restart) or loses the boundary. If stored in `meta.json`, it **must** also be bound to the peer's `pairId` (`peer.get("pairId")` in [hublink.py:1054](hublink.py#L1054)) so unpairing and re-pairing the same hub ID cannot resume driving old panes.
  - What if the local operator on the peer hub types into a driven pane at the peer's own wall (which §2 explicitly praises: *"The pane is also visible and drivable at the peer's own wall"*), and then the driving hub reads those turns via R4 even if the driving hub does **not** have `watch` (`R1: It implies nothing else`)? That's fine if intentional, but note that a compromised driving hub can read anything the peer operator types into that shared pane.
- **R7 ("and the seat tools... No agent on either hub can reach these routes"):**
  Wait: when `POST /fed/v1/drive/open` calls `mgr.create(...)` on the peer hub, `Pane._native_mcp()` ([sessions.py:1933-1944](sessions.py#L1933-L1944)) attaches the `corral-seats` MCP server (`seat_list`, `seat_send`, `seat_broadcast`, `seat_wait`, `ask_human`) to every non-reviewer pane!
  - That means an agent inside a **driven** pane on Peer B **can** call `seat_list()` and `seat_send()` to message *other local seated panes on Peer B* that the driving hub did **not** open and has no grant to see or drive!
  - This directly violates R1 (*"It does not reach panes the peer did not open"*) and R7. Driven panes either must not get `corral-seats` (`_native_mcp()` disabled unless opted in) or `seats_mcp.py` must isolate driven panes from local seats.

---

### C. The Relay (R4): Long-Polling over the Existing Signed Request Path

**Verdict:** The existing `hublink.py` transport will **fail under continuous long-polling** for four concrete reasons in the code today:

1. **Connection Caps (`MAX_CONNS_IP = 4`, `MAX_CONNS = 32`):**
   - In [hublink.py:67-68](hublink.py#L67-L68) and [hublink.py:446-453](hublink.py#L446-L453), `_Srv.process_request()` enforces `MAX_CONNS_IP = 4` concurrent TCP connections per peer IP, and `FedHandler._go()` ([hublink.py:1735-1737](hublink.py#L1735-L1737)) sends `Connection: close` and sets `self.close_connection = True` on every response (no HTTP keep-alive).
   - If R4 long-polls `/fed/v1/drive/events?pane&since` per pane, **opening 4 driven panes saturates `MAX_CONNS_IP = 4`**. The 5th driven pane—or any user click on **Send**, **Cancel**, **Answer card**, or **Roster refresh** while 4 long-polls are holding the 4 slots—is **immediately dropped at the socket level** (`self.shutdown_request(request)` in [hublink.py:450](hublink.py#L450)), which the client reports as `Unreachable`!
2. **Hardcoded 15-second Socket Timeout in `_Srv.get_request()`:**
   - In [hublink.py:439](hublink.py#L439), `_Srv.get_request()` sets `sock.settimeout(15)`. Any long-poll that waits >15s or any slow response will time out or force rapid reconnect churn.
3. **Full TLS Handshake + Subprocess/Stat Check + Disk `fsync` on Every Poll:**
   - Because `FedHandler` closes the TCP connection after every request ([hublink.py:1735-1737](hublink.py#L1735-L1737)) and `_raw_call` creates a fresh `HTTPSConnection` ([hublink.py:846-848](hublink.py#L846-L848)), every poll cycle does a full mTLS handshake.
   - Worse: on every successful `call()`, `self._seen(pid, grants=...)` ([hublink.py:895-910](hublink.py#L895-L910)) rewrites `roster-cache.json` via `_write_private()`, which calls **`os.fsync(fd)` on the temp file AND `os.fsync(dfd)` on the parent directory** ([hublink.py:100-115](hublink.py#L100-L115)) while holding `self.lock`! And on the server side, `verify()` appends to `nonces.log` under `self.lock` ([hublink.py:1600-1612](hublink.py#L1600-L1612)). During active token streaming (where events arrive every ~50–100ms), a naïve long-poll that returns immediately on each chunk and re-polls will trigger 10–20 mTLS handshakes + 20–40 disk `fsync`s per second!
4. **Nonce Store (`MAX_NONCES = 20000` in `2 * SKEW_S = 600s`):**
   - In [hublink.py:59](hublink.py#L59) and [hublink.py:1601-1610](hublink.py#L1601-L1610), `seen` holds nonces for `2 * SKEW_S = 600s` (10 minutes) and refuses with `429 too many requests` at `MAX_NONCES = 20000` (average 33.3 req/sec sustained over 10 minutes across all routes from that peer).
   - If 4–6 driven panes are streaming token chunks concurrently and long-polling returns per-chunk (every 50ms = 20 req/sec/pane), 2 panes streaming for 10 minutes will hit `MAX_NONCES = 20000` and lock out the peer with HTTP 429 (`"too many requests; slow down"`).
   - Also, `verify()` does an $O(N)$ list comprehension over `seen.items()` on *every single request* under `self.lock` ([hublink.py:1603-1604](hublink.py#L1603-L1604)): at 15,000 nonces, that scans 15,000 dict entries under the global hublink lock on every request.

#### Better Shape for R4
- **Multiplex all driven panes for a peer onto a SINGLE multiplexed stream per peer** (`/fed/v1/drive/stream` or `/fed/v1/drive/events` taking a cursor map `{pane_id: since_seq}` across all driven panes on that peer), rather than one long-poll connection per pane. That uses at most **1 connection slot per peer** regardless of how many panes are driven.
- **Or use a single chunked/SSE response over the signed HTTPS request** (parallel to `/api/stream` locally): authenticate once in `verify()`, then stream newline-delimited JSON events (`\n` heartbeat every 10s) over that open TLS connection, reconnecting only on drop.
- **Coalesce streaming chunks (e.g., 50–100ms batch window)** if using poll-per-batch, and skip `_seen()` disk `fsync` on relay event fetches.

---

### D. What Is Missing or Underspecified

1. **Lane Model & Effort Pickers (`S.catalog`) in `wireDialog()`:**
   - R2 says `GET /fed/v1/lanes` returns `available_agents()` minus shell lanes, and `#f-host` lists each peer with `drive`.
   - Look at how `wireDialog()` in [static/app.js:3728-3783](static/app.js#L3728-L3783) actually works: `#f-agent` collapses agents sharing `a.group` into a single `group:<id>` option, and `#f-host` lists the members of that group!
   - If you group by **peer** ("R2: The picker adds one group per peer with `drive`"), then `#f-agent` would show the *peer name* and `#f-host` would show the *lanes*—backwards from the plan's intro (*"the operator picks an agent (Claude, Codex, Grok, Gemini) and a **host**"*). To pick **Agent** in `#f-agent` and **Host** in `#f-host`, each agent family (`claude`, `codex`, `gemini`, `grok`) must either be its own group across hosts or `#f-host` must be populated independently of `group:<id>`.
   - Furthermore, `fillCfg()` ([static/app.js:3794-3835](static/app.js#L3794-L3835)) populates `#f-model` and `#f-effort` from `S.catalog[chosenAgent()]`. `available_agents()` does **not** include `self.catalog`! Unless `GET /fed/v1/lanes` also returns the peer's `mgr.catalog` (and merges it into `S.catalog` under `hub:<peer-id>:<lane>`), `#f-model` and `#f-effort` will always be disabled (`"Default (agent decides)"`) for remote panes.

2. **Folder Suggestions (`#cwdlist`) and Own-Branch Worktree Probing (`wtProbeSoon`):**
   - When the user switches `#f-host` to a remote peer in `#newdlg`, `#f-cwd` defaults to `S.lastCwd || S.defaultCwd || '~'` (a **local** path on the driving hub!) and `#cwdlist` suggests **local** folders ([static/app.js:3786-3791](static/app.js#L3786-L3791)). `GET /fed/v1/lanes` needs to return the peer's allowed driving roots so `#f-cwd` and `#cwdlist` update when switching hosts.
   - Changing `#f-host` triggers `wtProbeSoon(dlg, 0)` ([static/app.js:3854-3856](static/app.js#L3854-L3856)), which calls `/api/session/worktree/probe` on the **local** hub's filesystem. Without a federated worktree probe (or forwarding the probe when a remote host is selected), the **Own branch** and **Review at the end** checkboxes in `#newdlg` will never appear (or will probe the wrong machine's directory)!

3. **R6 (Reviews & Blind Challenges) has 7 routes, not just "diff and review card":**
   - R6 says: *"The diff and the review card render from the peer, through the relay. Push, merge and discard run on the peer, bound to the reviewed tree as today."*
   - In reality, the worktree review dialog (`openReview` in `static/app.js`) calls `/api/session/worktree/snapshot`, `/api/session/worktree/commit`, `/api/session/worktree/mark_reviewed`, `/api/session/worktree/remotes`, `/api/session/worktree/publish`, `/api/session/worktree/discard`, and `/api/session/worktree/challenge` ([sessions.py:2834-2960](sessions.py#L2834-L2960)).
   - Also, `p.worktree_summary` is only in memory and emitted via `p.emit("worktree", {"summary": summ})` ([sessions.py:2792-2793](sessions.py#L2792-L2793)), while `worktree_view()` and `challenge_view()` are computed dynamically in `Pane.snapshot()` ([sessions.py:2245-2257](sessions.py#L2245-L2257)), **not** solely from `events.jsonl`. The relay (R4) must carry `snapshot()` state metadata (state, pending cards, `worktree`, `challenges`, `usage`, `contextPct`, `rateLimit`, `error`, `deadCause`), not just `pane.events`.

4. **Attachments (`attachContent`), Quote (`quoteInto`), Cross-Feed, and `port`:**
   - **⌘K Content Attach ([static/app.js:4354-4378](static/app.js#L4354-L4378)):** `/api/content/attach` checks whether the target pane's lane has tools; if it does (`mode === 'path'`), it inserts a **local filesystem path** from the driving hub into the composer! On a driven pane running on Peer B, that local path does not exist (or points to a different file). For a driven mirror pane, `/api/content/attach` **must** force `mode = 'excerpt'` (inline text) instead of a file path.
   - **Porting (`Manager.port` in [sessions.py:2393-2429](sessions.py#L2393-L2429)) and `openPort` ([static/app.js:3968-4001](static/app.js#L3968-L4001)):** Does `port` work from/to a mirror pane? If a mirror pane has `cwd` set to a path on Peer B, calling `Manager.port()` to a local lane will fail `if not cwd.is_dir()` on the driving hub.

5. **Notifications, Quiet Hours, and Duplicate Alerts:**
   - When a driven pane enters `needs-you` or finishes a turn, does the peer hub fire desktop/push notifications, or the driving hub, or both? If one operator runs both machines (e.g., laptop + desktop on the same desk or Tailscale notification webhook), both hubs firing notifications will double-notify or violate quiet hours configured on only one hub.

6. **Hub Restart & Lifecycle Asymmetry (Driving Hub vs. Peer Hub):**
   - **Peer hub restarts:** `Manager.restore()` on the peer hub restores panes as `detached` ([sessions.py:2586-2590](sessions.py#L2586-L2590)) and cuts off open turns via `p._turns().recover()`. When the driving hub reconnects its relay, how does the mirror pane reflect `detached`, and does sending a prompt from the driving hub auto-resume (`Pane.start()`) on the peer?
   - **Driving hub restarts:** Does `Manager.restore()` on the driving hub restore mirror panes from `STATE/panes/<id>/meta.json`? Must mirror panes count against `MAX_PANES = 12` (which caps live *processes* in [sessions.py:2526-2530](sessions.py#L2526-L2530)) or only `MAX_ROSTER = 48`? (They hold no local adapter process, so counting them against `MAX_PANES` artificially starves local panes.)
   - **Closing / Forgetting on one wall:** If the operator closes or forgets the driven pane at the peer's own wall, what happens to the mirror pane on the driving wall? And if the driving hub closes the mirror pane while the peer hub is unreachable, how is the orphan pane on the peer hub cleaned up when the link comes back (an outbox entry like offers)?
   - **Duplicate sight in `renderHubs()` (⇄ dialog):** If a driven pane lives as a real pane on Peer B and a mirror pane on Driving Hub A, it will appear on Driving Hub A's main grid **and** in Peer B's remote roster in the Hub Links dialog (`renderHubs()` in [static/app.js:4619-4661](static/app.js#L4619-L4661), where the operator could click **Take over** on a pane they are already driving!). Driven panes should be badged and excluded from Take over by the hub already driving them.
