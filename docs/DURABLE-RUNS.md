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
The hub connects to the socket and talks the same ACP through it. Every
line the agent writes is numbered and kept until a hub acknowledges it; when
a hub connects it says hello (which hub requests are still unanswered, how
much it is about to replay), re-sends the cards nobody answered and every
unacknowledged line in order, and goes live. The next hub adopts the running turn, and its
`turn_end` lands as if nothing happened.

- `meta.json` carries a `keeper` record (`sock`, `dir`, `pid`); `restore()`
  tries to reattach before it reaps anything. A keeper that does not answer
  is reaped like any orphan and the pane comes back paused, as before.
- `snapshot()` reports `durable: true` for a kept pane; `update` no longer
  counts such a pane as a restart blocker, so the daily update runs.
- The hub's SIGTERM note on a kept pane says the turn **continues**; only
  what was still queued in the hub's memory is marked interrupted.
- Bounds: one adapter line up to 16 MiB; the outbox up to 64 MiB (past it,
  the oldest notifications are dropped, counted, and the pane says so; never
  a card, a response or the exit); a keeper with no hub
  for `CORRAL_KEEPER_ORPHAN_S` (6 h) ends its agent; after the agent exits
  the keeper lingers 10 min for a hub to collect the exit, then quits.
- Kill switch: `CORRAL_LIGHT_KEEPER=0` forks adapters directly, as before.
- macOS: launchd leaves a detached grandchild alone on `kickstart -k` and
  `bootout` (measured 2026-10-30). Linux: the unit is `KillMode=process`
  now, so systemd signals the hub alone. An installed unit is never
  rewritten by update, so update refuses to restart a hub whose installed
  unit is not `KillMode=process` and prints the fix (2026-10-31).

Tests: `test_keeper.py` (K1–K10 and the keeper alone). The legacy
"a restart interrupts the turn" tests pin `CORRAL_LIGHT_KEEPER=0`.

### The keeper's delivery contract (protocol 3, 2026-10-31)

Three review rounds of protocol 2 showed each fix opening the next hole
(spool, carried lines, in-flight requeue, `drive_lock`, the inode guard).
Protocol 3 replaces all of them with one structure.

- **One numbered outbox.** Every line the adapter writes gets a sequence
  number; the adapter's exit is the last one. A line that is not a JSON
  object is dropped and counted.
- **Cumulative acks.** `{"keeper": "ack", "s": N}` means the hub has APPLIED
  every line up to N, and those lines leave the outbox. The client acks a
  card, a response or the exit at once, anything else within 0.2 s, and
  acks before a deliberate detach.
- **Attach.** A new hub gets `hello`, then the held cards, then every line
  after the last ack, in order, then `caught-up`, then live lines. So a line
  reaches the hubs exactly once, except the lines a hub had applied but not
  yet acked when it crashed: those come again to the next hub.
- **Cards are held until answered, not until acked.** An acked, unanswered
  card is re-sent first to every hub that attaches. Once an answer for its
  id passes through the keeper it is never sent again, acked or not, and a
  second answer for that id is dropped there: the agent gets one reply per
  question.
- **A request is outstanding until its response is acked**, and the hello
  lists what is outstanding, so the next hub adopts a running turn.
- **Ownership without a lock across a write.** A newer connection replaces
  the older, whose socket is shut down. A hub's line is accepted (owner
  check, bookkeeping, the stdin queue) in one hold of the lock; a
  per-connection writer thread and one stdin-writer thread do the I/O. A hub
  or an agent that stops reading blocks only its own thread, never an
  attach.
- **The exit and the linger.** After the exit the keeper waits until a hub
  has acked it and left (or 10 min). The v3 client ties `alive` to the
  stream, so an adopted turn of an agent that already exited still settles.
- **A socket name per keeper** (`s-<token>.sock`): no successor shares a
  path, so the client unlinks its own socket without an inode check (that
  check failed on Linux CI, where tmpfs reuses inode numbers).
- **Compatibility.** Protocol 2's client paths and its vendored fixture were
  retired on 2026-10-31, once no keeper running protocol 2 was left. A
  keeper that says hello with an older version is refused with a reason
  (`RetiredV2Keeper`), never driven; its orphan watch ends it.

Tests: `KeeperContract` (C1–C12, an in-process keeper with a stub agent),
`KeeperProperty` (seeded random runs of notes, cards, hub requests,
responses, answers, acks, attaches, detaches and crashes, checked against
the invariants above), `KeeperProcess` (a replaced hub gets EOF; a stalled
hub does not block a new attach, and the new hub gets all 3000 lines), and
`V2KeeperCompat` (the new client drives a v2 keeper through a mid-turn
reattach and a clean close). Mutating the keeper (letting a second answer
through, replaying an answered card, not holding an acked card, dropping the
owner check) fails at least one of them.

**Third review, 2026-10-31 (protocol 3).** A two-arm panel (Codex + Grok)
on 65f9ca0 returned `amend` from both arms. A hub restart ran under the Grok
arm's protocol-3 pane mid-review, and its turn was adopted. Each fix has a
test that fails on 65f9ca0 (`KeeperAfterReview3`, `KeeperAfterReview3Grok`,
`ClientAfterReview3`, `ClientAfterReview3Grok`, `TheDeadPaneHasItsReason`):

| Finding | What was wrong | Fix |
|---|---|---|
| Close race (both arms) | The owner check and the close claim were two holds of the lock, so a replaced hub could still close the agent | The close is claimed in the same hold as the owner check |
| Acked before written (Grok) | The pane buffers text (0.15 s) and thought (until the next event); acks covered lines whose content was still in the buffer, so a restart lost it | The pane tells the client which line its buffer starts at, and no ack passes it. Detach writes the buffers out before its final ack |
| Detach mid-apply (Codex) | Detach acked before the reader finished the line in hand, so that line came again outside the crash window | The reader applies each line under a lock that detach takes (bounded, 2 s) before its final ack |
| Held card re-sent (Codex) | Held cards were snapshotted at attach and written without a re-check | Re-checked under the lock just before writing. The window from there to the write remains, so the client also drops a card it already answered |
| Malformed hub line (both) | An unhashable id, an ack of `1e309`, or a too-deep line raised past the reader, which then kept owning the agent | Ids must be str, int or null; the ack catches overflow; parse catches recursion; the reader's cleanup runs in `finally`, and the adapter reader always appends the exit |
| Id type mismatch (Grok) | A reply id `"1"` for request `1` left the request outstanding forever | Requests and replies are keyed by `str(id)`; the hello returns the raw id |
| Dead before its reason (Grok) | The exit set `dead` before the reason was recorded, and the adopt path could set `ready` over an exit | `_dead` sets the state after the reason; both writes share one small lock |
| Line cap (Grok) | A line at the keeper's 16 MiB cap was over the client's 16 MiB cap once framed | The keeper caps adapter lines 4 KiB lower |

**Stdin backpressure (Grok), fixed after the panel.** When the agent
stopped reading stdin and the queue was full, the hub's reader waited in
`accept_from_hub`, so later acks and a `close` on that connection waited
too. The outbox stopped trimming and could drop notifications. Now each
connection has a reader and a drain thread. The reader applies control
lines (ack, close) at once. Another line is accepted as it is read while
the stdin queue has room; only once it is full do lines wait, in order, on
the connection's inbound queue (`INBOUND_MAX_BYTES`), which the drain
accepts from as room appears. Acks and a close wait only once both queues
are full. A hub that goes (a hang-up, or a failed write to it) lets go of
the agent at once, so the orphan watch and the linger see no hub, but its
waiting lines still drain; it drives nothing else. A hub that attaches, or
a close, drops waiting lines, as the old reader's one waiting line always
was. Tests: `StdinBackpressure` (an ack lands within 0.5 s and a close
ends the agent within `TERM_WAIT_S` behind a waiting line, both fail on
35a67fb; order; a replaced hub's waiting lines never reach the agent; lines
read before a hang-up do).

Its own panel (Codex + Grok, on c4fe8f5, both `amend`) found four defects
in the first cut, each now with a test that fails on c4fe8f5
(`StdinBackpressureAfterReview`):

| Finding | What was wrong | Fix |
|---|---|---|
| Lost on a write failure (Codex) | A failed write after a clean hang-up dropped the lines the hub had already sent | A hub that goes keeps draining its waiting lines |
| Orphan watch blocked (both) | A hung-up hub whose drain waited on a wedged agent kept owning it, so the orphan watch never fired | A hub that goes lets go of the agent at once |
| Answer lost on a replace (Grok) | Every data line went through the inbound queue, so an answer already read could be dropped by a replacement on a healthy agent and its card raised again (19 of 100 runs) | A line is accepted as it is read while stdin has room; only lines read while it is full wait |
| Close left the hub owning (Grok) | A close never woke the waiting drain and never let the connection go | The close claim notifies the drain, which drops the waiting lines; the reader's clean end lets go |

*The two reviews below record protocol 2's fixes. Protocol 3
(above) replaced the mechanisms they describe: the spool, the carried hello
lines, the in-flight requeue, `drive_lock` and the inode guard are gone. The
findings stay covered by the contract tests.*

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

**Second review, 2026-10-31.** A two-arm panel (Codex + Grok) on the fixed
keeper returned `amend` with eleven findings. Each fix below has a test that
fails on c30c880 and passes now (`TheKeeperAfterReview2`,
`TheExitedAgentsTurnSettles`, `PanelAfterReview2`):

| Finding | What was wrong | Fix |
|---|---|---|
| F4/F5 | A card queued for a stalled hub was spooled AND carried in the next hello, so the next hub raised it, saw the spooled copy as a duplicate, and answered the agent "cancelled" | A line the hello carries (an unanswered card, an unacked response) is never spooled or replayed again. A keeper-backed client drops a replayed duplicate card silently |
| F2/F3 | A replaced hub's queue and in-flight line were lost or landed behind the new hub's replay | Dropping a hub moves its in-flight line, then its queue, into the spool in order, before the next hello |
| F1 | A replaced hub could still write one already-read command, or close the agent | The owner check and the write to the agent share `drive_lock`; attach takes it before swapping owners |
| F6 | A turn that finished while no hub listened, then the agent died, was never settled | The outstanding prompt is adopted whether or not the agent still runs; `dead` is never overwritten |
| F9 | The keeper quit once an exit frame was QUEUED, even if no hub read it | The hub acks the exit (`ack-exit`); the keeper lingers until an ack arrives and that hub has gone |
| F10 | The orphan watch could end an agent a hub had just reattached to | The decision and the close claim are one hold of the lock; attach refuses once a close is claimed |
| F8 | An old client's close could unlink a successor keeper's socket | The client unlinks only the socket inode it connected to |
| F11 | The `/tmp` fallback dir was taken on trust | `lstat`: a real dir, ours, mode 0700, or no keeper for that pane (it still works; a restart ends it), said loudly |
| panel | The synthesizer was the caller's own lane, and the packet named the host | The CLI always sends the calling pane; its lane neither arms nor synthesizes. No host name in `packet.md`/`packet.json` |
| panel r2 | Round 2 kept round 1's synthesis, and arm panes were titled with their prompt | Each round is synthesized afresh into `synthesis-rN.md`; panes are titled `panel <slug> · <lane>` (`PanelAfterReview3`) |

**F7, fixed: `corral-light stop`.** `KillMode=process` means `systemctl
--user stop corral-light` also leaves every keeper running, until its
six-hour orphan watch ends it, and the hub cannot tell a stop from a
restart (both send SIGTERM). `corral-light stop` (`stop.py`) stops the
service first, so the hub lets go of its agents as on a restart, then sends
every recorded keeper the same `{"keeper": "close"}` the hub sends on close,
and keeps each connection reading until that keeper exits. A keeper that
does not answer its socket is signalled, but only once `ps` confirms the pid
is still a `keeper.py`. It refuses from inside a pane, and while a hub that
is not a service still runs. Tests (`test_stop.py`): every keeper and its
agent are gone within 10 s; the service stops before any keeper is closed;
both refusals; a reused pid is never signalled.

**Deferred, with a plan: a close sent just before a hang-up can be lost.**
Found while building the stop verb: a hub that sends `close` and hangs up at
once loses the close in about one run in three, because the keeper's writer
hits the broken pipe first, drops the connection (shutting the socket down)
and the reader never reads the close. It predates the split reader. The hub
is not exposed (its close also signals the process group) and `stop.py`
keeps reading until the keeper exits, so nothing ships with the hole open.
Plan: a hub that GOES keeps its reader to EOF (shut the socket down for
writing only), and that reader may still apply a close or data lines until
another hub attaches. Falsifier: connect, read the hello, send close and
close the socket at once; in 50 runs the keeper exits within `TERM_WAIT_S`
+ 1 s every time (today about a third hang until the orphan watch). It
moves the owner rule again, so it gets its own review.

## The panel (`panel.py`)

**Where sealed files go.** By default `<cwd>/reviews/<date>-<slug>`. A
caller's `out_dir` wins; otherwise `~/.config/corral-light/panels.json`
(`CORRAL_PANELS_CONFIG`) can send panels run under a path prefix somewhere
else, so a repo whose reviews name local paths never holds them:
`{"out_roots": [{"cwd": "~/src/repo", "root": "~/private/panels"}]}`. A
config that exists but cannot be read refuses the panel rather than fall
back into the repo (`PanelOutRoots`).

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
