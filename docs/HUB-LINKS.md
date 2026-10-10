# Hub links — hubs that see, hand over and delegate work

Two Corral Light hubs on two machines, one operator. Until now neither could
see what the other was doing, and moving work meant a mailbox and a cron
poll. Hub links let a hub:

| You want… | Do this | Grant needed on the other hub |
|---|---|---|
| See what the other hub is working on | `corral-light hubs ls`, or ⇆ on the wall | `see` |
| Read one pane's recent turns and its diff | `corral-light hubs show PEER PANE [--diff]`, or **Look** | `watch` |
| Move a pane here and keep going | `corral-light hubs take PEER PANE --cwd ~/your/checkout`, or **Take over** | `takeover` |
| Hand a task to the other hub | `corral-light hubs offer PEER "…"`, or **Offer work** | `delegate` |

Hub links are **off** until you turn them on, on each machine.

## Setting up two hubs

On each machine, at that machine:

```
corral-light hubs enable --bind <this machine's LAN or tailnet IP> --name office
```

On the machine whose work you want to reach (say the office):

```
corral-light hubs invite --allow see,watch,takeover
```

It prints a short pairing code, like `7K2M-QF9D-XB3P`, good once for ten
minutes (`--ttl` up to thirty). On the other machine, type it:

```
corral-light hubs join 192.168.1.31 7K2M-QF9D-XB3P --allow see,delegate
```

The code never crosses the network. Each hub proves it knows the code with
an HMAC bound to both TLS certificates, so a machine in the middle cannot
relay or reuse it, and nothing is stored unless both proofs check. If you
can copy and paste between the machines, the `clhub1:` token the invite
also prints does the same and pins the certificate up front.

Each side's `--allow` is what the *other* hub may do *here*. Both print the
same check code; compare them if you like. Change grants later with
`corral-light hubs grant PEER --allow …`; unpair with `corral-light hubs forget PEER`.

The listening port (default 8097) must be open in each machine's firewall
to the other one, for example with ufw:

```
sudo ufw allow from 192.168.1.0/24 to any port 8097 proto tcp
```

## What each verb does

**See** returns titles, lanes, states, idle time, waiting-card counts, the
folder name (not the path) and the branch. No conversation content.

**Watch** returns the last six turns of one pane, what it is asking, the
titles of cards waiting there, and the diff of its branch or checkout. This
is content leaving the machine, so it is its own grant.

**Take over** is two-phase:

1. **Reserve.** The source hub records the transfer before it stops
   anything, so the pane cannot be reopened there mid-handover.
2. **Pack.** It stops the pane (with `--interrupt`, or **Take over** on a
   busy pane, it cancels the running turn first; otherwise a busy pane is
   refused), then snapshots its code: the own branch or the checkout, with
   uncommitted changes and untracked files that are not ignored, as a git
   bundle. It uses a temporary index and a temporary ref, so the source
   checkout, its index and its branches are not touched. If the code or the
   transcript cannot be packed, the pane is reopened there and nothing moves.
3. **Stage.** The taking hub stores the package, archives the transcript,
   and, given `--cwd`, fetches the bundle into your checkout as
   `corral/from-<hub>/<branch>-<id>` with a worktree of its own under the
   hub's state directory. Your checkout's working tree is not touched.
4. **Confirm.** The taking hub tells the source it has the package. Confirm
   and reclaim are mutually exclusive on the source: exactly one wins. From
   a confirmed transfer on, the source refuses to reopen that pane.
5. **Activate.** Only after the confirmation does the taking hub open a live
   pane on the same lane and send it the handoff pack: the original ask, the
   newest whole turns, where it stopped and where the code is, framed as
   context written on another hub.

Every step is recorded, so `corral-light hubs fetch TRANSFER` continues from
wherever an attempt stopped. That covers an answer lost to a sleeping hub, a
confirmation that did not get through, and code saved without `--cwd` that
you now want checked out. Until a transfer is confirmed, the source operator
can `corral-light hubs reclaim TRANSFER`; a reclaimed transfer never starts a
pane on the taking side.

**Offer** puts a task in the other hub's inbox. Nothing runs until the
operator *there* accepts it (`corral-light hubs inbox`, `accept`, or the
wall's ⇆). Accepting opens a pane and sends the task, framed as a request
written elsewhere (P20). The sender follows it with `corral-light hubs offers ID`,
which shows the pane's state and, with `watch`, its answer. An offer to a
sleeping hub waits in an outbox and is retried with backoff until it lands;
a retried offer is still one offer.

## What never crosses a link

- **Permission cards are answered on the hub that shows them.** Watch shows
  that a card is waiting and its title; the answer stays at that hub's wall,
  where the payload and its digest are shown (P17).
- **No typing into another hub's panes.** Take over moves the work; it does
  not drive the pane where it was.
- **No credentials, no signing.** Only the pair key exists, and it never
  leaves the two hubs' state directories.
- **No shell takes remote work.** Offers and takeovers refuse SSH and other
  shell lanes, where text would run as a command.
- **No agent reaches these routes.** The seat tools stay on their own hub;
  every hub-link action is the operator's, from the paired CLI or browser.
- **Trust changes are local.** Enable, invite, join, grant, forget and
  address changes are refused unless the request comes from the machine
  itself (loopback, no proxy or Tailscale Serve header).

## Security model

- **Identity**: each hub makes a self-signed EC P-256 certificate (`openssl`,
  key 0600) and a random hub id. A peer is its certificate fingerprint,
  pinned at pairing. No CA, no hostnames.
- **Pairing**: a 60-bit one-time code, typed by the operator. The joiner
  sends an HMAC of the code bound to both certificates, never the code; the
  inviter answers with its own proof, which the joiner checks before it
  stores anything, along with its fingerprint and a fresh 256-bit pair key,
  all inside TLS. Wrong codes are capped per address and overall; past the
  cap, pairing is refused outright rather than evicting anything.
- **Every request** after pairing is mutual TLS (1.2 or later): each side
  checks the other's certificate against the fingerprint pinned at pairing.
  The listener trusts only the paired hubs' own certificates. Each request
  also carries an HMAC-SHA256 over the protocol, method, path, timestamp,
  nonce, body digest, sender id and recipient id. Requests more than five
  minutes off the clock, replayed nonces, requests addressed to a different
  hub, and signed requests on a connection without that hub's certificate
  are refused. Nonces are kept on disk, so a restart does not reopen a
  replay window.
- **Pairing generations.** A hub id that is already paired cannot be paired
  again until the operator forgets it, and every transfer and offer is bound
  to the pairing it was made under, so a new key never inherits old work.
- **Commit points re-check.** A takeover re-reads the grant when it commits,
  so a grant revoked mid-request stops it; confirming a takeover also needs
  the `takeover` grant.
- **Exposure**: the listener binds one address you name, never every
  interface. It caps concurrent connections overall and per address, and
  caps request size. The handshake runs on the connection's own thread.
  Wrong pairing codes are capped per address and overall. The inbox is
  capped per peer, and decided offers are dropped after two weeks. The
  ledger rotates at 5 MB.
- **Audit**: `STATE/hubs/ledger.jsonl` records every pairing, grant, refusal,
  takeover and offer. Pairing, grant changes, unpairing, takeovers of your
  panes and incoming offers also raise a security notice.

Files, all under `STATE/hubs/` (mode 0700, files 0600): `hub.key`, `hub.crt`,
`id.json`, `config.json`, `peers.json` (contains pair keys), `invites.json`,
`inbox.json`, `outbox.json`, `transfers-in.json`, `transfers-out.json`,
`roster-cache.json`, `ledger.jsonl`, `in/<transfer>/`, `worktrees/<transfer>/`.

## Limits worth knowing

- **A takeover carries the whole repository.** The bundle holds the
  checkout's history and every uncommitted change in that checkout, not only
  the pane's own files. Grant `takeover` to hubs you would hand the whole
  repository to. An own-branch pane is the cleanest unit to move.
- **The snapshot is of one moment.** Closing the pane stops its agent, not
  other panes, editors or processes writing the same checkout. Ignored files
  do not travel. In a repository with submodules only the pinned commits
  travel, and the takeover says so.

- The machine running the hub must be reachable from the other one on the
  port you chose (default 8097). Over a LAN this is the LAN address; a
  tailnet address works the same way.
- A taken-over code bundle is capped at 24 MB. Above that the hub sends a
  thin bundle against the branch's base commit, and your checkout must
  already have that commit (fetch from the remote first).
- The handoff carries the transcript, not the vendor's session: the new pane
  continues from the pack, as a port does. The full transcript is archived
  on the taking hub, searchable with `corral-light search`.
- Taking the work back is another takeover in the other direction; that
  needs the `takeover` grant both ways.
