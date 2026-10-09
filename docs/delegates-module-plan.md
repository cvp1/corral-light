# Delegates as the second Corral Light module (and Fleet after it)

> Status: rev 1, 2026-10-09. Draft, not yet reviewed by a panel. Nothing
> built.
>
> Decided by the operator on 2026-10-09, before this draft:
> 1. **Generic and public**, as FinOps is. Anyone can install it. Full
>    Corral's ranch estate (Lightsail delegate boxes, gpg-signed charters,
>    `delegates/status.py`) is one way to configure it, not the thing it
>    is built around.
> 2. **The board first, lanes later.** v1 is a read-only room that fits the
>    seam FinOps built. Talking to a delegate box, which full Corral does
>    with `delegate:<box>` lanes, needs a lane primitive in the seam. That
>    primitive gets its own design and panel review before anything is
>    built (§8).
> 3. **Delegates, then Fleet.** Fleet gets its own plan once this one is
>    built. §10 records what is known about it so far.

## 0. The ask

Port full Corral's Delegates into Corral Light as a module in its own
repository (`corral-light-delegates`), under the seam and trust model of
`docs/finops-module-plan.md` §3 to §5. Spec it, plan the build and the
tests, and send it to the panel.

The question the board answers: **which agents have I handed work to on
other machines, what are they allowed to do, until when, what do they
cost, and is any of them running past what I allowed?**

## 1. What full Corral has, and what carries over

Full Corral's Delegates (measured from a fresh read-only clone of
`cvp1/corral` on 2026-10-09) is made of two separate pieces:

| Piece | Where | What it does |
|---|---|---|
| **The room** | `delegates.py` (74 lines), `GET /api/delegates`, `buildDelegates()` in `app.js` | Shows the rows from `delegates/status.py --json` exactly as they arrive, run as a subprocess with a 20 s timeout. One route, no loop, no attention class, polled every 60 s. Rows: `name`, `state`, `box`, `hours_left` or `expires_unparseable`, `spend.box` (`amount_usd`, `as_of`, `note`), `spend.credentials[]`, `audit_events`, plus a board-level `unattributed_audit`. |
| **The lanes** | `sessions.py:504-720`, `delegates/delegate_acp.py` | One chat-only lane per live delegate-tier box listed in `lightsail/ACTIVE-BOXES.md` or `~/.config/corral/delegate-hosts.json`. Each lane is ssh, with Craig's key, to `chat_bridge.py` on the box, which calls its broker's `llm` verb. It has no tools and no filesystem, and the box's charter data-class gate is the boundary. Sending vault content to one needs a confirm that names the destination. |

States in full Corral, with the colour each one gets (`DEL_STATE_CLASS`):
`LIVE` ok; `READY` warn (signed and in date, with no box: not live);
`UNAPPROVED`, `DRAFTED` warn; `OVERDUE-REVOKE`, `BAD-SIG` bad; `REVOKED`,
`EXPIRED` unknown.

Rules that carry over unchanged:

- **The viewer computes nothing.** Every state, TTL and spend figure is
  worked out by the module. Light renders what it reports.
- **Charter TTL is not uptime.** A countdown always reads "expires in
  …". A bare `+522h` next to `LIVE` was once read as uptime (Craig,
  2026-08-24).
- **A broken source makes the board empty and shows an error. It never
  takes the room down.**
- **The room has no action buttons.** Renewing and revoking happen
  elsewhere: at the operator's keyboard, with their key.
- **Null is unreported, never zero** (from FinOps). A delegate with no
  declared cost shows `unknown`, never `$0`.

Not carried over: gpg and `cc-handoff/verify_task.py`, Lightsail's
`ACTIVE-BOXES.md` as a required input, `delegates/spend.py`, audit-log
attribution, the Fleet room's box cards, and the lanes (§8).

## 2. What a delegate is, generically

A **delegate** is an agent the operator runs on a machine other than this
one, under a written grant. It has two halves, and either can exist
without the other:

- **A charter.** A file the operator writes. It names the delegate, says
  what the delegate may touch (data classes, credentials, budget) and when
  the grant expires, and is optionally signed by the operator.
- **A box.** The machine the delegate runs on: a cloud VM, a home server,
  a container host. It is known from an inventory: something the operator
  wrote, or something a provisioning tool or cloud API reported.

The board joins them by name and works out one state per delegate:

| State | Charter | Box | Level | Meaning |
|---|---|---|---|---|
| `DRAFTED` | present, `status: draft` | any | warn | written, not yet approved |
| `UNSIGNED` | approved, signing required, no signature | any | warn | full Corral's `UNAPPROVED` |
| `BAD-SIG` | signature present and fails | any | bad | the file changed after it was signed, or the wrong key signed it |
| `READY` | valid, in date | none | warn | granted but not running |
| `LIVE` | valid, in date | present | ok | |
| `EXPIRED` | past `expires` | none | unknown | finished cleanly |
| `REVOKED` | `status: revoked` | none | unknown | finished cleanly |
| `OVERDUE-REVOKE` | expired or revoked | **present** | bad | **a box is still running on a grant that ended. This is the alarm.** |
| `ORPHAN` | none | present | warn | inventory reports a box that no charter names (new; full Corral has no such row) |

`expires` is required. A charter with no parseable `expires` is
`BAD-CHARTER` (bad) with the reason, rather than a grant with no end.

## 3. Trust model

The collector gets what FinOps §3 already allows and nothing more: system
directories read-only, its config folder read-only, its data folder
writable, the feed read-only, no network, and no home directory. In
particular it never sees `~/.ssh`, cloud credentials, or the boxes.

That is enough for v1, because none of v1's inputs need a network or a
secret:

- Charters are files in the module's config folder.
- Signatures are checked with **public** keys, using `ssh-keygen -Y
  verify` (OpenSSH 8.1 or later, in `/usr/bin`, which the sandbox mounts
  read-only). The `allowed_signers` file sits in the config folder.
- Inventory is files: some the operator writes, and some an outside tool
  drops into an inbox (§4.3).

What a charter reveals: delegate names, hostnames, data-class names,
budget figures. All of these stay on this machine and appear only behind
Light's cookie, as every module view does.

The board is not proof. A `LIVE` row means "a charter is valid and an
inventory says a box exists". It does not mean the box is up, that it is
the box the charter meant, or that the agent on it obeys the charter.
Each row names its inventory source and how old it is (`fresh_at`), and
the module's docs say this in their first paragraph.

## 4. Sources

### 4.1 Charters (`<config>/charters/<name>.md`)

```markdown
---
name: cal-1
status: approved          # draft | approved | revoked
expires: 2026-11-01T00:00:00Z
box: cal-1                # the inventory name; defaults to `name`
data_classes: calendar-read, mail-draft
credentials: google-calendar-ro
cost_box_usd_month: 5.00  # declared; optional
cost_note: Lightsail nano
---

What this delegate is for, in the operator's words. The signature covers
every byte of this file, front matter and body.
```

- The front matter is flat `key: value` lines, parsed by the module itself
  with no YAML dependency. Unknown keys are kept and shown under "other";
  they are never refused, so old charters outlive new modules.
- A signature is `<name>.md.sig`, made with `ssh-keygen -Y sign -n
  corral-light-delegate-charter -f <key> <name>.md`. The fixed namespace
  means a signature made for something else does not verify here.
- `<config>/config.toml` holds `require_signature = "yes" | "no"`
  (default `no` on a new install, so the board is useful at once;
  `setup` offers to turn it on). When it is `yes`, an approved charter
  without a valid signature is `UNSIGNED` or `BAD-SIG`, never `LIVE`.
- Revoking is an edit (`status: revoked`) and, when signing is required,
  a fresh signature. Deleting the file removes the row. If its box is still
  in inventory, that box then shows as `ORPHAN`, so a deletion cannot hide
  a running box.

### 4.2 Declared inventory (`<config>/boxes.toml`)

A box the operator lists by hand: `name`, optional `host`, `provider`,
`since`, `cost_usd_month`. This is the generic counterpart of
`ACTIVE-BOXES.md`. Rows of kind `declared`.

### 4.3 The inbox (`<config>/inbox/*.json`)

Any outside tool the operator runs can drop a file here in the schema
`corral-light.delegates.inbox/1`:

```json
{"schema": "corral-light.delegates.inbox/1",
 "source": "ranch delegates/status.py",
 "generated_at": "2026-10-09T18:00:00Z",
 "boxes":    [{"name": "cal-1", "provider": "lightsail", "since": "2026-08-16",
               "cost": {"usd_month": "5.00", "kind": "vendor", "as_of": "2026-10-01"}}],
 "charters": [{"name": "cal-1", "state": "LIVE", "expires": "2026-11-01T00:00:00Z",
               "verified_by": "gpg"}]}
```

- This is how an estate plugs in **with no new privilege**. On the ranch,
  a cron line runs `delegates/status.py --json`, passes the result through
  a 20-line converter shipped in the module's `contrib/`, and writes the
  file atomically into the inbox. The module never runs the operator's
  tools. It reads what they leave behind.
- Charters from an inbox are shown with their source's own verdict and a
  `verified_by` label. The module cannot re-check a gpg signature it never
  saw, and says so. When a local charter and an inbox charter share a
  name, the local one wins, and the row says it is shadowing the inbox
  one.
- Limits: 16 files, 1 MiB each, 500 rows in total. A file older than its
  `stale_after_s` (default 2 h) shows its rows marked stale. Each malformed
  file is skipped with its own error line, and the other files still load.
- The config folder is read-only to the collector and writable by the
  operator, so the inbox has the same write access as the config itself.
  §9 Q2 asks whether it should live somewhere else.

### 4.4 Cloud inventory (Phase 3, opt-in)

Fetchers for Lightsail, Hetzner, DigitalOcean and GCP list instances
through the exact-host egress machinery FinOps Phase 4 built
(`module_fetch.py`, `review_egress.py`). Each needs a read-only API key
the operator grants with `module key` and `module grant`. Rows of kind
`vendor`. Nothing in v1 depends on this.

### 4.5 Reachability (Phase 2, core change)

An inventory says a box exists. It does not say the box answers. Phase 2
adds a core-run report in the style of `grok-usage` (FinOps §4.2):
`ssh-reach`. For each box with a `host` that the operator lists, the
**core**, not the module:

1. runs `ssh-keyscan -T 5 -t ed25519 <host>` with no credentials in its
   own sandbox, network allowed to that host's port 22 only;
2. compares the key to the operator's `known_hosts` (read by the core, not
   bound for the module);
3. writes `module-feed/v1/reports/ssh-reach/<box>.json` with `{reachable,
   key_matches, rtt_ms, checked_at, error_class}`.

This proves that a machine answers and holds the expected host key. It
never logs in, never runs a remote command, and touches no private key.
Whether the agent is running stays out of scope until lanes exist. A
`LIVE` row whose box is unreachable becomes `LIVE` with level `warn` and
the note "inventory says up; unreachable for 3 checks".

## 5. What the board shows

A `corral-light.module/1` snapshot (FinOps §4.5), so no core renderer
change is needed:

- **Tiles:** Live (count); Needs attention (the count of `OVERDUE-REVOKE`,
  `BAD-SIG`, `BAD-CHARTER` and `ORPHAN`, level `bad` when not zero);
  Expiring within 72 h; Declared monthly cost (sum of declared costs, kind
  `declared`, or `unknown` when nothing is declared; never `$0`).
- **Table "Delegates":** Name, State, Box, Expires ("expires in 41 h",
  "expired 3 h ago", "no expiry: BAD-CHARTER"), Cost (each figure with its
  kind), Source (`charter`, `charter + boxes.toml`, `inbox: ranch …`, with
  its age).
- **Table "Sources":** each charter folder, `boxes.toml` and inbox file,
  with row counts, age and errors.
- **Notes:** the honesty line from §3.

**Notices** (FinOps §4.7, Phase 3 of the seam): `OVERDUE-REVOKE` is
`bad` (a notice never pages; modules cannot reach the core's page classes),
with the text "cal-1's charter ended 3 h ago and its box is still listed".
A charter expiring within 24 h is `info`. The rail shows at most 3 notices
per module, ordered by level, so with more than one overdue delegate the
module raises **one** aggregate `bad` notice ("2 boxes are running past
their charters") rather than one per delegate. Each notice stops when the
charter is renewed, the row goes away, or the module goes stale.

Snapshot cadence: `every_s` 300, `budget_s` 10. Everything is local file
reads plus at most one `ssh-keygen -Y verify` per changed charter. Each
verification is cached in the data folder by the charter's and the
signature's SHA-256.

## 6. The module (`corral-light-delegates`)

```
module.json            manifest: collector, cli, doctor; reads: [], network: none
delegates/charter.py   front-matter parse, state machine
delegates/sources.py   charters, boxes.toml, inbox
delegates/verify.py    ssh-keygen -Y verify wrapper with a cache
delegates/view.py      snapshot builder
collector.py           snapshot
cli.py                 setup | show | doctor | new <name> | sign <name> | check
contrib/ranch_status_to_inbox.py
```

- `reads` is empty. v1 needs nothing from the feed. Phase 2 adds
  `ssh-reach` under `vendor_reports`, renamed in §9 Q1.
- `cli.py new <name>` writes a charter template. `cli.py sign <name>`
  **prints** the exact `ssh-keygen -Y sign` command for the operator to
  run; it does not sign. Measured against the built seam: the module CLI
  runs in the same sandbox as the collector (config writable, nothing
  else of home), so it cannot see a private key, and that is correct.
- `setup` finds `~/.ssh/allowed_signers` or offers to create a one-line
  one from a chosen public key, writes `config.toml`, and creates the
  `charters/` and `inbox/` folders.

## 7. Core changes

| Phase | Change |
|---|---|
| 1 | **None.** v1 uses only what the seam already provides: config read-only, data writable, `/usr/bin` read-only, snapshot views, notices. This is the test of whether the seam was built general enough; any core change that turns out to be needed is a finding to report, not something to add quietly. |
| 1 | `modules/index.json` gains `delegates`. |
| 2 | `vendor_reports.py` gains `ssh-reach` (§4.5): its own sandbox profile, port-22 egress to the named hosts only, `known_hosts` read by the core. The manifest vocabulary gains the name. |
| 3 | None beyond FinOps Phase 4's fetcher machinery; a host list per provider. |

## 8. Lanes: what the later design must answer

Not built in this plan. Recorded so that the board's data model does not
block it.

- A lane is a **write capability**: it sends the operator's words, and
  maybe their files, to another machine. Every module capability so far is
  read-only. The design-6 residual (`design-6-fable-residuals.md` §2.3)
  still applies: by the seam's own rule, "a module needing more is core",
  so this may be a core lane type, `delegate:`, beside `host:`, fed by the
  module's inventory, not a module API.
- What Light already has: `host:` lanes from `ssh-hosts.json`. These are a
  shell, not a chat, have no permission rail, and must never be handed to
  an agent. A delegate lane is different: chat-only, with the box's broker
  as the boundary.
- Questions for that design: what the box end speaks (ACP over ssh? full
  Corral's `chat_bridge.py`?); how the destination-naming confirm
  carries over; how `OVERDUE-REVOKE` blocks opening a lane; whether a lane
  may exist for a box with no charter.

## 9. Open questions

1. **The report vocabulary's name.** `vendor_reports` was named for Grok.
   `ssh-reach` is not a vendor. Rename it to `core_reports` in the manifest
   with `core_api` 2, or add a sibling key?
2. **The inbox's location.** Inside the config folder (no core change,
   same trust as config), or a separate `<state>/module-inbox/<name>/`
   that the core mounts read-only (cleaner: config stays the operator's
   own text, and tools write to a place meant for tools)?
3. *(Settled while drafting: `cli.py sign` prints the command. The
   seam runs the CLI sandboxed, with no view of `~/.ssh`.)*
4. **gpg.** Support it as a second signature type in v1, so the ranch's
   charters verify locally instead of arriving as the inbox's verdict?
   It costs a gpg keyring binding in the sandbox.
5. **`ORPHAN`.** New relative to full Corral. Is a box with no charter an
   alarm (`warn`), or normal for an operator who runs servers that are not
   delegates? Proposed: `warn` only for boxes whose inventory marks them
   `tier: delegate`, otherwise not shown.

## 10. Fleet, next (not planned here)

From the same clone: `fleet.py` (500 lines) bridges to
`ranch-hub/attention_api` and the cc-handoff mailbox, ranks tasks into the
needs-you rail, opens "work it here" panes, stages signing bundles, and
asks dogma-2 to open its own YubiKey signing terminal. `estate.py` renders
inside the Fleet room. `fleet_objects.py` is a shared inventory seam that
FinOps and Status also import. It has 4 routes, rides the attention tick,
and 18 tests mention it.

A generic Fleet is a different product: **this operator's machines and
hubs**. Which hosts exist, whether each one's Light hub and scheduled
jobs are fresh, what each is working on, and tasks addressed between
them. Notes for its plan:

- The signing-related parts (stage, open-sign, mesh promotion) stay out
  of any module. The design-6 plan puts them in core "regardless", and in
  Light they may simply not exist.
- Hub-to-hub (design-6 Stage D) is parked. A Fleet board can still be
  built from what other hosts publish (a STATE.md-like heartbeat file per
  host, synced by the operator's own means) through the same inbox
  pattern as §4.3.
- Delegates' box inventory (§4.2 to §4.4) is Fleet's machine list in
  miniature. Build it so Fleet can reuse it; this is the `fleet_objects`
  lesson from design-6 LIVE.md finding 3, where modules came to depend on
  modules.

## 11. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Measure** | (a) The real `delegates/status.py --json` output and one real charter from ranch-server, obtained by a cc-handoff request; it settles §4.3's converter and the state list. (b) `ssh-keygen -Y verify` inside the collector sandbox on this host: binary present, no `$HOME` needed, exit codes. (c) A file written into the config folder by another process is seen on the next run. (d) The aggregate overdue notice under the seam's limit of 3 per module. | a results doc; this plan updated where an answer changes it |
| **1. Board v1** | §4.1 to §4.3, §5, §6; no core change | §12.1 passes; installed here with `module add delegates`; on this host a sample charter, a `boxes.toml` and a converted ranch inbox render, and an expired charter with a box shows one rail notice |
| **2. Reachability** | §4.5 in core, plus the module reading it | §12.2 passes; a stopped test VM turns its row to warn within three checks |
| **3. Cloud inventory** | §4.4 fetchers, opt-in | each tested against a local stub, as FinOps Phase 4 was |
| **4. Lanes** | a separate plan (§8) with its own panel | not scheduled |

## 12. Testing plan

### 12.1 Module (its own repository, CI on Python 3.9, 3.12, 3.14)

- **State machine:** one test per row of the §2 table, plus `BAD-CHARTER`
  (missing `expires`, unparseable `expires`, `expires` with no time zone).
  `OVERDUE-REVOKE` is tested from both directions: expired with a box, and
  revoked with a box.
- **Expiry wording:** in 41 h, expired 3 h ago, and the exact boundary.
  The phrase "expires" is always present, so it never reads as uptime.
- **Signatures, against real `ssh-keygen`:** valid; body edited after
  signing → `BAD-SIG`; front matter edited → `BAD-SIG`; wrong namespace →
  `BAD-SIG`; a signer not in `allowed_signers` → `BAD-SIG`;
  `require_signature = no` with no signature → `LIVE`. The cache is
  invalidated by a one-byte edit.
- **Inbox:** valid file; malformed JSON skipped while other files still
  load; over the size limit; over the row limit; stale; schema version
  unknown; a name shadowed by a local charter, with the label present.
- **Never zero:** no declared cost → the tile is `unknown`, never `$0`.
- **Deletion cannot hide a box:** remove a charter whose box is listed →
  `ORPHAN` (or, per §9 Q5, the agreed outcome).
- **Snapshot:** stays within every core bound (FinOps §4.5) with 500
  delegates; the core validator from Light's test kit accepts it.
- **Converter:** `contrib/ranch_status_to_inbox.py` maps the measured
  ranch fixture from Phase 0 to rows equal to full Corral's for the same
  input.

### 12.2 Seam and core (Light's repository)

- Phase 1: Light's suite passes unchanged with the module installed and
  with it removed, as FinOps's does (FinOps §8.3).
- Phase 2: `ssh-reach` against a local sshd fixture: reachable with a
  matching key; key mismatch; refused; timeout. The report process cannot
  read `~/.ssh/id_*` (a sentinel key file is absent inside its sandbox).
  It reaches no host but the named one on port 22 (a second listener
  stays silent).

### 12.3 Live checks on this host (manual, before calling v1 done)

- A real charter signed with the operator's key verifies; editing one
  byte turns it `BAD-SIG` within one run.
- The ranch inbox, delivered by its cron line, matches full Corral's
  Delegates room row for row on the same minute.
- Uninstall leaves Light exactly as before (`module remove delegates
  --purge`).
