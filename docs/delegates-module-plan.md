# Delegates as the second Corral Light module (and Fleet after it)

> Status: rev 2, 2026-10-09. Not yet reviewed by a panel. Nothing built.
>
> Rev 1 went cold to a three-vendor panel (Codex on gpt-6-astra, Grok,
> Gemini): AMEND, AMEND, REJECT
> (`reviews/2026-10-09-delegates-module-panel/synthesis.md`). In the same
> hour, a partial Phase 0 came back from ranch-server
> (`docs/delegates-phase0.md`). What rev 2 changes and why:
>
> - **Deleting a charter can no longer quiet the alarm.** Rev 1 turned
>   `OVERDUE-REVOKE` (bad) into `ORPHAN` (warn) when the charter file was
>   removed, and its own test asserted that. Rev 2 keeps a **grant ledger**
>   (§2.3). The panel converged on this.
> - **One state became three axes:** the grant, the box and the source (§2).
>   A box listed without a currently valid grant is the alarm, whatever the
>   reason the grant is invalid. The labels say what is known: "box
>   listed", never "running".
> - **Inbox rows are recomputed here.** A source's own verdict is shown as
>   "source reports …"; expiry is worked out on this host's clock.
> - **Pending the operator's call (§9 Q2): the inbox moves out of the
>   config folder** into a core-mounted, read-only folder. This is a small
>   core change, so rev 1's "no core change in v1" becomes "two small ones"
>   (§7).
> - **Seam facts rev 1 got wrong:** the module CLI cannot see `~/.ssh`, so
>   `setup` takes a pasted public key; tables cap at 200 rows; notices need
>   `"notices": true`; `timeout_s` is the enforced deadline, not `budget_s`;
>   `CORRAL_MODULE_CONFIG` names the file, not the folder.
> - **Ranch facts rev 1 got wrong (Phase 0):** the ranch signs charters with
>   OpenSSH hardware keys, not gpg, so its charters can be verified locally
>   (the gpg question is gone). A bare-date `expires` means the end of that
>   UTC day. Revocation comes from audit events, not a charter field.
> - **Phase 2 gets its own sandbox.** The seam has no network for reports,
>   and its egress proxy allows only port 443.
>
> Decided by the operator on 2026-10-09, before rev 1:
> 1. **Generic and public**, as FinOps is. The ranch is one way to
>    configure it.
> 2. **The board first, lanes later** (§8).
> 3. **Delegates, then Fleet** (§10).

## 0. The ask

Port full Corral's Delegates into Corral Light as a module in its own
repository (`corral-light-delegates`), under the seam and trust model of
`docs/finops-module-plan.md` §3 to §5. Spec it, plan the build and the
tests, and send it to the panel.

The question the board answers: **which agents have I handed work to on
other machines, what are they allowed to do, until when, what do they
cost, and is a box still listed for any of them without a grant that
covers it?**

## 1. What full Corral has, and what carries over

Full Corral's Delegates (read from `cvp1/corral` and the ranch's
`delegates/status.py`, 2026-10-09) has two parts:

| Piece | What it does |
|---|---|
| **The room** (`delegates.py`, 74 lines) | Shows the rows from `delegates/status.py --json` exactly as they arrive. One route, no loop, polled every 60 s. |
| **The lanes** (`sessions.py:504-720`) | One chat-only lane per live delegate-tier box, over ssh to `chat_bridge.py` on the box and its broker. |

How the ranch works (Phase 0, read from code, not measured):

- **Charters** are `delegates/charters/<name>.md`. Each has flat front
  matter with hyphenated keys, some of which hold `[a, b]` lists. Each is
  signed with a detached OpenSSH signature at `<name>.md.sig`, using a
  hardware key (`sk-ssh-ed25519`), in a namespace believed to be
  `cc-handoff`. The signature is checked against `cc-handoff/allowed_signers`.
- **States**, worst first: `OVERDUE-REVOKE`, `BAD-SIG`, `LIVE`, `REVOKED`,
  `EXPIRED`, `UNAPPROVED`, `READY`, `DRAFTED`.
- **"Live"** means a `tier == "delegate"` row exists in
  `ACTIVE-BOXES.md` or in `gcp/list.py`.
- **"Revoked"** means a revoke audit event is later than the box's last
  provision, enroll or deliver event.
- **One gap:** a live box whose charter is signed but not approved shows
  as `LIVE`. Rev 2 does not copy that.

Rules that carry over unchanged:

- **The viewer computes nothing.** Light renders what the module reports.
- **Charter TTL is not uptime.** A countdown always reads "expires in …".
- **A broken source degrades alone.** It never takes the room down.
- **The room has no action buttons.**
- **Null is unreported, never zero.**

Not carried over: audit-log attribution, `spend.py`, Lightsail as a
required input, and the lanes (§8).

## 2. The model

A **delegate** is an agent the operator runs on another machine under a
written **grant** (a charter). It runs on a **box** that some **source**
reports. Each delegate is described along three separate axes, and its
severity is derived from all three.

### 2.1 The axes

**Grant** (from the charter, worked out on this host):

| Value | When |
|---|---|
| `valid` | approved, in date, and the signature is good (or signing is off) |
| `draft` | not approved: `status: draft`, or an `approval:` value of `""`, `unsigned`, `pending` or `proposed` |
| `unsigned` | approved, signing required, no `.sig` |
| `bad-sig` | a `.sig` exists and does not verify |
| `bad-charter` | the charter cannot be read safely (§4.1): no `expires`, an `expires` with no time zone, a misspelled security key, a name that differs from the filename |
| `ended:expired` | now is at or after `expires` |
| `ended:revoked` | `status: revoked`, or the ledger has a revocation for this delegate (§2.3) |
| `ended:removed` | no charter file, but the ledger remembers a grant for a box that is still listed |
| `none` | no charter and no ledger entry |

**Box** (from inventory sources):

| Value | When |
|---|---|
| `listed` | at least one fresh source lists it |
| `listed-stale` | only stale sources list it |
| `not-listed` | every source that covers it is fresh and **complete** (§4.3), and none lists it |
| `unknown` | no fresh complete source covers it: missing, failed, stale, or partial |

**Source health**: per source, `fresh`, `stale` (older than its
`stale_after_s`), `failed` (with the reason), or `partial`.

### 2.2 Severity and labels

| Grant | Box | Label | Level |
|---|---|---|---|
| `valid` | `listed` | granted; box listed | ok |
| `valid` | `listed-stale` | granted; box listed by a stale source | warn |
| `valid` | `not-listed` | granted; no box | info |
| `valid` | `unknown` | granted; box unknown | warn |
| not `valid` | `listed` or `listed-stale` | **box listed without a valid grant (`<reason>`)** | **bad** |
| `ended:*` | `not-listed` | grant ended; box gone | unknown (done) |
| `draft`, `unsigned`, `none` | `not-listed` | not granted; no box | info |
| `bad-sig`, `bad-charter` | `not-listed` | charter problem; no box | warn |
| not `valid` | `unknown` | grant `<reason>`; box unknown | warn |

The alarm is one rule, not a list of states. A box listed without a valid
grant is `bad`, whether the grant expired, was revoked or removed, was
never approved, was never signed, or fails its signature. `<reason>` names
which one: "expired 3 h ago", "revoked 2026-10-02", "charter removed",
"never approved", "signature fails". A box seen only in a stale source
still raises the alarm, labelled with the source's age. A stale source
can make the board uncertain, but it never makes it reassuring.

`not-listed` needs a **complete** source. A box that is missing from a
partial, failed or stale source is `unknown`. Being absent from such a
source is not evidence that the box is gone.

### 2.3 Identities and the ledger

- **Delegate id** is the charter's `name`. It must match the filename
  stem and `^[a-z][a-z0-9-]{0,60}$` (the ranch's own regex). Renaming a
  charter retires the old delegate and creates a new one.
- **Box id** is `<source-id>/<box-name>`, so the same name from two sources
  stays two observations until it is joined. A charter's `box:` names the
  box, and defaults to the charter's `name`. For a ranch row, the
  converter also tries `<name>-node` first, as `status.py` does. Several
  delegates may share one box.
- **The ledger** is `<data>/ledger.json`, written by the collector only.
  It keeps, per delegate, every box id the delegate has been associated
  with and when; the highest-dated revocation seen; the last `expires`
  seen; and the time the charter was last seen. Entries are kept until
  every associated box has been `not-listed` for 30 days. The ledger
  makes three things hold:
  - Deleting a charter whose box is still listed gives `ended:removed`,
    which is `bad`.
  - Editing `box:` away from a listed box keeps the old association until
    that box is `not-listed`.
  - Restoring an older signed charter after a revocation was seen does
    not bring the grant back. A grant whose approval is dated before the
    recorded revocation stays `ended:revoked` until a newer signed charter
    arrives.
- The ledger protects against mistakes and stale tools, not against the
  operator. Same-user code can delete it. The module's docs say so.

### 2.4 Time

- `expires` is accepted in three forms: RFC 3339 with `Z`, RFC 3339 with an
  offset (`+00:00` or `-0700`), or a bare `YYYY-MM-DD`, which means
  23:59:59Z on that day (the ranch's meaning). A date-time with no zone is
  `bad-charter`.
- "Ended" means `now >= expires`, where `now` is the collector's UTC clock.
- If the clock moves backwards past the last run's `generated_at` by more
  than 5 minutes, a `clock` warning goes into Sources and every countdown
  is suffixed "(clock uncertain)". The board has no authoritative time and
  says so.
- An inbox `generated_at` more than 10 minutes in the future marks that
  source `failed: timestamp in the future`.

## 3. Trust model

The collector gets what FinOps §3 allows: system dirs read-only, its
config folder read-only, its data folder writable, no network and no home
directory. Under rev 2 it also gets the inbox folder read-only (§4.3).
**The module CLI runs in the same sandbox** with the config folder
writable, and it too has no `~/.ssh` (`modules.py:886-896`). No module path
ever touches a private key.

What a charter reveals (delegate names, hostnames, data classes, budgets)
stays on this machine, behind Light's cookie.

**The board is not proof.** "Box listed" means a source says a box exists.
It does not mean the box is up, that it is the box the charter meant, or
that the agent obeys the charter. Every row shows its sources and how old
they are. The honesty line is the first note in the view **and** the
subtitle of the "Granted, box listed" tile, because people read the tile
and skip the notes.

## 4. Sources

### 4.1 Charters (`<config>/charters/<name>.md`)

The config folder is `dirname($CORRAL_MODULE_CONFIG)`.

```markdown
---
name: cal-1
status: approved
expires: 2026-11-01T00:00:00Z
box: cal-1
data-class: calendar-read
capabilities: [calendar.read, mail.draft]
budget-usd: 5
cost-box-usd-month: 5.00
---

What this delegate is for, in the operator's words.
```

**Grammar.** The file is UTF-8. CRLF is read as LF *for parsing only*;
the signature covers the exact bytes. The front matter sits between two
`---` lines, starting at byte 0. Each line is `key: value`. A key matches
`[a-z][a-z0-9-]*`, and `_` is read as `-`, so `data_class` and
`data-class` are the same key. A value is a bare string, a `"…"` string, or
a `[a, b]` list of bare strings. `#` starts a comment only at the start of
a line. A duplicate key makes the charter `bad-charter`.

**Keys.** These are understood: `name`, `status` (`draft | approved |
revoked`), `approval` (the ranch's form: a non-empty value other than the
unsigned markers means approved), `expires`, `box`, `data-class`,
`capabilities`, `tools`, `budget-usd`, `credential-N-type`,
`cost-box-usd-month`, `cost-note` and `created`. Any other key is kept and
shown under "other". The exception is a key within edit distance 2 of
`name`, `status`, `approval`, `expires` or `box`, which makes the charter
`bad-charter` with "did you mean `expires`?". A typo in a security field
must never be read as harmless. When both `status` and `approval` are
present, the more restrictive one wins.

**Signatures.** `<name>.md.sig` is an OpenSSH detached signature. The
collector reads the charter **once**, then verifies and parses the same
bytes:

```
ssh-keygen -Y verify -f <config>/allowed_signers -I <principal>
           -n <namespace> -s <name>.md.sig  < (charter bytes on stdin)
```

- `principal` and `namespace` are per charter folder, set in
  `config.toml`. The defaults are the principal `setup` recorded and the
  namespace `corral-light-delegate-charter`. A ranch folder sets the
  namespace to the ranch's own (to be confirmed by Phase 0).
- Any key type `ssh-keygen` accepts is accepted, `sk-ssh-ed25519`
  included. Verifying a signature needs no hardware.
- `ssh-keygen` runs with a 5 s timeout. A missing binary, a timeout or an
  unsupported key type makes the grant `bad-sig: cannot verify (<reason>)`.
  That is still not `valid`, and it is labelled differently from "fails".
- The verify cache in `<data>/verify-cache.json` is keyed by the SHA-256 of
  the charter, the signature, `allowed_signers`, the namespace, the
  principal and the `ssh-keygen` version. Changing any of them, including
  removing a key from `allowed_signers`, forces a fresh check.
- `require_signature` in `config.toml` defaults to `yes` once
  `allowed_signers` exists, and to `no` only before `setup` has recorded a
  key. While it is `no`, the "Granted" tile reads "granted (unsigned
  charters)". Without signing, anyone who can write the config folder can
  extend a grant, and the board says so.

**Revoking** is `status: revoked` plus a fresh signature. The ledger
records the revocation, so restoring the older file does not undo it
(§2.3). **Deleting** a charter is never a way to close a grant: a listed
box shows `ended:removed`, which is `bad`, until the box is gone.

### 4.2 Declared inventory (`<config>/boxes.toml`)

Boxes the operator lists by hand. Each has `name`, `host`, `port`,
`host_key_alias` (the last three are for Phase 2), `provider`, `since` and
`cost_usd_month`. The file is one source (`declared`) and is always
`complete` for the boxes it lists. Its rows are of kind `declared`.

### 4.3 The inbox (`<state>/module-inbox/delegates/`; pending §9 Q2)

Any outside tool the operator runs can drop a JSON file here. The core
creates the folder (mode 0700) when a manifest sets `"inbox": true`. It
binds the folder **read-only** for the collector and **never** binds it
for the CLI, so the module's own `setup` cannot write fake inventory. Tool
output and the operator's grants no longer share a folder: a converter
that can write the inbox cannot rewrite `expires` or `allowed_signers`.

**Sources are declared.** `config.toml` lists each one: an `id`, a
`file` (its name in the inbox), a `label`, `stale_after_s` (default 7200)
and `complete` (`yes` if the tool reports every box it knows of, so a box
missing from it is `not-listed`). A file in the inbox with no declared
source is ignored and shown in Sources as "undeclared file". A tool cannot
become a source by writing a file.

```json
{"schema": "corral-light.delegates.inbox/1",
 "generated_at": "2026-10-09T18:00:00Z",
 "boxes":    [{"name": "cal-1-node", "tier": "delegate", "provider": "gcp",
               "since": "2026-08-16",
               "cost": {"usd_month": "1.75", "kind": "vendor", "as_of": "2026-09-04"}}],
 "grants":   [{"name": "cal-1", "reports_state": "LIVE",
               "expires": "2026-11-01T00:00:00Z", "revoked_at": null,
               "verified_by": "ssh-sig (source)"}]}
```

- **An inbox grant is evidence, not a verdict.** `reports_state` and
  `verified_by` are shown as "source reports LIVE, signature checked by
  source". The module derives the grant itself from `expires` and
  `revoked_at` on this host's clock. A source that has stopped updating
  cannot keep anything green past its `expires`.
- **A local charter beats an inbox grant with the same name.** The row
  says it shadows one. Boxes are never shadowed: every box observation
  from every source is kept.
- **Conflicts are shown, not resolved.** Two sources that disagree about
  a grant's `expires` or revocation produce one row that uses the more
  restrictive value and lists both. Two sources disagreeing about a box
  give `listed` if any fresh source lists it.
- **Limits.** Regular files only (symlinks, FIFOs and devices are
  refused); 16 files; 1 MiB each, read with a cap; 2,000 rows in total.
  Each bad file fails only its own source.
- **Partial writes.** Tools write to a temp file and rename it. The module
  also refuses a file whose JSON does not parse, and keeps that source's
  last good copy in `<data>/` marked `failed`.
- **The ranch** uses `contrib/ranch_status_to_inbox.py`. It reads
  `status.py --json` stdout only (stderr carries `WARN:` lines). It maps
  `box.tier`, `expires`, `active`, `spend.box` and the derived revocation
  into the schema above, and sets `complete` only when `gcp_visible` is
  true. It runs from the ranch's own cron, and the file reaches this host
  however the operator already syncs files.

### 4.4 Cloud inventory (Phase 3, opt-in)

Fetchers for Lightsail, Hetzner, DigitalOcean and GCP list instances
through FinOps Phase 4's exact-host egress (`module_fetch.py`,
`review_egress.py`). Each needs a read-only API key the operator grants.
These are `complete` sources for their account. Nothing in v1 depends on
them.

### 4.5 Host-key check (Phase 2, its own sandbox)

An inventory says a box exists. Phase 2 checks that **something at the
box's address answers with the expected SSH host key**. That is all it
claims: it is not a login, and it does not show the agent is running.

- **Targets are explicit.** Only boxes in `boxes.toml` with `host`, and
  `port` (default 22), are checked. `ssh_config` is never evaluated, and
  inbox content never adds a target.
- **The core** resolves `host` once per run and connects only to that
  address and port. It does this in a new profile, `hostkey-probe`, with
  its own network namespace and a forwarder that allows exactly those
  address and port pairs. It is not the report profile, which has no
  network, and not the egress proxy, which is 443-only and public-only.
  Private addresses are allowed here, because home servers are the usual
  case.
- It runs `ssh-keyscan -T 5 -p <port> <addr>` with all key types. It then
  checks the result with `ssh-keygen -F <host_key_alias or host> -f
  <known_hosts>`, which handles hashed entries. `known_hosts` is read by
  the core and never bound for the module. `@cert-authority` and
  `@revoked` lines are honoured.
- Caps: 32 targets, 4 at a time, 30 s for the whole run.
- Each outcome is one of `answers-key-matches`, `answers-key-unknown`,
  `answers-key-mismatch`, `answers-key-revoked`, `no-answer` or
  `resolve-failed`, together with `checked_at` and `duration_ms`. Results
  go to a path only this module can read
  (`<state>/module-reports/delegates/hostkey.json`, bound read-only). The
  module does not need `light-feed`, which would also expose panes and
  quota.
- Manifest: `"core_reports": ["hostkey-probe"]`, a new sibling of
  `vendor_reports`, at `core_api` 2.
- On the board, "granted; box listed" becomes "granted; box listed;
  answers (key matches)", or warn with "no answer for 3 checks". The
  probe never clears an alarm.

## 5. What the board shows

A `corral-light.module/1` snapshot.

- **Tiles:**
  - Granted, box listed: a count, with the honesty line as its note.
  - **Box listed without a valid grant:** a count, `bad` when not zero.
  - Expiring within 72 h.
  - Sources with problems.
  - Declared monthly cost: kind `declared`, `unknown` when nothing is
    declared, never `$0`.
- **Table "Delegates":** sorted by level (`bad` first), then by name.
  Columns: Name, Grant, Box, Expires, Cost, Sources. A table cell has no
  level of its own (`static/app.js` renders cells as plain text), so the
  label carries the severity in words ("**ALARM** box listed without a
  valid grant (expired 3 h ago)"). The table is split into blocks of 200
  rows (`MAX_ROWS`, `modules.py:1003`). Every alarm row is in the first
  block, and a note gives the total and the number of blocks.
- **Table "Sources":** each charter folder, `boxes.toml` and declared inbox
  source, with health, age, row counts and errors. It also lists the
  ledger's size and the clock warning.
- **Notes:** the honesty line, then "Ledger: 2 boxes are tracked for
  charters that no longer exist".

**Notices** (manifest `"notices": true`; the rail shows at most 3 per
module, `modules.py:1102`):

- **The alarm notice** (`bad`) is one notice for all alarm rows: "2
  boxes are listed without a valid grant". Its id is `alarm.<generation>`,
  where the generation is a short hash of the sorted set of alarm box ids.
  The rail keys dismissal on module, id, level and title
  (`static/app.js:3250`). So when the set changes, it is a new notice and
  comes back after "Not now", even if the count stays the same.
- **The expiry notice** (`info`) is one notice: "cal-1 expires in 20 h",
  or "3 grants expire within 24 h". It follows the same id rule.
- Both stop when their condition clears, the module is disabled, or the
  module goes stale.

**Cadence.** `every_s` 300, `timeout_s` 30 (the enforced deadline).
`budget_s` 10 is advisory, a value handed to the module, which it uses to
stop starting new verifications. A grant can therefore end up to one
cadence before its row changes. The expiry notice exists for that reason.

## 6. The module (`corral-light-delegates`)

```
module.json            collector, cli, doctor; reads: []; network: none; notices: true; inbox: true
delegates/charter.py   grammar, keys, signature verify (one read, one byte buffer)
delegates/sources.py   charters, boxes.toml, declared inbox sources
delegates/ledger.py    associations, revocations, retention
delegates/model.py     grant and box axes, severity, labels
delegates/view.py      snapshot blocks, split tables, notices
collector.py           snapshot
cli.py                 setup | show | doctor | new <name> | sign <name> | check
contrib/ranch_status_to_inbox.py
```

- `setup` asks the operator to **paste** a public key line, then writes
  `<config>/allowed_signers` with the principal and
  `namespaces="corral-light-delegate-charter"`. It also writes
  `config.toml` and creates `charters/`. It cannot read `~/.ssh` and says
  so: "paste the output of `cat ~/.ssh/id_ed25519.pub`".
- `new <name>` writes a charter template. `sign <name>` **prints** the
  exact `ssh-keygen -Y sign -n <namespace> -f <your key> <path>` command,
  with every argument shell-quoted and the name validated. It never signs.
- `check` re-runs the model and prints each row's axes and reasons.

## 7. Core changes

| Phase | Change |
|---|---|
| 1 | `modules/index.json` gains `delegates` (one entry). |
| 1 | **If §9 Q2 goes to the separate inbox:** the manifest key `inbox: true` (additive, so `core_api` stays 1); the core creates `<state>/module-inbox/<name>/` and binds it read-only for the collector only, in the same way it binds `fetched` (`modules.py:869-873, 882`); `module remove --purge` deletes it; doctor shows it. Tests in §12.2. |
| 2 | The `hostkey-probe` profile and forwarder (§4.5); the `core_reports` manifest key; `core_api` 2; a per-module report path. |
| 3 | None beyond FinOps Phase 4's fetchers; a host list per provider. |

Any other core change found while building Phase 1 is a finding to report,
not something to add quietly.

## 8. Lanes: what the later design must answer

Not built in this plan. The panel agreed that **a delegate lane is a core
lane type**, `delegate:<id>`, beside `host:`, and not a module capability.
A module snapshot never authorizes sending anything.

- The core takes its list of candidate boxes from this module's
  inventory, the way `host:` lanes come from `ssh-hosts.json`. Before every
  send, the core checks the grant itself rather than trusting the
  snapshot. It refuses to open, or to send, when the grant is not `valid`.
- The v1 data model keeps what a lane will need: a stable delegate id and
  box id, the `data-class` and `capabilities` fields, and a box `host`
  and `port`.
- Questions left for that design: what protocol the box end speaks;
  how the confirm that names the destination carries over; and
  whether the box's host key must match (Phase 2) before a lane opens.

## 9. Open questions

1. *(Settled by the panel: `core_reports`, a sibling key at `core_api` 2,
   decided when Phase 2 is built.)*
2. **The inbox's location: the operator decides.** Rev 2 is written for a
   separate core-mounted folder (Grok, Gemini, and the author). Astra
   would keep it in the config folder, with no core change, on the grounds
   that the same user can write both. If the operator chooses the config
   folder, §4.3 moves back to `<config>/inbox/`, §7's inbox row is
   dropped, and the CLI's writable config mount then covers the inbox
   (the risk this rev closes).
3. *(Settled: `sign` prints the command.)*
4. *(Gone: the ranch signs with OpenSSH, Phase 0.)*
5. *(Settled: a box enters the model only when it is `tier: delegate` in
   its source, or when a charter or the ledger names it. Other servers are
   Fleet's. A box the ledger ties to a grant never leaves on a tier change.)*
6. **New:** should the ledger's 30-day retention be configurable, or fixed
   so that it cannot be set to zero?

## 10. Fleet, next (not planned here)

Full Corral's Fleet is a bridge to the ranch's mailbox, including the
signing flow, and is ranch-specific. A generic Fleet is **this
operator's machines and hubs**: which hosts exist, whether each one's
Light hub and jobs are fresh, and the tasks between them.

- Signing-adjacent actions stay out of any module.
- Hub-to-hub is parked. A board can be built from per-host heartbeat files
  delivered through the same declared-source inbox pattern as §4.3.
- **Shared schema, not shared code.** Delegates publishes its box
  observation format (`corral-light.boxes/1`: source id, box name, tier,
  host, port, provider, since, cost). Fleet reads the same format from
  its own sources, and neither module imports the other. This avoids the
  module-depends-on-module problem in design-6 LIVE.md finding 3.

## 11. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** | **Partial, 2026-10-09** (`docs/delegates-phase0.md`): the ranch's shape, read from code. Still to do: (a) a measured `status.py --json` run, fixtures from `delegates/selftest_status.py`, and the ranch's namespace confirmed. This needs a person or an authorized agent on ranch-server, because auto-triage cannot run commands. (b) `ssh-keygen -Y verify`, including an `sk-ssh-ed25519` signature, inside the real collector sandbox on this host. (c) A file renamed into the inbox is seen on the next run. (d) Alarm-notice dismissal and its return when the set changes. | a results doc; this plan updated |
| **1. Board v1** | §2 to §6, and §7's Phase 1 rows | §12.1 and §12.2 pass; installed here; a sample charter, `boxes.toml` and a converted ranch inbox render; deleting an expired charter whose box is listed keeps one `bad` notice |
| **2. Host-key check** | §4.5 | §12.3 passes |
| **3. Cloud inventory** | §4.4 | each fetcher tested against a local stub |
| **4. Lanes** | a separate plan (§8) | not scheduled |

## 12. Testing plan

### 12.1 Module (its own repository, CI on Python 3.9, 3.12 and 3.14)

- **The alarm rule, as a product of the axes.** Every grant value crossed
  with every box value gives the level in §2.2. Specifically: `draft`,
  `unsigned`, `bad-sig`, `bad-charter`, `ended:*` and `none` with `listed`
  are all `bad`, and so is each with `listed-stale`.
- **Alarm continuity.** These sequences each keep the row `bad` until a
  fresh complete source drops the box:
  - an expired charter whose box is listed: delete the charter;
  - the same: change its `box:`;
  - the same: change `status` to `draft`;
  - a revoked charter: restore the older signed approved file;
  - the same: tier removed from the source;
  - the only source goes stale;
  - the only source fails.

  Each test asserts the **level and the reason**, not the label.
- **Absence is not evidence.** A box missing from a partial, stale or failed
  source → `unknown`, not `not-listed`. Missing from a fresh complete
  source → `not-listed`.
- **Time.** `Z`, `+00:00` and `-0700` with the same instant give the same
  result; a bare date ends at 23:59:59Z; a naive date-time is
  `bad-charter`; `now == expires` is ended; a clock 10 minutes behind the
  last run shows the warning; a `generated_at` in the future fails the
  source.
- **Inbox recompute.** `reports_state: LIVE` with `expires` in the past →
  the alarm, not ok. Two sources disagreeing → the more restrictive value,
  with both listed.
- **Undeclared and hostile files.** An undeclared file, a symlink, a FIFO,
  2 MiB, 3,000 rows and truncated JSON each fail only themselves. The last
  good copy is kept and marked `failed`.
- **Signatures, with real `ssh-keygen`.** Covered cases:
  - valid;
  - one byte of the body changed;
  - one byte of the front matter changed;
  - wrong namespace;
  - signer not in `allowed_signers`;
  - `allowed_signers` without `namespaces=`;
  - missing `-I` principal;
  - `sk-ssh-ed25519` (a fixture signature);
  - `ssh-keygen` missing, which gives "cannot verify";
  - a key removed from `allowed_signers` while the charter and signature
    stay unchanged → no longer `valid` (the cache is invalidated);
  - CRLF on disk with an LF-signed file → `bad-sig`.
- **One read.** The charter is replaced between the verify and the parse
  (a test hook) → the grant matches the bytes verified, never the new file.
- **Grammar.** Duplicate key; `expiers:` → `bad-charter` with the hint; a
  name that differs from the filename; `data_class` read as `data-class`;
  both `status: approved` and `approval: pending` → `draft`.
- **Truncation.** 450 delegates with the alarm on the 401st by name → it
  is in block one, the tile reads 1, and every row appears across the
  blocks. This checks rows shown, not just that the validator accepted
  the snapshot.
- **Notices.** One alarm, then a different alarm with the same count →
  the id changes. "Not now" on the first does not hide the second. No
  more than 3 notices.
- **Never zero.** No declared cost → `unknown`.
- **Converter.** On the ranch selftest fixtures (Phase 0a), the converter
  and the model give, for each fixture, the level full Corral gives. The
  exception is `unapproved-1` with a live box, which is `bad` here on
  purpose and is asserted as a known difference.

### 12.2 Seam and core (Light's repository)

- Light's suite passes unchanged with the module installed and removed.
- **Inbox bind** (if Q2 goes that way). The collector can read the inbox
  and cannot write it. The interactive CLI cannot see it at all. A module
  without `inbox: true` gets no inbox. `--purge` deletes it. A symlinked
  inbox folder is refused at bind time.
- **The CLI sandbox has no `~/.ssh`.** A sentinel key in a test home is
  absent inside `cli.py setup`.

### 12.3 Phase 2

These run against a local sshd fixture:

- each of the six outcomes;
- a hashed `known_hosts` entry matches;
- `host_key_alias` is honoured;
- a non-standard port works;
- `@revoked` is honoured.

These check the probe's limits:

- the probe cannot reach a second listener on another port or address;
- `~/.ssh/id_*` is absent inside its profile;
- 40 targets are capped at 32, within 30 s;
- an inbox file naming a host adds no target.

### 12.4 Live checks on this host (manual, before calling v1 done)

- A real charter signed with the operator's key verifies. Editing one byte
  turns it `bad-sig` within one run.
- The ranch inbox, converted, matches full Corral's Delegates room for
  every fixture except the known `UNAPPROVED`-with-box difference.
- Deleting a charter whose box is listed keeps the rail's alarm.
- `module remove delegates --purge` leaves Light as it was.
