# Corral Light — resilience review and roadmap

> Read of the whole tree on 2026-09-28 (hub.py, sessions.py, corral_core/*,
> static/app.js, consult.py, the launchers, the unit files), plus a feature
> inventory of full Corral and AI-OS Seed for what is worth carrying over.
> The operator's ask: "too many ways to kill a bunch of open panes by simple crashes
> and single points of failure"; make Light the full-time surface; make it
> fully usable from the command line on every lane.
>
> What was verified live: the suite (`python3 test_corral_light.py`, all
> pass), the codex adapter advertising `loadSession: true`
> (spike/node_modules/@agentclientprotocol/codex-acp/dist/index.js:30533),
> full Corral's dead-pane resume (corral/sessions.py:1372). What was NOT
> reproduced here: a Light hub crash with live panes — Light's daily driver is
> mac-host, and linux-host runs the full hub. The kill paths below are read
> from the code, with line numbers, so each can be checked in ten minutes.

## TL;DR

1. **The hub is the parent of every agent, over stdio pipes.** Any hub exit —
   a deploy restart, an OOM, a signal, `launchctl kickstart` — ends every
   live pane's process at once. That is the single point of failure, and it
   is structural: `corral_core/acp.py:227` spawns each adapter with
   `stdin/stdout=PIPE`; the unit files (`corral-light.service`
   `Restart=always`, default `KillMode=control-group`; the plist `KeepAlive`)
   then kill the whole group on restart. Restore brings panes back
   `detached`; the conversation survives via `session/load` on the lanes that
   support it, the **in-flight turn and every queued message do not**.
2. **A pane whose agent dies on its own cannot be revived in Light.**
   `sessions.py` `Pane.resume()` refuses anything that is not `detached`;
   `send()` raises on `dead`. Full Corral fixed exactly this on 2026-09-04
   (corral/sessions.py:1372 accepts `dead`). In Light one adapter crash or a
   usage-limit cutoff means dismiss → Archived → reopen → resume, four clicks
   you have to already know. **This is the cheapest, highest-value fix.**
3. **No graceful shutdown.** hub.py installs no signal handler. SIGTERM drops
   queued type-ahead (`MAX_QUEUED_TURNS=4`) and pending permissions with no
   note in any transcript.
4. **Nothing outside the hub watches it.** `/health` exposes `tick_age_s` for
   a watcher (hub.py:143) and Light ships none; full Corral has `hub_watch.py`
   on an hourly timer (P21).

Recommendation: ship the four P0 items below first (about a day, all in the
shared core so both skins get them), then the CLI client, then port the four
full-Corral features that make Light a daily driver, and only THEN decide
whether the pane-host process (the one true fix for item 1) is worth its
weight, with a week of restart data in hand.

## 1. Kill-path inventory

| # | What kills panes | Blast radius | Where | Fixable? |
|---|---|---|---|---|
| K1 | Hub process exits (deploy restart, crash, signal, OOM) | every live pane's adapter dies; in-flight turn lost; queue lost; pending permissions lost | `corral_core/acp.py:227` (PIPE spawn), `corral-light.service` (`Restart=always`, cgroup kill), plist `KeepAlive` | structural — mitigate (P0-b/c), or remove with a pane host (P2) |
| K2 | Adapter crashes / vendor cuts the session (rate limit, auth rotation) | that pane is `dead` with no way back but dismiss+reopen | `sessions.py` `Pane.resume()` (`if self.state != "detached": raise`); `send()` raises on dead; app.js:1449 offers only ✕ | **yes, 20 lines** (P0-a) |
| K3 | Hub restart while a pane is mid-turn | the turn is silently cut; the pane shows `detached` with no note saying a turn was interrupted | no SIGTERM handler in hub.py | yes (P0-b) |
| K4 | `session/load` unsupported or lossy on a lane | Claude: verified good. Codex: adapter says `loadSession: true` (unverified live). Antigravity: disk-backed (antigravity_acp.py:92). SSH: fresh shell, fine. **Ollama: context lost on every restart, by design** (ollama_acp.py:148 says so in the pane). **Grok: unknown** — the vendor CLI's ACP mode, never measured | per lane | measure with a lane matrix (§4); Ollama can persist history on disk keyed by session id (~30 lines in ollama_acp.py) |
| K5 | Roster cap `MAX_ROSTER=60` or live cap `MAX_PANES=12` | refusal, loud | `sessions.py` `create`/`_reserve_live` | fine as is |
| K6 | Browser falls behind the SSE stream | `resync` marker → full refresh | `corral_core/sessions.py:752` | fine as is |
| K7 | The single spawner thread blocks on one `Popen` | no new pane can start on any lane until it returns | `corral_core/acp.py:162` (`max_workers=1`) | minor; add a spawn timeout note |
| K8 | `Manager()` runs `restore()` at import | a STATE dir that cannot be read stops the hub from starting → restart loop | `hub.py:138`, `sessions.py` `Manager.__init__` | acceptable (loud), but the watchdog should say so |
| K9 | `catalog.json` written non-atomically | a crash mid-write loses the model lists until the next handshake | `sessions.py` `remember_catalog` | trivial: tmp+`os.replace` like `save_meta` |
| K10 | Stale cookie after 12h | consult/TUI clients get 401; consult re-pairs itself | `auth.py` `SESSION_TTL` | fine |

What is already right and should not be touched: atomic `meta.json`
(`corral_core/sessions.py:318`), the bounded ring + JSONL record, the
generation counter that retires stale drain threads (`sessions.py` `_drain`),
group kill on close (`corral_core/acp.py:666`), the long-lived spawner thread
(Grok's `PR_SET_PDEATHSIG`), `PERMISSION_TIMEOUT=None`, the observer tick
that never dies (`hub.py:222`).

## 2. What to build, in order

### P0 — this week, small, in `corral_core` so both skins inherit

- **a. Resume from `dead`.** Port corral/sessions.py:1372 into Light's
  `Pane.resume()`: accept `dead` as well as `detached`, clear `error`, reuse
  `acp_session`. `send()` on a dead pane resumes implicitly, exactly as it
  does for `detached`. app.js:1449: add ↻ next to ✕ on a dead row.
  Test: `kill -9` the adapter's pid, type into the pane, expect a `resumed`
  event and the reply.
- **b. Graceful shutdown.** `signal.signal(SIGTERM|SIGINT)` in `hub.serve()`:
  for each live pane, emit a `note` ("hub restarting — this turn was
  interrupted; N queued message(s) saved"), persist the unsent queue into
  `meta.json` (new META key `queued`, both skins read with `.get`), then
  `pause()`, then exit. On restore, a saved queue lands in the pane's
  composer, never auto-sent (P17). ~60 lines.
- **c. Re-attach on boot.** Record `was_live: true` in meta on every state
  edge into `ready/busy/needs-you`, false on pause/close. After `restore()`,
  a background thread resumes `was_live` panes, staggered 2 s apart, bounded
  by `MAX_PANES`, best-effort with a `note` on failure. A deploy becomes a
  ten-second blip instead of a wall of grey panes. Opt-out env
  `CORRAL_LIGHT_AUTO_RESUME=0`.
- **d. Outside watchdog.** Port `corral/hub_watch.py` (71 lines, stdlib)
  as `corral-light watch`: probe `/health`, judge `tick_age_s`, and on
  failure restart the unit (`systemctl --user restart` /
  `launchctl kickstart -k`) and write `~/.local/share/corral-light/DEAD`
  with the reason. Timer every 10 min. Light has no Home Assistant, so the
  page is the file plus a desktop notification (P0-e below).
- **e. Needs-you notification off the glass.** Half of "I end up back at the
  command line" is not seeing when a pane needs you. A pluggable notifier in
  the hub: macOS `osascript -e 'display notification'`, Linux
  `notify-send`, optional `ntfy` URL — fired on `permission` and `dead`
  only when `MGR.subscribers` is empty (same on-glass rule full Corral's
  push.py uses), never 21:00–05:00.

### P1 — the command line (§4) and the lane matrix

### P2 — decide, with data, whether to remove K1 outright

**Pane host.** One tiny supervisor per pane (`pane_host.py`, stdlib) owns the
adapter's stdio, buffers the last N events, and listens on
`STATE/panes/<id>/sock`. The hub becomes a client of the socket; a hub
restart reconnects and replays what it missed by `seq`. The adapter never
notices. This is tmux for ACP and it is the only way an in-flight turn
survives a deploy. Cost: ~400 lines plus a socket transport in
`AcpClient`; unit files change to `KillMode=process` (Linux) and
`AbandonProcessGroup` (mac) so the hosts outlive the hub. Risk: orphan hosts
if one wedges — the watchdog lists them and the host reaps itself after
`STALL_S` with no client. **Do not build this before P0 a–e**: after a week
of `was_live` re-attach data you will know whether interrupted turns are a
weekly annoyance or a daily one.

## 3. Features worth carrying over

All of these are stdlib-only in full Corral (measured: no non-stdlib
imports in transcripts.py, schedule.py, roles.py, port.py, corral_tui.py,
hub_watch.py, push.py, attention.py). The only shared dependency is
`corral_core/transcript.py`, which Light already ships.

From **full Corral**, ranked by daily-driver value:

| Feature | Why it matters for full-time Light | Source | Port cost |
|---|---|---|---|
| Resume dead panes | see K2 | corral/sessions.py:1372 | 20 lines |
| Scheduled prompts ("later" on a pane) | "at 06:00 ask this pane to summarise overnight runs" — the thing a browser tab cannot do and a cron line does badly | corral/schedule.py (425 lines); actions start/remind/nudge/resume; 3 h catch-up | half a day; drop the runs-registry hook |
| Roles (TOML presets: lane, effort, posture, preamble) | one click to open "reviewer on Grok, strict" instead of five fields; consult already takes `--model/--effort/--posture` | corral/roles.py (1062 lines), never authority | half a day |
| Transcript search + digest | Light's ⌘K searches notes, not its own conversations; "what did Codex say about the cookie yesterday" is a grep today | corral/transcripts.py (FTS over events.jsonl) | half a day |
| Port a conversation to another lane | Light already carries the `ported_from` annotation and cannot produce it | corral/port.py (472 lines) + the transfer gate hook already in the core | half a day |
| TUI client | the terminal surface that answers permissions with the digest bound | corral/corral_tui.py (1715 lines, curses) | a day: cookie name, port, and it reads `/api/attention`, `/api/asks`, `/api/fleet`, `/api/estate`, `/api/run/retry`, `/api/tmux/forget`, none of which Light serves — derive NEEDS YOU from `pane.pending` instead |
| Find in pane, palette actions | small quality-of-life | app.js:1159, 3634 | hours |
| Context-usage pill | Light already receives `usage_update`; it renders nothing | corral app.js:1102 | hours |

Not worth porting (ranch-only state): fleet mailbox, FinOps rooms, delegate
boxes, tmux adoption, Hetzner/Lightsail inventories, the Herdr rail.

From **AI-OS Seed** (the floor Light says it sits on, and shows nothing of):

- **A floor strip in the header.** Run `observability/freshness.py --all
  --json` and `report.py --stats --json` from `~/aios` every few minutes and
  show one line: jobs green/red, vault locked/unlocked, last brief. Today
  "the window for AIOS" cannot tell you the floor is broken.
- **Freeze on pause, resume from brief.** Pause offers "write a session
  brief" (`session-brief/session_brief.py write`); the new-pane dialog
  offers "resume from brief" and seeds the first prompt from it. A brief is
  model-agnostic where an `acp_session` is not — this is the cross-model
  continuity story that directly serves "no matter the model".
- **Doctor with the floor.** `corral-light doctor` already lists lanes; add
  the seed's own checks (vault lock state, scheduler drift, runs.db
  freshness) — the open `SEED-026 /seed-doctor` item, done once, here.
- **Vault-lock awareness.** When `~/.key` is locked, say so in the header
  before a lane fails at its first prompt.

## 4. Fully functional at the command line, on every lane

Today the terminal surface is `consult.py` (lanes/ask/send/fanout/crossfeed/
close): JSON out, scripted, no permission answering, no live stream. Full
Corral has `corral tui`. The `corral-light` wrapper exposes
pair/serve/doctor/diagnose/consult and nothing pane-shaped.

Gap list, every one lane-agnostic because it speaks the hub API:

| Verb | Hub route | Exists today |
|---|---|---|
| `panes` — list with state, lane, model, pending count | `/api/state` | consult `lanes` lists lanes, not panes |
| `open --lane --cwd --model --effort --posture` | `/api/session/new` | consult `ask` (but always sends) |
| `say <pane> <text>` | `/api/session/send` | consult `send` (waits, JSON) |
| `watch <pane>` — live stream to the terminal, markdown-ish, tool rows | `/api/stream` | none |
| `ok <pane> [n]` / `no <pane>` — answer a permission, digest-bound, payload printed in full first (P17) | `/api/session/permission` | none |
| `pause` / `resume` / `close` / `forget` / `rename` | the matching routes | none |
| `attach <pane> <note>` / `quote <from> <to>` | `/api/content/attach`, `/api/session/quote` | none |
| `tui` | polling client | corral_tui.py in the full tree only |

Proposal: one `cli.py` (stdlib, reuses consult's `Hub` client and
self-pairing) with those verbs, wired into the `corral-light` wrapper; then
port `corral_tui.py` on top of it. A `lane_matrix.py` runs open → say →
watch → (permission if the lane has tools) → pause → resume → close against
every available lane and prints a table; that table goes in the README as
the evidence for "works on every model", and re-runs on every adapter bump.
Grok's `session/load` and Codex's live re-attach get measured by that same
matrix, closing K4.

## 5. Sequence

1. P0 a–e (a day). Verify: kill an adapter, resume; `systemctl --user
   restart corral-light` with three busy panes, expect three notes and
   three re-attached panes within 10 s.
2. `cli.py` + `lane_matrix.py` (a day). Verify: the matrix table, all lanes.
3. Roles, scheduled prompts, transcript search, port (three days).
4. Floor strip + freeze/brief (a day).
5. Read a week of restart notes; decide on the pane host.
