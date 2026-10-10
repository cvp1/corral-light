# Delegates as the second Corral Light module (and Fleet after it)

> Status: rev 4, 2026-10-10; Phase 1 built the same day (corral-light-delegates 0.1.0, 122 tests; Light's Q7 change on branch delegates-phase1). Phase 0 (a) and (b) measured
> 2026-10-10 (`docs/delegates-phase0.md`). Rev 4 has not been put to the
> panel: round three recommended writing it and then building Phase 1
> without a fourth round, because the remaining risk is in the
> implementation and §12 is what catches that.
>
> **Rev 4** answers panel round three on rev 3: AMEND, AMEND, AMEND
> (`reviews/2026-10-09-delegates-module-panel/synthesis-r3.md`). Rev 3
> fixed the alarm's logic and left its identity rules to the implementer:
> which box a grant names, which source a name belongs to, and how a box
> ever leaves. Rev 4 settles them in §2.1 to §2.5, §4.1 to §4.3 and §12
> without reopening the model:
>
> - **`box:` is required, signed, and has no default.** A charter without
>   it is `bad-charter`. The default was the last place a typo could
>   change what a grant authorises.
> - **A box is `<source-id>/<name>`, and nothing joins boxes by name.** A
>   grant lists the qualified names it covers. `aliases` is gone.
> - **Every box has an exit.** `cli.py gone <box>` writes the operator's
>   gone-record, so a box that only the ranch reports can reach
>   `not-listed` and age out of the ledger.
> - **Tombstones are keyed by `<source-id>/<name>`**, and `issued` has
>   bounds: not after `expires`, not more than 10 min ahead of now.
> - **`unknown` recomputes coverage.** The ledger carries a box's last
>   presence, not its last level.
> - **The inbox `signature` enum is `verified | failed | absent |
>   unverified`**, and inbox grants must carry `issued` and `grant_id`.
>   The ranch converter reads the charter files beside `status.py`,
>   because its JSON has neither `created` nor bytes to hash.
> - **`ended:removed` is back** in the Grants table.
> - **Signatures verify on Linux only in v1.** The macOS sandbox forbids
>   child processes, so on macOS every signed charter reads "cannot
>   verify" until a core-provided verifier exists (Phase 2).
> - **Probe pins carry their address**, and a result for an address no
>   longer pinned is dropped.
> - **A damaged tombstone file is salvaged**, and `ack-ledger` never
>   empties it.
> - Seam references are by name, not line number.
>
> One departure from the synthesis, stated in §2.4: the gone-record is an
> unsigned operator assertion with the same authority as `boxes.toml`,
> not a signed file. Signing it would make the exit unusable on a macOS
> hub in v1, and it protects nothing `boxes.toml` does not already
> expose. No fleet hub runs macOS today: camano is Arch Linux and
> ranch-server is Linux Mint; dogma is the only macOS host.
>
> **Rev 3** answered round two (`synthesis-r2.md`): the box became the
> unit of the alarm; `unknown` could keep an alarm but never quiet one;
> revocation was ordered by a signed `issued` key with tombstones kept
> forever; "complete" became a per-file claim the operator allows per
> source; edit-distance typo detection went; the Phase 2 probe compares
> key blobs under a core-side allow; `core_api` became backward
> compatible. **Rev 2** answered round one (`synthesis.md`): three axes
> per delegate (grant, box, source), a grant ledger, inbox rows recomputed
> on this host's clock, a pasted public key in `setup`, tables split at
> 200 rows, Phase 2 in its own sandbox, and two rev 1 errors corrected
> from Phase 0 (the ranch signs with OpenSSH, not gpg; a bare-date
> `expires` is valid).
>
> Decided by the operator on 2026-10-09: **generic and public**; **the
> board first, lanes later**; **Delegates, then Fleet**.
>
> Decided by the operator on 2026-10-10, after panel round three: **Q2,
> the inbox lives inside the config folder** (`<config>/inbox/`), no core
> bind; **Q7, yes**, in Grok's shape: while a module is failing, the rail
> keeps the `bad` notices from its last good snapshot, a Phase 1 core
> change.

## 0. The ask

Port full Corral's Delegates into Corral Light as a module in its own
repository (`corral-light-delegates`). It runs under the seam and trust
model of `docs/finops-module-plan.md` §3 to §5.

The question the board answers: **which agents have I handed work to on
other machines, what may they do, until when, what do they cost, and is
any box listed that no valid grant covers?**

## 1. What full Corral has, and what carries over

Full Corral's Delegates (read from `cvp1/corral` and the ranch's
`delegates/status.py`, 2026-10-09) has two parts:

| Piece | What it does |
|---|---|
| **The room** (`delegates.py`, 74 lines) | Shows the rows from `delegates/status.py --json` exactly as they arrive. One route, polled every 60 s. |
| **The lanes** (`sessions.py`, the delegate lane block) | One chat-only lane per live delegate-tier box, over ssh to `chat_bridge.py` and the box's broker. |

How the ranch works (Phase 0: read from code, then measured on
2026-10-10, `docs/delegates-phase0.md`):

- **Charters** are `charters/<name>.md`, with flat hyphenated front
  matter and a `created` key, not `issued`. Each is signed by a detached
  OpenSSH signature `<name>.md.sig` from a hardware key, namespace
  `cc-handoff`.
- **States**, worst first: `OVERDUE-REVOKE`, `BAD-SIG`, `LIVE`, `REVOKED`,
  `EXPIRED`, `UNAPPROVED`, `READY`, `DRAFTED`.
- **"Live"** means a `tier == "delegate"` box row exists, keyed
  `<name>-node` on Lightsail first, then `<name>` on GCP. The ranch's
  JSON gives the key it matched as `box.manifest_key`.
- **"Revoked"** comes from audit events, carried in `last_event` as
  `revoke <timestamp>`, where the timestamp can be compact
  (`2026-08-16T000000Z`).
- **`status.py` lists charters, not boxes.** A box with no charter file
  is not in its output, and the output carries no `created`, `issued`,
  `revoked_at` or charter hash.
- **One gap:** a live box with a signed but unapproved charter shows as
  `LIVE`. Rev 4 does not copy that.

Rules that carry over: **the viewer computes nothing**; a **charter TTL
is not uptime**; **a broken source degrades alone**; **there are no
action buttons**; **null is unreported, never zero**.

Not carried over: audit-log attribution, `spend.py`, Lightsail as a
required input, and the lanes (§8).

## 2. The model

A **grant** is a signed charter: what a delegate may do, and until when.
A **box** is a machine that some **source** reports. The board's unit is
the **box**, because the question is whether anything is running that no
grant covers. Each grant is also shown, because the operator manages
grants.

### 2.1 Grants

Each charter (local, or reported by an inbox source) gives one grant, with
a **grant id**. For a local charter it is the SHA-256 of the charter's
bytes, shown shortened. For an inbox grant it is the `grant_id` the
source supplies (§4.3); the module never derives one. Its status is the
first of these that applies, in this order:

| Status | When |
|---|---|
| `bad-charter` | unreadable under §4.1: `name`, `box`, `issued`, `expires`, or both `status` and `approval` missing; `issued` later than `expires`; `issued` more than 10 min ahead of now; a time with no zone; a duplicate key; a malformed key line; a name that differs from the filename. For an inbox grant, also a missing or null `issued` or `grant_id` |
| `bad-sig` | a `.sig` that fails, or one that cannot be checked (labelled "cannot verify: <reason>"; on macOS, every signed charter, §4.1) |
| `ended:revoked` | `status: revoked`; a revocation from any source under the shadowing rule (§4.3); or a tombstone for the same `<source-id>/<name>` whose `issued` is equal to or later than this grant's (§2.4) |
| `ended:expired` | now ≥ `expires`; the ledger has `ended_at` for this grant id; or the ledger's expiry mark for the same `<source-id>/<name>` is equal to or later than this grant's `issued` (§2.4) |
| `ended:removed` | a grant id the ledger knows that no charter file and no inbox source supplies now. Listed for 30 days, then dropped with its ledger entry |
| `unsigned` | approved and signing required, but there is no `.sig` |
| `draft` | not approved: `status: draft`, or `approval` set to `""`, `unsigned`, `pending`, `proposed`, `>` or `|` (case-insensitive). If both `status` and `approval` are present, the more restrictive one wins |
| `valid` | approved, in date (`issued` at most 10 min ahead of now, now < `expires`), and the signature is good (or signing is off) |

For an inbox grant, `valid` also requires the source to report
`approved: true` and `signature: verified`. The row then says "valid per
<source>" (§4.3).

### 2.2 Boxes

A box is `<source-id>/<name>`: the source that reports it, and the name
that source reports. `boxes.toml` is the source `declared`, so a hand
listed box is `declared/<name>`. **Nothing joins two boxes
automatically**: not a shared name across two sources (two machines
called `worker` under two sources are two boxes), and not a name that
differs by a suffix (the ranch's `cal-1-node` on Lightsail and `cal-1` on
GCP are two boxes). One machine that two sources report is two rows
until a grant's `box:` lists both qualified names (§4.1); then the grant
covers both, and both rows say so.

Each box has a **presence**:

| Presence | When |
|---|---|
| `listed` | a fresh source lists it |
| `listed-stale` | only stale sources list it, and no fresh complete source omits it |
| `not-listed` | a fresh complete source that covers it omits it, **or** a gone-record (§2.4) is dated later than every listing of it. **This beats a stale listing**, which stays visible as a note. Only a box the ledger has seen listed, or one with a gone-record, can be `not-listed` |
| `unknown` | none of the above: never listed by any source, or every source that listed it is failed, partial or stale and no fresh complete source covers it |

A source **covers** a box when the box's source id is that source. A
fresh complete `boxes.toml` therefore speaks only for `declared/*`
boxes, and the ranch inbox, which never claims complete (§4.3), speaks
for none. A listing whose `generated_at` is later than a gone-record's
date reinstates the box; the row then says "listed again after the
gone-record of <date>".

### 2.3 Coverage and severity

A box is **covered** when at least one `valid` grant lists its qualified
id in `box:`. A grant that does not list the box never covers it, even
when it is valid.

A box is **in scope** when a source reports it as `tier: delegate`, or
when any grant lists it, or when the ledger ties it to a grant (§2.4).
Other boxes are Fleet's and are not shown.

Presence and coverage are recomputed on every run. The ledger carries a
box's last **presence** (§2.4), never its last level, so an `unknown` box
gets today's coverage applied to the presence it last had:

| Presence | Covered | Level | Label |
|---|---|---|---|
| `listed` | yes | ok | covered by <grant>; box listed |
| `listed-stale` | yes | warn | covered by <grant>; box listed by a stale source |
| `listed` or `listed-stale` | no | **bad** | **box listed without a valid grant (<reason>)** |
| `unknown`, last presence known | per the row above for that presence, with current coverage | "last listed <age> ago by <source>; <that row's label>" |
| `unknown`, never listed | — | warn | "named by <grant>; never listed by any source" |
| `not-listed` | — | info | "box gone: <how>" (a `boxes.toml` line removed, a gone-record of <date>, or omitted by <source>) |

An old `ok` therefore cannot survive its grant expiring while the box is
`unknown`, and an old `bad` cannot survive a valid grant arriving.

`<reason>` comes from the grants that list the box, worst first:
"expired 3 h ago", "revoked 2026-10-02", "charter removed", "never
approved", "signature fails", "cannot verify". If no grant lists it, the
reason is "no charter names this box".

**Alarm continuity.** Once a box has been `bad`, only three things lower
it: a `valid` grant that lists it; a fresh complete source that covers
it reporting it `not-listed`; or the operator's gone-record (§2.4). A
failed, partial or stale source does not lower it. Neither does deleting
a charter, editing `box:`, a tier change, removing a source from config,
or the box becoming `unknown`. The two operator exits are both hand
written config: deleting a `boxes.toml` line (§4.2) and `cli.py gone`.

### 2.4 The ledger, tombstones and gone-records

The ledger and the tombstones are in the data dir. Only the collector
writes them, with a write to a temp file and a rename. The gone-records
are in config, written by the CLI.

- **`ledger.json`** holds, per box (qualified id): every grant id that
  ever listed it; its last presence that was `listed` or `listed-stale`,
  with the time and the source; and the level it last had, kept as
  history and never used to compute severity. It holds, per grant id,
  `ended_at` the first time the grant was seen ended. It holds, per
  `<source-id>/<name>`, an **expiry mark**: the highest `issued` among
  grants of that name seen `ended:expired`, so a re-signed copy of an
  expired charter with the same `issued` and different bytes stays
  ended. That makes the clock running backwards unable to revive an
  expired grant: only a grant with a greater `issued` can reopen the
  delegate. Each box's entries are kept for 30 days after the box is
  `not-listed`. The period is fixed. A zero would be a switch that
  quiets alarms.
- **`tombstones.jsonl`** holds one line per `<source-id>/<name>`: the
  highest `issued` among the grants of that name seen revoked. It is
  kept forever and is small. A restored older or equal-`issued` charter
  stays `ended:revoked`. A local charter and an inbox grant with the same
  name have separate tombstones; the inbox tombstone reaches the local
  grant only through §4.3's shadowing rule, and only when the local
  grant's `issued` is not later than the tombstone's. A renewal with a
  later `issued` and a fresh signature therefore works, and nothing
  wedges.
- **A rename** is a new grant with a new name. The old grant's box is
  covered as soon as the renamed charter, which is valid and lists the
  same `box:`, is read. Nothing wedges.
- **Damage.** A ledger that does not parse is moved aside to
  `ledger.json.damaged-<date>` and rebuilt empty. A tombstone file that
  does not parse is moved aside the same way and **its parseable lines
  are salvaged** into the new file. Either case puts a `bad` row in
  Sources ("ledger damaged on <date>; alarm history lost" or "tombstones
  rebuilt on <date>; <n> of <m> lines salvaged") until the operator runs
  `cli.py ack-ledger`. That verb writes `<data>/ledger.ack` and nothing
  else: it is the one CLI write to the data dir, and it never touches
  the tombstones. After the acknowledgement, Sources shows the rebuild as
  info for 30 days.
- **`gone.toml`** (`<config>/gone.toml`) is the operator's gone-record.
  `cli.py gone <source-id>/<name>` appends `box`, `at` (now, UTC) and an
  optional `note`. The verb refuses a name that is not an in-scope box,
  and refuses a box that a fresh source lists now ("<source> lists it as
  of <age> ago; stop it first"). A gone-record makes the box
  `not-listed` from `at` (§2.2), starts its 30-day ledger clock, and is
  shown in Sources for 7 days, like a `boxes.toml` removal. It is an
  unsigned file with the same authority as `boxes.toml`: both are the
  operator's hand inventory, both lower alarms, and both are writes by
  the same user to the same folder. Round three asked for it to be
  signed when signing is on; rev 4 does not do that, because v1 cannot
  verify on macOS (§4.1) and the exit must work on every hub.
- **What this protects against:** mistakes, stale tools and restored
  files. It does not protect against the operator, because same-user code
  can delete the data dir. The docs say so.

### 2.5 Time

- `expires` and `issued` are each one of: RFC 3339 with `Z`; RFC 3339 with
  an offset (`+00:00` or `-0700`); or a bare `YYYY-MM-DD`. For `expires`,
  a bare date means 23:59:59Z that day; for `issued`, it means 00:00:00Z.
  A date-time with no zone is `bad-charter`.
- **`issued` has bounds.** `issued` later than `expires` is `bad-charter`.
  `issued` more than 10 min ahead of now (the inbox skew limit below) is
  `bad-charter`; within 10 min it is accepted. `issued` is compared only
  against the tombstone and the expiry mark of its own
  `<source-id>/<name>`.
- Ended means `now >= expires`, on the collector's UTC clock, and is then
  latched in the ledger (§2.4).
- If the clock runs more than 5 min behind the last run's `generated_at`,
  a clock warning goes into Sources and countdowns show "(clock
  uncertain)".
- An inbox `generated_at` more than 10 min in the future fails that
  source. A colon-less offset (`-0700`) is accepted.
- The ranch converter parses audit timestamps in both the RFC 3339 form
  and the compact `2026-08-16T000000Z` form (§4.3).

## 3. Trust model

The collector gets what FinOps §3 allows: system dirs read-only, the
config folder read-only, the data folder writable, no network, and no
home directory. The inbox is a subfolder of config (§4.3, Q2 decided), so
the collector already sees it read-only and no new bind is needed. **The
module CLI runs in the same sandbox,** with config writable and no view
of `~/.ssh` (`modules.py`, the CLI mount list: `fetched` is appended to
the read-only list before the interactive branch, and `HOME` is the
data dir, `module_sandbox.py`). No module path touches a private key.

**On macOS the collector cannot run a child process.** The Seatbelt
profile denies process-fork (`module_sandbox.py`, the Darwin base
profile; `docs/finops-macos-sandbox.md` §3). So v1 verifies signatures
on Linux only. On macOS every signed charter is `bad-sig: cannot verify
(no subprocess in this sandbox)`, the Covered tile's subtitle says
"signatures unverifiable on this host", and Sources carries one warn
row saying so. That is honest and not reassuring. The live hub (camano)
runs Arch Linux under bubblewrap and verifies; a macOS hub waits for
Phase 2's core-provided verifier (§7, §11).

**The board is not proof.** "Box listed" means a source says a box
exists. It does not mean the box is up, or is the machine the charter
meant, or obeys the charter. Every row names its sources and their ages.
The honesty line is the first note **and** the subtitle of the "Covered"
tile.

**Separation is not isolation, and here there is no separation.** With
the inbox inside the config folder (Q2), the module CLI's writable config
mount covers it, so `setup` could write inventory, and a converter that
can write the inbox can also write charters. The operator chose this
because the same user can write both folders either way, and a core
bind would have bought a boundary around the public module only. The
module's docs say so plainly, and `doctor` warns if a file in `inbox/`
is newer than the last collector run while `setup` was the last CLI
command to run.

## 4. Sources

### 4.1 Charters (`<config>/charters/<name>.md`)

The config folder is `dirname($CORRAL_MODULE_CONFIG)`.

```markdown
---
name: cal-1
status: approved
issued: 2026-10-01T00:00:00Z
expires: 2026-11-01T00:00:00Z
box: [cal-1, ranch/cal-1-node]
data-class: calendar-read
capabilities: [calendar.read, mail.draft]
budget-usd: 5
cost-box-usd-month: 5.00
---

What this delegate is for, in the operator's words.
```

**Grammar.**
- The file is UTF-8. CRLF is read as LF for parsing only; the signature
  covers the exact bytes, so a file signed as CRLF verifies and a CRLF
  copy of an LF-signed file does not.
- The front matter starts at byte 0 between two `---` lines.
- Each line is `key: value`, with the key matching
  `[a-z][a-z0-9_-]*`. `_` is folded to `-`.
- A non-blank line that is not a valid key line, or not a comment (`#`
  at the start), is `bad-charter`. `Status:` is one.
- A value is a bare string, a `"…"` string, or a `[a, b]` list.
- A duplicate key, after folding, is `bad-charter`.

**Required:** `name`, which must match the filename stem and
`^[a-z][a-z0-9-]{0,60}$`; `box`; `issued`; `expires`; and at least one
of `status` (`draft | approved | revoked`) or `approval` (the ranch's
form). A misspelled security key therefore shows up as a missing one,
and a misspelled `box` (`boxx:`, `bxo:`) is a missing `box`, so the
charter is `bad-charter` and covers nothing. There is no default.

**`box`** is one qualified name or a list of them. A qualified name is
`<source-id>/<name>`. A bare name is `declared/<name>`, the box of that
name in `boxes.toml`, and nothing else: `box: cal-1` does not cover the
ranch's `ranch/cal-1-node`, and `box: ranch/cal-1` does not cover
`gcp/cal-1` from a Phase 3 fetcher. The row shows every qualified name
the grant lists.

**Understood:** the keys above, `data-class`, `capabilities`, `tools`,
`budget-usd`, `credential-N-type`, `cost-box-usd-month`, `cost-note` and
`created`. **Every other key** is kept and shown under "other". There is
no typo guessing.

**Signatures.** The collector reads the charter once, then verifies and
parses the same bytes:

```
ssh-keygen -Y verify -f <config>/allowed_signers -I <principal>
           -n <namespace> -s <name>.md.sig     # charter bytes on stdin
```

- `principal` and `namespace` are set per charter folder in
  `config.toml`. The default namespace is
  `corral-light-delegate-charter`; a ranch folder uses the ranch's own,
  `cc-handoff` (Phase 0, measured from the archived signatures: namespace
  `cc-handoff`, hash `sha512`, key type `sk-ssh-ed25519@openssh.com`).
- Any key type `ssh-keygen` accepts is fine, including `sk-ssh-ed25519`.
  Verifying needs no hardware.
- `ssh-keygen` gets a 5 s timeout. If it is missing, times out or meets
  an unsupported key, the grant is `bad-sig`, labelled "cannot verify".
- **Linux only in v1.** On macOS the collector does not try: every
  signed charter is `bad-sig: cannot verify (no subprocess in this
  sandbox)`, with one warn row in Sources (§3).
- The cache key is the SHA-256 of the charter, the signature,
  `allowed_signers`, the namespace, the principal and the `ssh-keygen`
  version.
- `require_signature` defaults to `yes` once `allowed_signers` exists.
  While it is `no`, the Covered tile reads "covered (unsigned charters)".
- **The module checks `namespaces=` itself.** Measured (Phase 0b):
  `ssh-keygen -Y verify` accepts an `allowed_signers` line that has no
  `namespaces=` option, for any namespace. `setup` always writes the
  option, and the collector refuses a line without it as `bad-sig`,
  "cannot verify (allowed_signers line has no namespaces=)", with a note
  in Sources. A principal with no line in `allowed_signers` is reported
  as "principal not in allowed_signers" by the module, since `ssh-keygen`
  gives no reason.

**Revoking** means `status: revoked`, a newer `issued` and a fresh
signature. **Renewing** means a newer `issued` and `expires` and a fresh
signature. **Deleting** a charter never closes a box (§2.3).

### 4.2 Declared inventory (`<config>/boxes.toml`)

The operator lists boxes by hand. Each has `name`, `tier`, `provider`,
`since` and `cost_usd_month`. It is a **complete** source with the id
`declared`, and its boxes are `declared/<name>`. There is no `aliases`
key: a machine that another source also reports is listed in a grant's
`box:` under both names (§4.1). **Deleting a line is the operator saying
the box is gone.** That lowers an alarm, which is the operator's call to
make. Sources shows "removed from boxes.toml by hand: cal-1" for 7 days,
and the ledger keeps the history. Probe targets are not configured here
(§4.5).

### 4.3 The inbox (`<config>/inbox/`; §9 Q2, decided)

Outside tools drop JSON files here. `setup` creates the folder (0700)
inside the config folder, so the collector sees it read-only through the
existing config bind and **no core change is needed**. The CLI's writable
config mount covers it too (§3). The earlier design, a core-mounted
`<state>/module-inbox/<name>/` bound for the collector only, is recorded
in `synthesis-r2.md` and `synthesis-r3.md` and can be revisited if a
second module needs an inbox.

**Sources are declared** in `config.toml`. Each has an `id`, a `file`, a
`label`, `stale_after_s` (default 7200), and `may_claim_complete` (default
`no`). An undeclared file is ignored and listed as "undeclared file".
Boxes in a source's file are `<id>/<name>`, and a bare name in one of
its grants' `box` means a box of that source.

```json
{"schema": "corral-light.delegates.inbox/1",
 "generated_at": "2026-10-09T18:00:00-0700",
 "complete": false,
 "boxes":  [{"name": "cal-1-node", "tier": "delegate", "provider": "lightsail",
             "since": "2026-08-16",
             "cost": {"usd_month": "1.75", "kind": "vendor", "as_of": "2026-09-04"}}],
 "grants": [{"name": "cal-1", "grant_id": "sha256:3f9c…",
             "issued": "2026-09-04T00:00:00Z",
             "expires": "2026-11-01T00:00:00Z", "box": "cal-1-node",
             "approved": true, "signature": "verified",
             "revoked_at": null, "reports_state": "LIVE"}]}
```

- **Grant fields.** `name`, `grant_id`, `issued` and `expires` are
  required; a grant with any of them missing or null is `bad-charter`
  with the reason "source gave no <field>". `box` is a name, a list, or
  null when the source knows no box for the grant (the ranch: a charter
  with no live box); a null `box` lists nothing and covers nothing, and
  the grant is still shown with its real status (Phase 1 finding: the
  fixture's revoked and expired rows all have `box: null`). `approved`
  is a boolean.
  **`signature` is one of `verified | failed | absent | unverified`**;
  any other value fails the whole file. Only `verified` can make an
  inbox grant `valid`. `revoked_at` is a time or null. `reports_state`
  is free text, shown as "source reports …".
- **Complete.** A source counts as complete for one run only when the
  file says `"complete": true`, `may_claim_complete` is `yes`, and the
  file was read whole (no limit cut it short). Otherwise the source is
  `partial`, and a box missing from it is not evidence.
- **Grants are the source's claims.** `approved`, `signature` and
  `reports_state` are shown as "source reports …". The module derives
  the status itself from `issued`, `expires` and `revoked_at` on this
  host's clock (§2.1). An inbox grant is `valid` only when the source
  says it is approved and verified.
- **Shadowing.** A local charter with the same name replaces an inbox
  grant for approval and signature. **Revocations and earlier `expires`
  from any source always apply**, shadowed or not, to a local grant
  whose `issued` is not later than the revoked or expiring grant's
  (§2.4). A local renewal with a later `issued` is not shadowed by an
  older inbox revocation.
- **Conflicts** are shown, not resolved. The more restrictive value wins,
  and every source is listed.
- **Limits.** Regular files only. At most 16 files, 1 MiB each (read with
  a cap) and 2,000 rows in total; anything cut short is `partial`. A file
  that does not parse is `failed`, and its last good copy is kept as
  `stale` evidence: it can keep an alarm, never lower one (§2.3).
- **The ranch** uses `contrib/ranch_status_to_inbox.py`, which runs on
  the ranch beside `charters/`.
  - It reads the rows from `status.py --json` stdout, and for each row
    it reads `charters/<row.charter>` once: the bytes give `grant_id`
    (`sha256:` plus the hex digest) and the front matter gives `created`,
    which becomes `issued`. A charter it cannot read gives a grant with
    `grant_id` and `issued` null, which the module reports as
    `bad-charter` (never `valid`, never able to outrank a tombstone).
  - If `status.py` exits non-zero (it does so when `ACTIVE-BOXES.md` is
    missing), the converter leaves the previous output file in place and
    exits non-zero itself, so the ranch's cron log carries the error.
  - It never writes `"complete": true`, because `status.py` lists
    charters, not boxes.
  - It emits one box per row with a non-null `box`: `name` from
    `box.manifest_key`, `provider` from `box.domain`, `tier` from
    `box.tier`.
  - It maps `approval_signed` to `approved`; `sig_ok` to
    `signature: verified`, `signed and not sig_ok` to `failed`, `not
    signed` to `absent`.
  - It turns a `last_event` of `revoke <timestamp>` into `revoked_at`,
    parsing both the RFC 3339 and the compact form (§2.5).
  - It passes `expires` through as given. A row with
    `expires_unparseable: true` (the fixture's `naive-1`) is therefore
    `bad-charter` here, where full Corral treats it as never expired: a
    known difference, asserted in §12.1.
  - Measured value types (Phase 0a): `budget_usd` is a **string**, passed
    through as text; `capabilities` is the raw front-matter text
    (`[llm.chat]`) or null; `hours_left` is a float or null and is
    ignored here; `box` is null or `{tier, ip, domain, manifest_key}`;
    `generated_at` carries a colon-less offset.
  - It runs from the ranch's own cron, and the file reaches this host
    through whatever sync the operator already has.

### 4.4 Cloud inventory (Phase 3, opt-in)

Fetchers for Lightsail, Hetzner, DigitalOcean and GCP, through FinOps
Phase 4's exact-host egress. Each is its own source with its own id
(`gcp`, `lightsail`, …), so a grant that covers a box seen by a fetcher
lists it as `gcp/cal-1`. A fetcher writes `complete: true` only when
every page arrived. Nothing in v1 depends on them.

### 4.5 Host-key check (Phase 2, its own sandbox)

This checks that **something at the box's address answers with a host key
the operator already trusts**. It is not a login, and it does not show the
agent is running.

- **Targets are allowed by the core,** not by module config:
  `corral-light module allow-probe delegates <box> <host>[:port]`. The
  allow is stored in the core's pins (`modules.json`), and doctor lists
  it. Module config and inbox content never add a target. At most 32.
- **A pin is `<box> <host>[:port]`.** The operator changes a box's
  address with the same verb, which replaces the pin. Every outcome
  carries the address it was measured against, and a result for an
  address that is no longer pinned is dropped, not shown. A box whose
  pin changed is `not-checked` until the next run against the new pair.
- **The `hostkey-probe` profile.** It is new: it is neither the report
  profile (which has no network) nor the egress proxy (which allows only
  port 443 and only public addresses). The core resolves each host once,
  and a forwarder allows exactly those address and port pairs, private
  addresses included.
- **The check, per target:**
  1. `ssh-keyscan -T 3 -p <port> <addr>`, all key types.
  2. `ssh-keygen -F <name> -f <known_hosts>`, where `<name>` is `host`,
     or `[host]:port` when the port is not 22. This handles hashed
     entries.
  3. **Compare the scanned key blobs with the returned lines.** When a
     host answers with several keys, one `@revoked` match is
     `key-revoked`, else one mismatch is `key-mismatch`, else one match
     is `key-matches`.
  `known_hosts` is read by the core and never bound for the module.
- **Outcomes and levels:**

  | Outcome | Level |
  |---|---|
  | `key-matches` | ok |
  | `key-mismatch` | bad |
  | `key-revoked` (an `@revoked` line matches) | bad |
  | `key-unknown` | warn |
  | `cert-unsupported` (the host presents a certificate; certificate checks are out of v2) | warn |
  | `no-answer` | warn after 3 consecutive checks |
  | `resolve-failed` | warn |
  | `not-checked` (still running at the deadline, or the pin changed) | no change |

- **Caps:** 8 targets at a time and 30 s for the run. 32 targets at 3 s
  in waves of 8 is 12 s.
- **Where results go:** `<state>/module-reports/delegates/hostkey.json`,
  bound read-only. No `light-feed`.
- **Manifest:** `"core_reports": ["hostkey-probe"]`. This is additive.
  The core moves to `CORE_API = 2` and **accepts any manifest whose
  `core_api` is at most its own** (`modules.py`, `CORE_API` and its
  equality check accept only equality today). `core_api` must be a
  positive integer: a boolean, zero or a string is refused. A module
  that names `core_reports` must declare `core_api: 2`.
- **On the board,** a `key-mismatch` or `key-revoked` makes the box `bad`
  whether or not it is covered. The probe can raise a level, never lower
  one.

## 5. What the board shows

- **Tiles:**
  - Covered boxes, with the honesty line as its note (on macOS,
    "signatures unverifiable on this host", §3).
  - **Boxes listed without a valid grant**, `bad` when not zero.
  - Boxes unknown.
  - Grants expiring within 72 h.
  - Sources with problems.
  - Declared monthly cost: kind `declared`, or `unknown` when nothing is
    declared; never `$0`.
- **Table "Boxes":** one row per in-scope box, named by its qualified
  id. Columns: Box, Presence (with "last listed <age> ago" for an
  `unknown` box), Covered by, Level and reason (in words, because cells
  have no level), Sources. Rows are sorted by level, then name. The
  table splits into blocks of `MAX_ROWS` (200, `modules.py`). **Alarm
  rows fill the blocks first, in order.**
- **Table "Grants":** one row per (grant, box it lists). Columns: Name,
  Grant id, Status (including `ended:removed`), Issued, Expires, Box,
  Cost, Source. This is where the operator sees "expires in 41 h".
- **Table "Sources":** health, age, complete or partial, row counts and
  errors for each source. It also shows the ledger's size, a ledger or
  tombstone rebuild if one happened, hand removals and gone-records for
  7 days, the clock warning, and on macOS the "cannot verify" row.

**Notices** (manifest `"notices": true`; the rail shows at most
`NOTICES_PER_MODULE` per module, 3, `modules.py`):
- **The alarm** (`bad`) is one notice for every box without a valid
  grant. Its id is `alarm.<generation>`, a short hash of the sorted alarm
  box ids, so a changed set is a new notice that comes back after "Not
  now" (the dismissal key in `static/app.js` is module, id, level and
  title).
- **Expiry** (`info`) follows the same rule.
- Both clear when the condition clears.

**Cadence:** `every_s` 300 and `timeout_s` 30 (enforced). `budget_s` 10 is
advisory to the module.

**A seam limit, fixed in Phase 1 (Q7, decided).** Today, if the collector
itself fails, `fresh_at` is not advanced (it moves only on success) and
the module's notices are dropped at `fresh_at + 2 × every_s` or
`NOTICE_MAX_AGE_S`, whichever is first (`modules.py`, the notice drop),
so a broken collector drops its own alarm from the rail. Phase 1 changes
the core so a failing module's last `bad` notices stay on the rail,
marked with the age of the last good snapshot (§7).

## 6. The module (`corral-light-delegates`)

```
module.json            core_api 1 (2 from Phase 2); collector, cli, doctor; reads: []; network: none; notices: true
delegates/charter.py   grammar, required keys, box binding, verify (one read, one buffer; Linux only)
delegates/sources.py   charters, boxes.toml, gone.toml, declared inbox sources, completeness
delegates/ledger.py    ledger, tombstones, expiry mark, ended_at latch, damage and salvage
delegates/model.py     grant status, box identity and presence, coverage, severity
delegates/view.py      tiles, split tables, notices
collector.py           snapshot
cli.py                 setup | show | doctor | new | sign | check | gone | ack-ledger
contrib/ranch_status_to_inbox.py
```

- `setup` asks the operator to paste a public key line. It writes
  `<config>/allowed_signers` (principal plus
  `namespaces="corral-light-delegate-charter"`), writes `config.toml`, and
  creates `charters/` and `inbox/`.
- `new <name>` writes a template with `issued` set to now and `box` set
  to the name, as a bare `declared` box the operator edits. `sign` prints
  the exact, shell-quoted `ssh-keygen -Y sign` command and never signs.
- `gone <source-id>/<name>` appends the gone-record (§2.4).

## 7. Core changes

| Phase | Change |
|---|---|
| 1 | `modules/index.json` gains `delegates`. No `inbox` manifest key is needed, since the inbox is inside config (Q2). |
| 1 | **Q7 (decided):** while a module's run state is `failing`, the rail keeps serving the `bad` notices from its last good snapshot until `NOTICE_MAX_AGE_S`, suffixed "last good <age> ago" (`modules.py`, the notice drop at `fresh + 2 × every`). No new card type. Tests in §12.2. |
| 2 | The `hostkey-probe` profile and forwarder; `module allow-probe` and its pin field; `core_reports`; `CORE_API = 2`, accepting `core_api <= CORE_API`. |
| 2 | **A core-provided signature verifier** for macOS: a second core report (`verify-signatures`) in which the core runs `ssh-keygen -Y verify` outside the module sandbox against the module's `allowed_signers` and charters, and writes the results to `<state>/module-reports/delegates/`. Its shape follows the probe's: the module asks for nothing it cannot read already, and the core never signs. Designed when Phase 2 is built. |
| 3 | None beyond FinOps Phase 4's fetchers. |

Any other core change found while building is a finding to report.

## 8. Lanes: what the later design must answer

A delegate lane is a **core lane type**, `delegate:<id>`. A module
snapshot never authorizes a send. The core takes candidate boxes from
this module's inventory, re-checks the grant itself before opening and
before every send, and refuses unless a `valid` grant lists the box (and,
once Phase 2 exists, its key matches). Open questions for that design:
the protocol at the box end, carrying over the confirm that names the
destination, and per-send data-class checks.

## 9. Open questions

1. *(Settled: `core_reports`, `core_api` 2, backward-compatible.)*
2. *(Decided by the operator, 2026-10-10: **inside the config folder**,
   `<config>/inbox/`, no core change. Three rounds of the panel favoured a
   separate core-mounted folder; the operator weighed that it is
   separation, not isolation, since the same user writes both, and chose
   the simpler shape. §3 states the consequence.)*
3. *(Settled: `sign` prints.)* 4. *(Gone.)* 5. *(Settled in §2.3: scope.)*
6. *(Settled: fixed 30 days of history; tombstones forever.)*
7. *(Decided by the operator, 2026-10-10: **yes**, in the shape Grok
   proposed in round three: while a module is failing, the rail keeps the
   `bad` notices from its last good snapshot until `NOTICE_MAX_AGE_S`,
   with a "last good <age> ago" suffix. A Phase 1 core change, §7.)*
8. *(Settled in rev 4, §2.4: the gone-record is unsigned, like
   `boxes.toml`. Reopen if a signed inventory is ever wanted; it would
   need the Phase 2 verifier first.)*

## 10. Fleet, next (not planned here)

A generic Fleet is the operator's machines and hubs. Signing-adjacent
actions stay out of any module. Hub-to-hub is parked, so heartbeats would
arrive through a declared-source inbox. **Shared schema, not shared
code:** Delegates publishes its box observation format
(`corral-light.boxes/1`), and Fleet reads the same format from its own
sources.

## 11. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** | **(a) and (b) done, 2026-10-10** (`docs/delegates-phase0.md`): `status.py --json` timed on ranch-server (0.13 s, exit 0, empty board), the `selftest_status.py` fixture captured with its real value types (`docs/fixtures/ranch-status-fixture.json`, 16 rows), the namespace confirmed as `cc-handoff`, and a real `sk-ssh-ed25519` signature verified inside the collector sandbox on camano, with four negative cases failing and an `allowed_signers` line without `namespaces=` verifying (hence §4.1's own check). (a) was re-measured through the mailbox on 2026-10-10 with the typed `delegates-status` verb, with identical results; re-run it once a real charter is live, to close the fixture gap below. (c) an inbox rename seen on the next run and (d) the alarm notice returning when its set changes need a collector and a notice to exist, so they are checked when Phase 1 is built. | results doc; plan updated |
| **1. Board v1** | §2 to §6, and §7's Phase 1 rows | §12.1 passes on Linux CI, with its signature cases skipped on macOS and §12.2's "cannot verify" case run there; §12.2 passes; installed on this hub; deleting an expired charter whose box is listed keeps one `bad` notice; Phase 0 (c) and (d) observed |
| **2. Host-key check and verifier** | §4.5, and the core-provided verifier (§7) so macOS verifies | §12.3 passes; on macOS a signed charter is `valid` |
| **3. Cloud inventory** | §4.4 | fetchers tested against local stubs |
| **4. Lanes** | a separate plan | not scheduled |

Fixture gap, carried from Phase 0: the captured fixture has no passing
`LIVE`, `READY` or `UNAPPROVED` signature. Phase 1 needs either an
archived charter signed by the operator placed in a fixture tree with
its `allowed_signers`, or a throwaway key the module repository owns.

## 12. Testing plan

### 12.1 Module (its own repository; CI on 3.9, 3.12, 3.14)

- **Severity table.** Every presence crossed with covered or not gives
  §2.3's level. Every grant status crossed with listing the box or not
  gives the right coverage.
- **Grant precedence.** Expired and also `bad-sig` is `bad-sig`. Revoked
  and expired is `ended:revoked`.
- **Binding.** A charter without `box:` is `bad-charter` and covers
  nothing; so is one with `boxx:`. A rename (new `name`, new filename)
  with the same `box:` keeps the box covered. `box: cal-1` covers
  `declared/cal-1` and not `ranch/cal-1` or `ranch/cal-1-node`;
  `box: [cal-1, ranch/cal-1-node]` covers both rows. Two sources each
  listing a box named `worker` are two rows, and a grant listing
  `a/worker` leaves `b/worker` `bad`.
- **Alarm continuity.** Each case starts from a `bad` box. After every
  step, assert the level and the reason, not the label. The box stays
  `bad` through each of these:
  - the charter is deleted;
  - `box:` is edited away;
  - the status is changed to `draft`;
  - the tier is removed;
  - the only source fails, with its last good copy still listing the box;
  - the only source goes stale;
  - the source is removed from config;
  - an over-cap "complete" file omits the box;
  - the ranch converter's output omits the box after its charter is
    deleted;
  - the box becomes `unknown` (its row reads "last listed <age> ago" and
    stays `bad`).

  It lowers **only** when a valid grant lists the box, a fresh
  complete `boxes.toml` drops it (and Sources then shows the hand
  removal), or a gone-record names it.
- **Exit.** A ranch-only box that leaves the ranch output stays at its
  last presence and level with no 30-day clock; `cli.py gone
  ranch/cal-1-node` moves it to `not-listed`, starts the clock, and
  shows in Sources for 7 days; `gone` refuses a box the ranch listed 3
  min ago; a ranch listing dated after the gone-record reinstates the
  box, `bad` if uncovered.
- **`unknown` recomputes.** A covered box goes `unknown`, then its grant
  expires → `bad` ("last listed … ago"). A `bad` box goes `unknown`,
  then a valid grant lists it → ok with the "last listed" note.
- **Reuse and rename.** Grant A ends, and grant B (valid) lists the same
  listed box → ok, "covered by B", with A shown as `ended:removed` once
  its file is gone. A rename the same way → ok. A valid grant C that
  lists a different box leaves the first box `bad`.
- **Revocation order.** A revoked charter with `issued` T: restoring the
  older approved file (`issued` < T) stays revoked; a file with an equal
  `issued` stays revoked; a re-signed charter with a later `issued` →
  `valid`. The tombstone outlives a 30-day ledger expiry (fake clock).
  Same-name delegates from two sources have separate tombstones: revoking
  `ranch/cal-1` does not end `declared/cal-1`'s local grant with a later
  `issued`, and does end one with an earlier `issued`.
- **Time and `issued` bounds.** `issued > expires` → `bad-charter`.
  `issued` 11 min ahead → `bad-charter`; 9 min ahead → accepted. A
  future `issued` never outranks a tombstone. A re-signed copy of an
  expired charter with the same `issued` and changed bytes →
  `ended:expired` (the expiry mark).
- **Clock.** An expired grant, then the clock set back a day → still
  `ended:expired` (the latch), with the clock warning shown.
- **Damage.** Corrupt `ledger.json` → moved aside, and the `bad` Sources
  row stays until `ack-ledger`. A `tombstones.jsonl` with one garbage
  line among three → moved aside, two tombstones salvaged, a revoked
  charter stays `ended:revoked` after `ack-ledger`, and `ledger.ack` is
  the only file the CLI wrote.
- **Completeness.** These are `partial`:
  - `complete: true` when `may_claim_complete` is `no`;
  - `complete: true` cut at 2,000 rows;
  - the ranch converter's output (it never claims complete).

  Fresh and complete, with the box missing, is `not-listed`. A fresh
  complete omission alongside a stale listing is `not-listed`, with a
  note. A box no source has ever listed is `unknown`, warn, not
  `not-listed`.
- **Inbox grants.** Each of these is not `valid` and leaves a listed box
  `bad`:
  - `approved: false` with `expires` in the future;
  - `signature: failed`, `absent` or `unverified`;
  - `reports_state: LIVE` but expired;
  - `grant_id` or `issued` missing or null (`bad-charter`).

  `signature: "yes"` fails the whole file. A local approved charter plus
  an inbox `revoked_at` on a grant with an equal or later `issued` →
  revoked; with an earlier `issued` → the local charter stays `valid`.
- **Grammar.** All of these are accepted as "other": `note`, `date`,
  `lane`, `job`, `log`, `os`, `boxes`, `bot`, `mission`, `payload` and
  `ssh-key-file`. These are `bad-charter`: `Status:`, a duplicate after
  `_` folding, a missing `issued`, a missing `expires`, a missing `box`,
  and neither `status` nor `approval`. `approval: >` and `approval: |`
  → `draft`. `status: approved` together with `approval: pending` →
  `draft`.
- **Signatures, with real `ssh-keygen`** (Linux CI; skipped on macOS).
  These are `bad-sig`:
  - one byte changed in the body;
  - one byte changed in the front matter;
  - the wrong namespace;
  - an unknown signer (reason "principal not in allowed_signers");
  - `allowed_signers` with no `namespaces=` (refused by the module;
    `ssh-keygen` alone accepts it, Phase 0b);
  - a missing `-I`;
  - a CRLF copy of a file that was signed as LF.

  A file signed as CRLF bytes is `valid`. `sk-ssh-ed25519` passes. A
  missing `ssh-keygen` gives "cannot verify". A key removed from
  `allowed_signers` → no longer `valid`. Swapping the file between the
  read and the verify → the board uses the bytes it verified.
- **Truncation.** 450 boxes with 210 alarms: blocks one and two start
  with all 210 alarms, the tile reads 210, and every row appears once.
- **Notices.** A changed alarm set with the same count → a new id. "Not
  now" does not hide it. At most 3 notices.
- **Never zero.** No declared cost → `unknown`.
- **Converter.** For each row of `docs/fixtures/ranch-status-fixture.json`
  with a charter file beside it, the level matches full Corral's state,
  except two known differences asserted by name: `naive-1`
  (`expires_unparseable`, `bad-charter` here, never expired there) and
  `unapproved-1` with a live box (`bad` here, `LIVE` there; the captured
  row has `box: null`, so the test sets a box on a copy of it). Also:
  `revoked-1`'s compact `last_event` gives `revoked_at`
  2026-08-16T00:00:00Z; `budget_usd` arrives as text; `generated_at` with
  `-0700` parses; a missing charter file gives null `grant_id` and
  `issued`; a non-zero `status.py` leaves the previous file in place.

### 12.2 Seam and core (Light's repository)

- Light's suite passes with the module installed and removed.
- **The inbox** (Q2 decided: inside config). The collector reads
  `<config>/inbox/` and cannot write it. A symlinked `inbox/` is refused.
  `doctor` warns when an inbox file is newer than the last collector run
  and `setup` was the last CLI command (§3).
- **macOS.** Under the Darwin profile, every signed charter reads
  `bad-sig: cannot verify (no subprocess in this sandbox)`, the Covered
  tile's subtitle says so, Sources has one warn row, and the run does
  not fail.
- **Failing module keeps its alarm** (Q7). A module whose last good
  snapshot carried a `bad` notice, then fails for three cadences: the
  notice is still on the rail with the "last good" suffix; it goes when
  `NOTICE_MAX_AGE_S` passes or a good snapshot clears it.
- **The CLI has no `~/.ssh`.** A sentinel key is absent inside `cli.py
  setup`.
- **`core_api`.** With the core at 2, a manifest with `core_api: 1` still
  loads (FinOps, unchanged). A manifest with `core_api: 3`, `true`, `0`
  or `"2"` is refused. `core_reports` with `core_api: 1` is refused.

### 12.3 Phase 2 (a local sshd fixture)

- A **different key on a name `-F` finds** → `key-mismatch`, and a
  covered box goes `bad`.
- `@revoked` → `key-revoked`. A certificate → `cert-unsupported`. A
  hashed entry matches. `[host]:2222` is looked up for port 2222. A host
  answering with a matching key and a revoked one → `key-revoked`.
- A target not allowed by `allow-probe` is not contacted, even when
  `boxes.toml` or the inbox names it.
- **Pins.** `allow-probe` for a pinned box with a new host replaces the
  pin; the old pair is not contacted again, its `key-matches` result is
  dropped, and the box is `not-checked` until the next run.
- The probe cannot reach a second listener. `~/.ssh/id_*` is absent in
  the profile. 32 targets finish within 30 s; one sleeping target is
  `not-checked`, not `no-answer`.
- **Verifier.** On macOS, with the `verify-signatures` report, a signed
  charter is `valid` and a one-byte edit is `bad-sig`.

### 12.4 Live checks on this host (manual)

- On Linux, a real charter, signed with the operator's key, verifies. A
  one-byte edit → `bad-sig` within one run. On this macOS hub before
  Phase 2, the same charter reads "cannot verify" and the board says why.
- The converted ranch inbox matches full Corral's room for every fixture
  except the two known differences.
- Deleting a charter whose box is listed keeps the rail's alarm.
- `cli.py gone` on a box the ranch stopped reporting moves it to "box
  gone" and the alarm clears.
- `module remove delegates --purge` leaves Light as it was.
