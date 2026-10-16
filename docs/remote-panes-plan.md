# Panes on another host, from the picker — plan

Status: PLAN, v1, 2026-10-16. Nothing built. Reviewed in one round by a
three-vendor panel (Codex, Grok, Gemini); the charge, the v0 draft and the
answers are in `reviews/2026-10-16-remote-panes-panel/`. All three chose A
over B, and all three cut it down for v1. §3 is rewritten from their
findings. §5 lists the decisions the operator must make before Phase 0.

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

**What the panel changed.** `drive` is remote code execution as the peer's
user, with that machine's logins, `gh` and SSH agent. Folder roots set the
starting directory; they do not contain the agent. Card answers are what
gate those tools, so a driving hub that can answer cards can do anything
the agent can. That is materially more than `takeover`, which copies one
repository and runs the work elsewhere. The grant's notice and HUB-LINKS.md
must say this in those words.

**R1. A new grant, `drive`, split in two.** Granted locally, never a
default, with a security notice and a ledger line.

- `drive` (v1): list lanes, open a pane, send prompts, cancel and close
  panes this peer opened. Nothing else.
- `drive-answer` (later, a separate decision): answer those panes'
  permission cards and ask_human questions from the driving wall. Until
  then, cards are answered at the peer's own wall, as HUB-LINKS.md says
  today. That wall is a browser tab away on the LAN.

Ownership is persistent: (peer id, pairing generation, pane id) in the
pane's meta. Every route, including event reads, re-checks ownership and
the current grant at commit and before releasing results. Unpairing or
re-pairing ends driving of old panes.

**R2. Lanes route.** `GET /fed/v1/drive/lanes` returns an explicit,
stripped record per lane: key, label, model options, effort options,
posture options, and the peer's allowed roots. No `why` text, since today's
`why` strings carry absolute paths. Shell lanes and lanes whose posture is
not enforced are left out.

**R3. Picker.** The Host select becomes its own dimension, filled
independently of lane groups: this machine, then each peer with `drive`.
Picking a peer refills Agent, Model and Effort from that peer's lanes and
the folder suggestions from its allowed roots. Own branch, Review at the
end, attachments and Later are hidden for a remote host in v1.

**R4. Open.** `POST /fed/v1/drive/open` carries a client-made mutation id;
a retry with the same id returns the first result instead of a second pane.
The folder is resolved with `realpath` on the peer and must lie under an
allowed root. A driven pane gets no seat tools, so it cannot message the
peer's other panes. `allow_always` is stripped from its cards, as on
own-branch panes, so it cannot write lasting permissions into a checkout.

**R5. Relay.** One short poll per peer, never one held connection per pane:
`POST /fed/v1/drive/events` with a cursor per pane, the same shape as the
wall's `/api/state?since=`. Batches stay under the response cap. The poll
carries pane state (state, pending card titles, question, error) as well as
events. Polling backs off when nothing changes. This fits the listener's
limits of four connections per address, a six-second call timeout and a
15-second socket timeout, and the 20,000-nonce window. Prompts, cancel and
close each carry a mutation id and are never replayed blindly.

**R6. The mirror.** The driving hub keeps a mirror pane with no process.
It persists the peer, the remote pane id, the pairing generation and the
last cursor. A restart reattaches the relay; it never starts a local agent.
Mirrors do not count against the local live-pane cap. If the peer
restarts, the mirror shows the pane's real state there (detached, dead),
and resume runs on the peer.

**R7. Notifications.** The driving hub raises notices for its mirrors, on
its own quiet-hours clock. The peer stays silent for panes another hub is
driving.

**R8. Still out of v1.** Card answers from the driving wall, own branch,
review and publish, challenges, attachments, quote, fan-out, port, and
takeover of a driven pane (refused, and the pane is badged in the hub
dialog). Credentials, signing, trust changes, shell lanes and seat tools
never cross.

## 4. Phase 0

1. Pair two hubs on the LAN with `see` and `watch`, and confirm the
   existing routes work between the two machines.
2. Measure the short-poll relay's latency and nonce use against a busy
   streaming pane, not an idle one.
3. Falsifiers. Each one stops the design if it fails:
   - A `drive`-only peer approves a tool call, answers a question, or
     pushes.
   - A driven pane's agent reaches another pane on the peer through seat
     tools.
   - A retried open creates a second pane, or a retried prompt is
     delivered twice.
   - A driven pane, its relay and a roster poll starve an operator command
     of a connection.
   - A path under an allowed root that symlinks outside it is accepted.
   - A restart of the driving hub starts a local agent for a mirror.

## 5. Decisions for the operator

1. **Option A, as a prompt relay in v1.** Cards stay answered at the peer's
   own wall, so the two HUB-LINKS rules stand unchanged for v1.
2. **Whether `drive-answer` should come later at all.** It would let one
   wall answer the other machine's cards. The panel's condition is a
   per-card instance id bound to the agent's generation, an atomic check
   and consume, and an idempotent answer.
3. **Folder roots for driving** start empty; the operator lists them on
   each hub that may be driven.
4. **Which hubs to pair**, and which may drive which.
