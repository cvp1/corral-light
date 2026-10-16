You are reviewing a design before any code is written. Be adversarial. Do not edit any files; answer in text only. You may read the repo at ~/corral-light (hublink.py, docs/HUB-LINKS.md, sessions.py, static/app.js) to ground claims, and cite file:line for any claim about existing code.

Questions:
A. Is the recommendation (A: pane lives on the peer hub, this wall drives it via a new `drive` grant) right over B (agent process over SSH, pane local)? Steelman B.
B. Security: what does a `drive` grant let a compromised driving hub do, and is that materially worse than today's `takeover`? Is the R5 digest-bound card answer sound (races, replay, card ids)? Anything in R1-R7 that leaks or escalates?
C. The relay (R4): long-poll over the existing signed request path. Will the existing nonce/replay store, connection caps and 5-minute clock window hold up under continuous long-polling? Better shape?
D. What is missing or underspecified (lane model pickers, attachments, quiet hours/notifications, hub restart on either side, takeover of a driven pane, own-branch panes)?
E. What would you cut from v1?

Format: numbered findings, most severe first. Each: severity (blocker/major/minor), concrete scenario, fix. Then a one-line verdict. Under 900 words.

--- THE PLAN ---
# Panes on another host, from the picker — plan

Status: DRAFT, v0, 2026-10-16. Nothing built. Not yet reviewed. §5 lists
the decisions the operator must make before Phase 0 starts.

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

## 3. Design for A

**R1. A new grant, `drive`.** Granted locally, like every grant, with
`corral-light hubs grant PEER --allow …,drive`. It implies nothing else; it
is never a default; granting it raises a security notice. It lets the peer:
list this hub's lanes, open a pane here, send input to panes it opened,
answer their permission cards, cancel and close them. It does not reach
panes the peer did not open.

**R2. Lanes route.** `GET /fed/v1/lanes` (needs `drive`) returns the result
of `available_agents()` minus shell lanes, with no paths. The picker adds
one group per peer with `drive`, so `#f-host` lists this machine and each
such peer. Lane keys from a peer are namespaced `hub:<peer-id>:<lane>`.

**R3. Open.** `POST /fed/v1/drive/open` with lane, a folder, model, effort,
posture and the first prompt. The folder must lie under a root the peer's
operator allowed for driving (new config, empty by default). Shell lanes
are refused, as today. The pane records who opened it, for the audit ledger.

**R4. Relay.** The driving hub keeps a mirror pane in its own state that
holds no process. It streams the remote pane's events over the link (long
poll on `/fed/v1/drive/events?pane&since`, signed like every request) and
forwards input, cancel and close. When the link drops, the mirror shows it
as unreachable and resumes from its last sequence number.

**R5. Cards.** A permission card from a driven pane shows on both walls with
the same payload and digest. An answer from the driving wall carries the
card id and the digest it was shown; the peer refuses an answer whose digest
does not match its own card, and the first answer from either wall wins.

**R6. Reviews.** The diff and the review card render from the peer, through
the relay. Push, merge and discard run on the peer, bound to the reviewed
tree as today.

**R7. What still never crosses.** Credentials, signing, trust changes,
shell lanes, and the seat tools. No agent on either hub can reach these
routes. A driven pane cannot open further driven panes.

## 4. Phase 0

1. Pair this hub with a second hub on the LAN, with `see` and `watch`, and
   confirm the existing routes work between the two machines.
2. Measure event latency of a polled relay against the wall's own stream.
3. Prove R5's digest check refuses a stale or forged answer.

## 5. Decisions for the operator

1. **Option A over B**, including the two HUB-LINKS rules it changes for
   panes opened under `drive`.
2. **Folder roots for driving** start empty; the operator lists them on each
   hub that may be driven.
3. **Which hubs to pair**, and which may drive which.
