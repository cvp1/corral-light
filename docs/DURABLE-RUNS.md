# Durable runs: the keeper and the panel

Two pieces that landed together on 2026-10-30, from the 2026-10-12 panels'
unanimous pick for the one feature Corral Light was missing against the
field: a run the hub owns, that survives a hub restart and the author's
/clear, and comes back as one decision packet.

## The keeper (`keeper.py`)

**Before.** The hub forked every ACP adapter itself, over pipes. A hub
restart (`corral-light update`, a crash, a service restart) closed the pipes
and the agent died mid-turn, taking the turn, its pending permission cards
and its queued work with it. `update` therefore refused to restart while any
pane was busy, and often deferred for hours.

**Now.** Each pane's agent runs under a keeper: a small stdlib process in its
own session that owns the adapter's stdio and serves it over a Unix socket.
The hub connects to the socket and talks the same ACP through it. When the
hub goes away the keeper spools what the agent says; when a hub connects
again it says hello (which hub requests are still unanswered, which
permission cards were never answered, how much was spooled), replays the
spool in order, and goes live. The next hub adopts the running turn, and its
`turn_end` lands as if nothing happened.

- `meta.json` carries a `keeper` record (`sock`, `dir`, `pid`); `restore()`
  tries to reattach before it reaps anything. A keeper that does not answer
  is reaped like any orphan and the pane comes back paused, as before.
- `snapshot()` reports `durable: true` for a kept pane; `update` no longer
  counts such a pane as a restart blocker, so the daily update runs.
- The hub's SIGTERM note on a kept pane says the turn **continues**; only
  what was still queued in the hub's memory is marked interrupted.
- Bounds: one adapter line up to 16 MiB; the spool up to 64 MiB (past it,
  lines are dropped, counted, and the pane says so); a keeper with no hub
  for `CORRAL_KEEPER_ORPHAN_S` (6 h) ends its agent; after the agent exits
  the keeper lingers 10 min for a hub to collect the exit, then quits.
- Kill switch: `CORRAL_LIGHT_KEEPER=0` forks adapters directly, as before.
- macOS: launchd leaves a detached grandchild alone on `kickstart -k` and
  `bootout` (measured 2026-10-30). Linux: the unit is `KillMode=process`
  now, so systemd signals the hub alone. The watch timer is unchanged.

Tests: `test_keeper.py` (K1–K10 and the keeper alone). The legacy
"a restart interrupts the turn" tests pin `CORRAL_LIGHT_KEEPER=0`.

**Reviewed by its own feature.** The first real panel run was a one-arm
Codex review of the keeper (sealed files kept in the operator's vault, not
this repo: they name local paths). It returned `amend` with five high findings, all real, all fixed
the same afternoon with a regression test each (`TheKeeperAfterReview`):

1. A replaced hub's socket was closed but not shut down, so its reader kept
   forwarding commands (request-id collisions, a card answerable twice). Now
   `shutdown()` first, and a replaced connection drives nothing.
2. A flushed socket write was booked as hub receipt, so a prompt completion
   written just before the hub died was lost. Now a response stays
   outstanding until the hub ACKS it, and unacked responses are replayed to
   the next hub ahead of the spool.
3. Writes happened under the keeper's lock with no bound, so a hub that
   stopped reading froze the adapter, reattach and the orphan watch. Now
   one writer thread per connection over a byte-bounded queue; a hub that
   is not reading is dropped and its lines spooled.
4. The hub's reader-ended path overwrote already-answered slots with an
   error, so an adopted completion followed by the exit frame raised. Now
   a set slot keeps its answer.
5. A reused keeper dir kept the previous keeper's spool and replayed it
   into the new session. Now the spool is truncated on start.

## The panel (`panel.py`)

A durable, hub-owned review run across several vendors:

```
corral-light consult panel start --charge-file charge.md \
    --lane codex --lane grok --cwd ~/repo --topic "gate design"
corral-light consult panel await <id>      # prints the packet when done
corral-light consult panel next <id> --charge-file charge-v2.md
corral-light consult panel ack <id>
```

Or, from inside a pane, the `corral-panel` MCP tools (`panel_start`,
`panel_status`, `panel_await`, `panel_next`, `panel_ack`); the pane that
starts a panel is its author and gets the packet as a question on its own
pane when the run is done.

What the hub does:

1. **Round 1, blind.** One background pane per arm on its own lane (read-only
   where the lane can be told so). The charge goes inside a nonce-tagged
   fence as data. When the turn ends the WHOLE answer is sealed from the
   transcript on disk to `<out>/r1-<lane>.md`, never a clipped last answer.
2. **Round 2** (`--rounds 2` or `panel next`): each arm gets the others'
   sealed round-1 answers, verbatim and tagged untrusted, and is asked for
   new | repeated | resolved on every finding, dissent kept explicit.
3. **Synthesis** by a FRESH pane on a lane that is neither an arm's nor the
   author's, reading the sealed files only (inlined for a lane without file
   tools). Code, not the synthesizer, extracts each arm's verdict and
   findings from the sealed files, so a round-2 consensus cannot erase a
   round-1 dissent.
4. **The packet**: `packet.md` and `packet.json` — run id, charge digest,
   tree oid, coverage, verdict per arm per round, a findings table
   (severity, file:line, arms for, new | repeated | resolved), dissent on the
   verdict, the synthesizer's questions and next action, and the paths to
   the raw files. The wall shows the run under **Panels** in the rail until
   acknowledged.

The brake (what every arm of the 10-12 panel asked for):

- One round by default. Two at most without `panel next`.
- `panel next` is refused when neither the charge digest nor the tree oid
  changed since the last round, and past `MAX_ROUNDS` (4) whatever the votes.
- Votes never become grants: an arm's "ship" is a line in a table.

Durable: the run's state is `STATE/panels/<id>.json`, rewritten at every
step; the arms are ordinary panes under keepers; on boot `Panels.resume()`
picks every running run back up with the turn ids it already sent. Nothing
of the run lives in the author's context, so it survives the author's
/clear and a hub restart.

Metric (from the panel): the author's /freeze + /clear + resume count on
panel days, from ~30–40 per six days before. If it stays high with the
orchestration gone, the gap was the mission layer, and the scope widens.

Tests: `test_panel.py` (P1–P8).

## Out of scope, on purpose

`--until-ship` loops, autonomous fix/review loops, a mission layer or PM,
hub-owned plan revisions, seat-driven panels, a new GUI. The classifier lane
on the gate (Codex Guardian's shape) is parked with a dated revisit note in
the operator's vault.
