# Panes on another host, from the picker — plan

Status: BUILDING, v1.3, 2026-10-16. Phase 1 built on branch `remote-panes`: §6 records what exists and what was measured. Reviewed in one round by a
three-vendor panel (Codex, Grok, Gemini); the charge, the v0 draft and the
answers are in `reviews/2026-10-16-remote-panes-panel/`. All three chose A
over B, and all three cut it down for v1. §3 is rewritten from their
findings, then amended by the operator's ruling on what `drive` is. §5 lists the decisions the operator must make before Phase 0.

**What this adds.** In the New conversation dialog, the operator picks an
agent (Claude, Codex, Grok, Gemini) and a **host**. The pane runs on that
host, in a folder on that host, and the operator works in it from this wall
like any local pane: types into it, answers its permission cards, reviews
its diff.

**Why.** Each machine has its own repositories, toolchains and logins. Today
the only ways to reach another machine are a raw SSH shell lane (no agent,
no rail), and hub links (see, look, take over, offer). Take over moves the
work here. Offer hands it to the operator over there, who must accept it at
that wall. Neither lets one operator, at one wall, start and drive an agent
on the other machine.

## 1. What exists

- **The picker** (`static/index.html` `#newdlg`, `wireDialog()` in
  `static/app.js`) already has a Host select, `#f-host`. It is filled only
  for a lane group. Today the only group is SSH shells (`host:<name>`).
  Agent lanes are always local.
- **SSH host lanes** (`ssh_acp.py`) run a plain shell on another machine.
  Text typed is a command. They are refused anywhere an agent is expected.
- **Hub links** (`hublink.py`, `docs/HUB-LINKS.md`): paired hubs over mutual
  TLS with pinned certificates and an HMAC on every request. Grants are
  `see`, `watch`, `takeover`, `delegate`, set locally by the operator of
  the hub that grants them. `see` returns a roster of panes. Nothing returns
  the peer's lanes.

## 2. Two ways to build it

**A. The pane lives on the other hub; this wall drives it.** This hub asks
the peer hub to open the pane, over the existing link. The peer runs it with
its own lanes, logins, worktrees, review sandbox and transcripts. This wall
shows it and relays the operator's input and card answers.

- For: every pane feature works unchanged, because the pane is an ordinary
  pane on its own hub. No new SSH trust. No credential crosses. The pane is
  also visible and drivable at the peer's own wall. The link's
  authentication, replay protection and audit ledger already exist.
- Against: it reverses two rules in HUB-LINKS.md, "no typing into another
  hub's panes" and "permission cards are answered on the hub that shows
  them". It needs a streaming relay of pane events across the link, which
  does not exist (roster and Look are polled snapshots).

**B. The pane lives here; only the agent process runs over there.** This hub
starts the agent's ACP adapter on the other machine through SSH and speaks
ACP over the SSH channel. The pane, its transcript and its cards stay here.

- For: no hub-link rule changes. The permission rail never leaves this hub.
- Against: the other machine needs the adapter, the vendor CLI and a login,
  managed outside Corral. A new SSH key must be able to start agents there,
  which needs a forced-command gate that runs a server-built argv, never the
  client's string. Own branch, diff review, the review sandbox and
  challenges all assume a local checkout, so they would be refused for
  these panes in v1, as they are for shell lanes today. Every hub restart
  drops the SSH channel.

**Recommendation: A.** One operator owns both hubs, and the grant is set at
the hub that runs the work, by that operator, at that machine. That keeps
the rule that matters ("trust changes are local") while changing the two
that assumed two different people. B duplicates half of Corral for remote
paths and still ends with a weaker pane.

## 3. Design for A, v1

**What `drive` is.** `drive` lets the driving hub act as the operator's
own user on the peer machine: open agents there, prompt them, and answer
their permission cards and questions. That is the intent. The operator is
that user on that machine, and the grant is set by the operator, at that
machine, like every grant. The panel called it remote code execution as
the peer's user, with that machine's logins; that is an accurate
description of the feature, not a defect in it. The grant's notice and
HUB-LINKS.md say so in those words, so nobody grants it to a hub they would
not hand a login shell to. It is never a default.

Operator rulings, 2026-10-16: "The intent is to act as my user on that
host. Because I AM that user on that host." And: "Treat this like ssh
access. Essentially, it is, we are just removing the need to ssh over and
start claude in the ssh session. We want this connection to be as robust,
efficient, and fast as possible." So `drive` is held to the bar of an SSH
login: one strong, pinned, mutually authenticated channel, no extra
friction once it is up, and no more exposure than SSH to that user. The panel's v1 cut that kept
card answers at the peer's wall is overruled. The panel's correctness
findings stand.

**R1. The `drive` grant.** Granted locally, with a security notice and a
ledger line per open, prompt, answer, cancel and close (ids and digests,
never prompt text). It lets the peer list lanes, open panes, send prompts,
answer permission cards and ask_human questions, cancel, and close, for
panes that peer opened. Ownership is persistent: (peer id, pairing
generation, pane id) in the pane's meta. Every route, event reads included,
re-checks ownership and the current grant at commit and before releasing
results. Unpairing or re-pairing ends driving of old panes.

**R1a. Card answers that cannot hit the wrong card.** This is correctness,
not authority. Today a card is keyed by the adapter's request id, which
restarts with the agent, and answer() compares the digest before taking
the lock. A late answer could approve a later card with the same id and
the same bytes. A driven pane's cards get a hub-minted card id bound to the
agent's generation. The answer is checked and consumed under one lock: the
card is still open, the generation matches, the digest matches, the option
is offered. Answers are idempotent: a retry returns the first outcome. The
first answer from either wall wins, and the other wall sees "answered
there", not an error. A reject never needs a digest.

**R2. Lanes.** The peer sends its lane list on the channel when it opens
and again whenever it changes, so the picker never waits on a request:
per lane, key, label, availability and why, model options, effort options
and posture options, plus the peer user's home and recent folders. Every
agent lane is offered. Shell lanes are left out, because an SSH host lane
already covers a plain shell.

**R3. Picker.** The Host select becomes its own dimension, filled
independently of lane groups: this machine, then each peer with `drive`.
Picking a peer refills Agent, Model and Effort from that peer's lanes and
the folder suggestions from that machine's home and recent folders. Own branch, Review at the
end, attachments and Later are hidden for a remote host in v1.

**R4. Open.** An open carries a client-made mutation id; a retry with the
same id returns the first result instead of a second pane. The folder may
be anywhere under the peer user's home directory, as with an SSH login
there. It is resolved with `realpath` on the peer, so a symlink cannot
take it outside home, and a missing folder is refused rather than created.
There is no per-folder allowlist to maintain. A driven pane is an ordinary
pane on the peer: it keeps its seat tools and the full card options, as a
pane opened at that wall would.

**R5. The channel.** One persistent, duplex channel per pair of hubs,
shaped like an SSH connection, not a series of web requests.

- *Open.* The driving hub dials the peer's existing listener with mutual
  TLS and the pinned certificate, and sends one signed request to
  `/fed/v1/drive/channel`. That request passes the usual checks (grant,
  clock, nonce, recipient). The peer then switches the same connection to
  length-prefixed JSON frames in both directions. Each frame after that is
  authenticated by the TLS session itself, as each SSH packet is by its
  transport, so there is no per-frame signature, nonce or disk write.
- *Why.* Today every hub-link call opens a new TLS connection, appends a
  nonce to disk under the hub lock, and closes; the listener allows four
  connections per address and drops sockets idle for 15 seconds. A channel
  pays the handshake and the nonce once per connection, not once per
  keystroke or token.
- *Down the channel.* Every event of every pane that peer is driving,
  multiplexed by pane and sequence number, plus pane state (state, cards,
  question, error). Token deltas are coalesced into frames of at most
  about 30 ms. `TCP_NODELAY` is on.
- *Up the channel.* Commands: open, prompt, answer, question answer,
  cancel, close. Each carries a mutation id and is acknowledged on the
  channel with its result; a retry of the same id returns that result.
  Every command re-checks the grant and pane ownership when it runs.
- *Liveness.* Heartbeats both ways every 10 seconds; 30 seconds of silence
  ends the channel. Revoking `drive`, forgetting the peer or re-pairing
  closes it at once.
- *Reconnect.* The driving hub redials at once, then backs off with jitter
  from a quarter second up to five seconds, and redials immediately on
  network change or wake from sleep. It resumes with a cursor per pane.
  The peer replays from each pane's event log; if a cursor is older than
  the log keeps, it sends a snapshot and says so. No event is lost or
  shown twice.
- *Limits.* A channel has its own slot, outside the per-address cap, so
  the other hub-link calls never starve. One channel per peer: a new one
  replaces the old. A small overall cap on channels, and the existing
  frame size cap.
- *What a drop costs.* Nothing on the peer. The agent keeps working there
  while the link is down, and the wall catches up on reconnect. That is
  the robustness gain over SSH plus a CLI, where the session dies with the
  connection unless it was started under tmux.

**R6. The mirror.** The driving hub keeps a mirror pane with no process.
It persists the peer, the remote pane id, the pairing generation and the
last cursor. A restart reattaches the relay; it never starts a local agent.
Mirrors do not count against the local live-pane cap. If the peer
restarts, the mirror shows the pane's real state there (detached, dead),
and resume runs on the peer.

**R7. Notifications.** Both hubs raise their usual notices for a driven
pane: the driving hub for its mirror, the peer for its own pane, each on its
own quiet-hours clock. Silencing the peer was considered and dropped: the
machine an agent runs on keeps telling its own wall what that agent is
doing.

**R8. Still out of v1, for size, not authority.** Own branch, review and
publish, challenges, attachments, quote, fan-out, port, and takeover of a
driven pane (refused, and the pane is badged in the hub dialog). Each can
follow once the relay is proven. Trust changes stay local to each machine.
Credentials never cross: the agent uses the logins already on its machine.

## 4. Phase 0

1. Pair two hubs on the LAN with `see` and `watch`, and confirm the
   existing routes work between the two machines.
2. Measure the channel against a busy streaming pane, on the LAN: added
   latency over a local pane (target under 20 ms at the median), time to
   resume after a dropped connection, and time to resume after the driving
   Mac wakes from sleep.
3. Falsifiers. Each one stops the design if it fails:
   - A peer without `drive` lists lanes, opens a pane, or answers a card.
   - An answer sent for one card approves a different one: a stale card id,
     a restarted agent with the same request id, or two walls answering at
     once.
   - A retried open creates a second pane, or a retried prompt is
     delivered twice.
   - A busy channel and a roster poll starve another hub-link call of a
     connection.
   - Cutting the connection mid-stream loses an event or shows one twice
     after resume.
   - A folder that symlinks outside the peer user's home is accepted.
   - A restart of the driving hub starts a local agent for a mirror.

## 5. Decisions for the operator

Settled 2026-10-16: option A; card and question answers from the driving
wall in v1; any folder under the peer user's home; the trust bar is an SSH
login; the transport is a persistent channel built for speed and resume.

1. **Which hubs to pair**, and which may drive which. Pairing and granting
   happen at each machine, since trust changes are refused remotely.

## 6. Built (branch `remote-panes`)

| Piece | Where |
|---|---|
| The channel: framing, heartbeats, redial, resume, run-once commands | `drive.py` |
| The `drive` grant, the upgrade path, channels outside the listener's slots | `hublink.py` |
| The driven side: lanes, home-confined folders, `driven_by`, commands | `drive_host.py` |
| Card ids and an atomic check-and-take for every card answer | `sessions.py`, `corral_core/sessions.py` |
| The driving side: mirrors, persisted, reattached on restart | `drive_remote.py` |
| Wall routes, state, history and pending cards for mirrors | `hub.py` |
| Machine choice in the New conversation dialog; a header badge | `static/` |
| `corral-light open --host`; remote panes in `corral-light panes` | `cli.py` |

Measured on loopback (`test_drive.py`): event latency 0.1 ms at the
median, command round trip 0.5 ms. Through two real hubs and the CLI
(`test_drive_e2e.py`): open 84 ms, prompt to reply 58 ms, both including
the CLI's own start. One finding on the way: a 30 ms timed queue wait took
about 100 ms on macOS, so batching never waits; it takes only what is
already queued.

Not yet measured: the LAN between two machines, and sleep and wake of the
driving Mac. Those need two paired machines (Phase 0 step 1).
