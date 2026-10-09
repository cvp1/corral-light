# Delegates as the second Corral Light module (and Fleet after it)

> Status: rev 3, 2026-10-09. Not yet reviewed by a panel. Nothing built.
>
> **Rev 3** answers panel round two on rev 2: AMEND, AMEND, AMEND
> (`reviews/2026-10-09-delegates-module-panel/synthesis-r2.md`). Main
> changes:
>
> - **The box is the unit of the alarm.** A listed box is covered when a
>   valid grant names it. Rev 2's ledger left a reused or renamed box
>   raising an alarm forever.
> - **`unknown` can keep an alarm but never quiet one.**
> - **Revocation is ordered by a signed `issued` key**, and revocation
>   tombstones are kept forever.
> - **"Complete" is a per-file claim** the operator must allow per source.
>   The ranch converter never claims it.
> - **Edit-distance typo detection is gone.** Required security keys
>   replace it.
> - **The Phase 2 probe compares key blobs.** Its targets need a
>   core-side allow, not module config.
> - **`core_api` stays compatible:** the core accepts any version up to
>   its own.
>
> **Rev 2** answered round one: AMEND, AMEND, REJECT (`synthesis.md`).
> It gave each delegate three axes (grant, box, source) and a grant
> ledger, and recomputed inbox rows on this host's clock. It put the inbox
> in a core-mounted folder (pending Q2), took a pasted public key in
> `setup`, split tables at 200 rows, and gave Phase 2 its own sandbox.
> It also corrected two rev 1 errors found in Phase 0
> (`docs/delegates-phase0.md`): the ranch signs with OpenSSH, not gpg,
> and a bare-date `expires` is valid.
>
> Decided by the operator on 2026-10-09: **generic and public**; **the
> board first, lanes later**; **Delegates, then Fleet**.

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
| **The lanes** (`sessions.py:504-720`) | One chat-only lane per live delegate-tier box, over ssh to `chat_bridge.py` and the box's broker. |

How the ranch works (Phase 0, read from code, not measured):

- **Charters** are `charters/<name>.md`, with flat hyphenated front
  matter. Each is signed by a detached OpenSSH signature `<name>.md.sig`
  from a hardware key.
- **States**, worst first: `OVERDUE-REVOKE`, `BAD-SIG`, `LIVE`, `REVOKED`,
  `EXPIRED`, `UNAPPROVED`, `READY`, `DRAFTED`.
- **"Live"** means a `tier == "delegate"` box row exists.
- **"Revoked"** comes from audit events.
- **`status.py` lists charters, not boxes.** A box with no charter file
  is not in its output.
- **One gap:** a live box with a signed but unapproved charter shows as
  `LIVE`. Rev 3 does not copy that.

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
a **grant id**: the SHA-256 of the charter's bytes, shown shortened. Its
status is the first of these that applies, in this order:

| Status | When |
|---|---|
| `bad-charter` | unreadable under §4.1: `expires`, `issued`, or both `status` and `approval` missing; a time with no zone; a duplicate key; a malformed key line; a name that differs from the filename |
| `bad-sig` | a `.sig` that fails, or one that cannot be checked (labelled "cannot verify: <reason>") |
| `ended:revoked` | `status: revoked`; or a revocation from any source; or a tombstone (§2.4) whose `issued` is equal to or later than this grant's |
| `ended:expired` | now ≥ `expires`, or the ledger has recorded `ended_at` for this grant id (§2.4) |
| `unsigned` | approved and signing required, but there is no `.sig` |
| `draft` | not approved: `status: draft`, or `approval` set to `""`, `unsigned`, `pending`, `proposed`, `>` or `|` (case-insensitive). If both `status` and `approval` are present, the more restrictive one wins |
| `valid` | approved, in date, and the signature is good (or signing is off) |

For an inbox grant, `valid` also requires the source to report
`approved: true` and `signature: verified`. The row then says "valid per
<source>" (§4.3).

### 2.2 Boxes

A box is identified as `<source-id>/<box-name>`. A box seen under the same
name by two sources is joined when a grant's `box:` names it or when the
operator lists the alias in `boxes.toml`. Each box has a **presence**:

| Presence | When |
|---|---|
| `listed` | a fresh source lists it |
| `listed-stale` | only stale sources list it, and no fresh complete source omits it |
| `not-listed` | a fresh complete source that covers it omits it. **This beats a stale listing**, which stays visible as a note. |
| `unknown` | none of the above: never seen by a fresh complete source, or every source covering it is failed, partial or stale |

### 2.3 Coverage and severity

A box is **covered** when at least one `valid` grant names it in `box:`.
A grant that does not name the box never covers it, even when it is
valid.

A box is **in scope** when a source reports it as `tier: delegate`, or
when any grant names it, or when the ledger ties it to a grant (§2.4).
Other boxes are Fleet's and are not shown.

| Presence | Covered | Level | Label |
|---|---|---|---|
| `listed` | yes | ok | covered by <grant>; box listed |
| `listed-stale` | yes | warn | covered by <grant>; box listed by a stale source |
| `listed` or `listed-stale` | no | **bad** | **box listed without a valid grant (<reason>)** |
| `unknown` | — | **the level it had when last seen listed**, else warn | "box unknown since <time>; last seen <state>" |
| `not-listed` | — | info | box gone |

`<reason>` comes from the grants that name the box, worst first:
"expired 3 h ago", "revoked 2026-10-02", "charter removed", "never
approved", "signature fails". If no grant names it, the reason is "no
charter names this box".

**Alarm continuity.** Once a box has been `bad`, only two things lower
it: a `valid` grant that names it, or a fresh complete source reporting
it `not-listed`. A failed, partial or stale source does not lower it.
Neither does deleting a charter, editing `box:`, a tier change, or
removing a source from config. Only the operator's own complete source
can do it: `boxes.toml`, where deleting a line means "gone" (§4.2).

### 2.4 The ledger and tombstones

Both files are in the data dir. Only the collector writes them, with a
write to a temp file and a rename. If either is corrupt, it is moved aside
and the board shows a `bad` Sources row ("ledger reset; alarm history
lost") until the operator acknowledges it with `cli.py ack-ledger`.

- **`ledger.json`** holds, per box, every grant id that ever named it and
  the level the box last had. It also holds, per grant id, `ended_at` the
  first time the grant was seen ended. That makes the clock running
  backwards unable to revive an expired grant: only a grant with a
  greater `issued` can reopen the delegate. Each box's entries are kept
  for 30 days after the box is `not-listed`. The period is fixed. A zero
  would be a switch that quiets alarms.
- **`tombstones.json`** holds, per delegate name, the highest `issued`
  among the grants seen revoked. It is kept forever and is small. A
  restored older or equal-`issued` charter stays `ended:revoked`.
- A rename is a new grant with a new name. The old grant's box is covered
  as soon as the renamed charter, which is valid, names the box. Nothing
  wedges.
- **What this protects against:** mistakes, stale tools and restored
  files. It does not protect against the operator, because same-user code
  can delete the data dir. The docs say so.

### 2.5 Time

- `expires` and `issued` are each one of: RFC 3339 with `Z`; RFC 3339 with
  an offset (`+00:00` or `-0700`); or a bare `YYYY-MM-DD`. For `expires`,
  a bare date means 23:59:59Z that day; for `issued`, it means 00:00:00Z.
  A date-time with no zone is `bad-charter`.
- Ended means `now >= expires`, on the collector's UTC clock, and is then
  latched in the ledger (§2.4).
- If the clock runs more than 5 min behind the last run's `generated_at`,
  a clock warning goes into Sources and countdowns show "(clock
  uncertain)".
- An inbox `generated_at` more than 10 min in the future fails that
  source.

## 3. Trust model

The collector gets what FinOps §3 allows: system dirs read-only, the
config folder read-only, the data folder writable, no network, and no
home directory. Under rev 3 it also gets the inbox folder read-only
(§4.3). **The module CLI runs in the same sandbox,** with config writable
and no view of `~/.ssh` (`modules.py:886-896`). No module path touches a
private key.

**The board is not proof.** "Box listed" means a source says a box
exists. It does not mean the box is up, or is the machine the charter
meant, or obeys the charter. Every row names its sources and their ages.
The honesty line is the first note **and** the subtitle of the "Covered"
tile.

**Separation is not isolation.** The inbox lives apart from the config
folder, so the module's CLI cannot write inventory and a converter has
no reason to touch charters. Any process running as the operator can
still write both. The docs say this plainly.

## 4. Sources

### 4.1 Charters (`<config>/charters/<name>.md`)

The config folder is `dirname($CORRAL_MODULE_CONFIG)`.

```markdown
---
name: cal-1
status: approved
issued: 2026-10-01T00:00:00Z
expires: 2026-11-01T00:00:00Z
box: cal-1
data-class: calendar-read
capabilities: [calendar.read, mail.draft]
budget-usd: 5
cost-box-usd-month: 5.00
---

What this delegate is for, in the operator's words.
```

**Grammar.**
- The file is UTF-8. CRLF is read as LF for parsing only; the signature
  covers the exact bytes.
- The front matter starts at byte 0 between two `---` lines.
- Each line is `key: value`, with the key matching
  `[a-z][a-z0-9_-]*`. `_` is folded to `-`.
- A non-blank line that is not a valid key line, or not a comment (`#`
  at the start), is `bad-charter`. `Status:` is one.
- A value is a bare string, a `"…"` string, or a `[a, b]` list.
- A duplicate key, after folding, is `bad-charter`.

**Required:** `name`, which must match the filename stem and
`^[a-z][a-z0-9-]{0,60}$`; `issued`; `expires`; and at least one of
`status` (`draft | approved | revoked`) or `approval` (the ranch's form).
A misspelled security key therefore shows up as a missing one. A
misspelled `box` falls back to the default (`name`); the row shows the
`box` it used.

**Understood:** the keys above, `box`, `data-class`, `capabilities`,
`tools`, `budget-usd`, `credential-N-type`, `cost-box-usd-month`,
`cost-note` and `created`. **Every other key** is kept and shown under
"other". There is no typo guessing.

**Signatures.** The collector reads the charter once, then verifies and
parses the same bytes:

```
ssh-keygen -Y verify -f <config>/allowed_signers -I <principal>
           -n <namespace> -s <name>.md.sig     # charter bytes on stdin
```

- `principal` and `namespace` are set per charter folder in
  `config.toml`. The default namespace is
  `corral-light-delegate-charter`; a ranch folder uses the ranch's own,
  once Phase 0 confirms it.
- Any key type `ssh-keygen` accepts is fine, including `sk-ssh-ed25519`.
  Verifying needs no hardware.
- `ssh-keygen` gets a 5 s timeout. If it is missing, times out or meets
  an unsupported key, the grant is `bad-sig`, labelled "cannot verify".
- The cache key is the SHA-256 of the charter, the signature,
  `allowed_signers`, the namespace, the principal and the `ssh-keygen`
  version.
- `require_signature` defaults to `yes` once `allowed_signers` exists.
  While it is `no`, the Covered tile reads "covered (unsigned charters)".

**Revoking** means `status: revoked`, a newer `issued` and a fresh
signature. **Renewing** means a newer `issued` and `expires` and a fresh
signature. **Deleting** a charter never closes a box (§2.3).

### 4.2 Declared inventory (`<config>/boxes.toml`)

The operator lists boxes by hand. Each has `name`, `tier`, `provider`,
`since` and `cost_usd_month`, plus `aliases` (names under which other
sources report the same box). It is a **complete** source of kind
`declared`. **Deleting a line is the operator saying the box is gone.**
That lowers an alarm, which is the operator's call to make. Sources
shows "removed from boxes.toml by hand: cal-1" for 7 days, and the ledger
keeps the history. Probe targets are not configured here (§4.5).

### 4.3 The inbox (`<state>/module-inbox/delegates/`; pending §9 Q2)

Outside tools drop JSON files here. The core creates the folder (0700)
when the manifest sets `"inbox": true`. It binds the folder read-only into
the collector's sandbox, and **leaves it out of the CLI's sandbox
entirely**. The CLI path must not reuse the read-only list the way
`fetched` does (`modules.py:882-891`).

**Sources are declared** in `config.toml`. Each has an `id`, a `file`, a
`label`, `stale_after_s` (default 7200), and `may_claim_complete` (default
`no`). An undeclared file is ignored and listed as "undeclared file".

```json
{"schema": "corral-light.delegates.inbox/1",
 "generated_at": "2026-10-09T18:00:00Z",
 "complete": false,
 "boxes":  [{"name": "cal-1-node", "tier": "delegate", "provider": "gcp",
             "since": "2026-08-16",
             "cost": {"usd_month": "1.75", "kind": "vendor", "as_of": "2026-09-04"}}],
 "grants": [{"name": "cal-1", "issued": "2026-09-04T00:00:00Z",
             "expires": "2026-11-01T00:00:00Z", "box": "cal-1-node",
             "approved": true, "signature": "verified",
             "revoked_at": null, "reports_state": "LIVE"}]}
```

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
  from any source always apply**, shadowed or not.
- **Conflicts** are shown, not resolved. The more restrictive value wins,
  and every source is listed.
- **Limits.** Regular files only. At most 16 files, 1 MiB each (read with
  a cap) and 2,000 rows in total; anything cut short is `partial`. A file
  that does not parse is `failed`, and its last good copy is kept as
  `stale` evidence: it can keep an alarm, never lower one (§2.3).
- **The ranch** uses `contrib/ranch_status_to_inbox.py`.
  - It reads `status.py --json` stdout only.
  - It never writes `"complete": true`, because `status.py` lists
    charters, not boxes.
  - It maps `box.tier`, `expires`, `approval_signed` to `approved`, and
    `sig_ok`/`signed` to `signature`.
  - It turns the audit-event revocation into `revoked_at`.
  - It fills `issued` from the charter's `created`.
  - It runs from the ranch's own cron, and the file reaches this host
    through whatever sync the operator already has.

### 4.4 Cloud inventory (Phase 3, opt-in)

Fetchers for Lightsail, Hetzner, DigitalOcean and GCP, through FinOps
Phase 4's exact-host egress. A fetcher writes `complete: true` only when
every page arrived. Nothing in v1 depends on them.

### 4.5 Host-key check (Phase 2, its own sandbox)

This checks that **something at the box's address answers with a host key
the operator already trusts**. It is not a login, and it does not show the
agent is running.

- **Targets are allowed by the core,** not by module config:
  `corral-light module allow-probe delegates <box> <host>[:port]`. The
  allow is stored in the core's pins (`modules.json`), and doctor lists
  it. Module config and inbox content never add a target. At most 32.
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
  3. **Compare the scanned key blobs with the returned lines.**
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
  | `not-checked` (still running at the deadline) | no change |

- **Caps:** 8 targets at a time and 30 s for the run. 32 targets at 3 s
  in waves of 8 is 12 s.
- **Where results go:** `<state>/module-reports/delegates/hostkey.json`,
  bound read-only. No `light-feed`.
- **Manifest:** `"core_reports": ["hostkey-probe"]`. This is additive.
  The core moves to `CORE_API = 2` and **accepts any manifest whose
  `core_api` is at most its own** (`modules.py:55, 295-297` accept only
  equality today). A module that names `core_reports` must declare
  `core_api: 2`.
- **On the board,** a `key-mismatch` or `key-revoked` makes the box `bad`
  whether or not it is covered. The probe can raise a level, never lower
  one.

## 5. What the board shows

- **Tiles:**
  - Covered boxes, with the honesty line as its note.
  - **Boxes listed without a valid grant**, `bad` when not zero.
  - Boxes unknown.
  - Grants expiring within 72 h.
  - Sources with problems.
  - Declared monthly cost: kind `declared`, or `unknown` when nothing is
    declared; never `$0`.
- **Table "Boxes":** one row per in-scope box. Columns: Box, Presence,
  Covered by, Level and reason (in words, because cells have no level),
  Sources. Rows are sorted by level, then name. The table splits into
  blocks of 200 (`modules.py:1004`). **Alarm rows fill the blocks first,
  in order.**
- **Table "Grants":** one row per (grant, box it names). Columns: Name,
  Grant id, Status, Issued, Expires, Box, Cost, Source. This is where
  the operator sees "expires in 41 h".
- **Table "Sources":** health, age, complete or partial, row counts and
  errors for each source. It also shows the ledger's size, a ledger reset
  if one happened, and the clock warning.

**Notices** (manifest `"notices": true`; the rail shows at most 3 per
module, `modules.py:1102`):
- **The alarm** (`bad`) is one notice for every box without a valid
  grant. Its id is `alarm.<generation>`, a short hash of the sorted alarm
  box ids, so a changed set is a new notice that comes back after "Not
  now" (`static/app.js:3250`).
- **Expiry** (`info`) follows the same rule.
- Both clear when the condition clears.

**Cadence:** `every_s` 300 and `timeout_s` 30 (enforced). `budget_s` 10 is
advisory to the module.

**A known seam limit (Q7).** If the collector itself fails, `fresh_at`
stops advancing, and the module's notices expire at `fresh_at + 2 ×
every_s` (`modules.py:1236, 1314`). A broken collector therefore drops its
own alarm from the rail. Every module has this today. The module dialog
still shows the last snapshot and the error.

## 6. The module (`corral-light-delegates`)

```
module.json            core_api 1 (2 from Phase 2); collector, cli, doctor; reads: []; network: none; notices: true; inbox: true
delegates/charter.py   grammar, required keys, verify (one read, one buffer)
delegates/sources.py   charters, boxes.toml, declared inbox sources, completeness
delegates/ledger.py    ledger, tombstones, ended_at latch, corruption handling
delegates/model.py     grant status, box presence, coverage, severity
delegates/view.py      tiles, split tables, notices
collector.py           snapshot
cli.py                 setup | show | doctor | new | sign | check | ack-ledger
contrib/ranch_status_to_inbox.py
```

- `setup` asks the operator to paste a public key line. It writes
  `<config>/allowed_signers` (principal plus
  `namespaces="corral-light-delegate-charter"`), writes `config.toml`, and
  creates `charters/`.
- `new <name>` writes a template with `issued` set to now. `sign` prints
  the exact, shell-quoted `ssh-keygen -Y sign` command and never signs.

## 7. Core changes

| Phase | Change |
|---|---|
| 1 | `modules/index.json` gains `delegates`. |
| 1 | **If Q2 is the separate inbox:** the manifest key `inbox: true`. The core creates `<state>/module-inbox/<name>/` and binds it read-only **for the collector only**, never added to the CLI's argv. `--purge` deletes it, and doctor shows it. |
| 2 | The `hostkey-probe` profile and forwarder; `module allow-probe` and its pin field; `core_reports`; `CORE_API = 2`, accepting `core_api <= CORE_API`. |
| 3 | None beyond FinOps Phase 4's fetchers. |
| Q7 | Only if the operator wants it: a stale-alarm card for a module whose last good snapshot had a `bad` notice. |

Any other core change found while building is a finding to report.

## 8. Lanes: what the later design must answer

A delegate lane is a **core lane type**, `delegate:<id>`. A module
snapshot never authorizes a send. The core takes candidate boxes from
this module's inventory, re-checks the grant itself before opening and
before every send, and refuses unless a `valid` grant names the box (and,
once Phase 2 exists, its key matches). Open questions for that design:
the protocol at the box end, carrying over the confirm that names the
destination, and per-send data-class checks.

## 9. Open questions

1. *(Settled: `core_reports`, `core_api` 2, backward-compatible.)*
2. **Where the inbox lives: the operator decides.** Round two: all three
   reviewers now favour the separate core-mounted folder, Astra
   included, though Astra argued for the config folder in round one.
   The author recommends it too. It is separation, not isolation (§3).
3. *(Settled: `sign` prints.)* 4. *(Gone.)* 5. *(Settled in §2.3: scope.)*
6. *(Settled: fixed 30 days of history; tombstones forever.)*
7. **New: should the core keep a stale-alarm card** when a module whose
   last good snapshot carried a `bad` notice stops producing snapshots?
   That would be a seam change benefiting every module. Without it, a
   broken collector quietly drops its own alarm.

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
| **0. Measure** | **Partial, 2026-10-09** (`docs/delegates-phase0.md`). Still to do: (a) on ranch-server, by a person or an authorized agent (auto-triage cannot run commands): a timed `status.py --json`, the `delegates/selftest_status.py` fixtures, and the namespace. (b) `ssh-keygen -Y verify`, including `sk-ssh-ed25519`, inside the real collector sandbox here. (c) An inbox rename is seen on the next run. (d) The alarm notice comes back when its set changes. | results doc; plan updated |
| **1. Board v1** | §2 to §6, and §7's Phase 1 rows | §12.1 and §12.2 pass; installed here; deleting an expired charter whose box is listed keeps one `bad` notice |
| **2. Host-key check** | §4.5 | §12.3 passes |
| **3. Cloud inventory** | §4.4 | fetchers tested against local stubs |
| **4. Lanes** | a separate plan | not scheduled |

## 12. Testing plan

### 12.1 Module (its own repository; CI on 3.9, 3.12, 3.14)

- **Severity table.** Every presence crossed with covered or not gives
  §2.3's level. Every grant status crossed with naming the box or not
  gives the right coverage.
- **Grant precedence.** Expired and also `bad-sig` is `bad-sig`. Revoked
  and expired is `ended:revoked`.
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
    deleted.

  It lowers **only** when a valid grant names the box, or a fresh
  complete `boxes.toml` drops it (and Sources then shows the hand
  removal).
- **Reuse and rename.** Grant A ends, and grant B (valid) names the same
  listed box → ok, "covered by B", with A shown as ended. A rename the
  same way → ok. A valid grant C that names a different box leaves the
  first box `bad`.
- **Revocation order.** A revoked charter with `issued` T: restoring the
  older approved file (`issued` < T) stays revoked; a file with an equal
  `issued` stays revoked; a re-signed charter with a later `issued` →
  `valid`. The tombstone outlives a 30-day ledger expiry (fake clock).
- **Clock.** An expired grant, then the clock set back a day → still
  `ended:expired` (the latch), with the clock warning shown.
- **Ledger corruption.** Corrupt JSON → moved aside, and the `bad` Sources
  row stays until `ack-ledger`.
- **Completeness.** These are `partial`:
  - `complete: true` when `may_claim_complete` is `no`;
  - `complete: true` cut at 2,000 rows;
  - the ranch converter's output (it never claims complete).

  Fresh and complete, with the box missing, is `not-listed`. A fresh
  complete omission alongside a stale listing is `not-listed`, with a
  note.
- **Inbox grants.** Each of these is not `valid` and leaves a named box
  `bad`:
  - `approved: false` with `expires` in the future;
  - `signature: failed`;
  - `reports_state: LIVE` but expired.

  A local approved charter plus an inbox `revoked_at` → revoked.
- **Grammar.** All of these are accepted as "other": `note`, `date`,
  `lane`, `job`, `log`, `os`, `boxes`, `bot`, `mission`, `payload` and
  `ssh-key-file`. These are `bad-charter`: `Status:`, a duplicate after
  `_` folding, a missing `issued`, a missing `expires`, and neither
  `status` nor `approval`. `approval: >` and `approval: |` → `draft`.
  `status: approved` together with `approval: pending` → `draft`.
- **Signatures, with real `ssh-keygen`.** These are `bad-sig`:
  - one byte changed in the body;
  - one byte changed in the front matter;
  - the wrong namespace;
  - an unknown signer;
  - `allowed_signers` with no `namespaces=`;
  - a missing `-I`;
  - CRLF on disk.

  `sk-ssh-ed25519` passes. A missing `ssh-keygen` gives "cannot verify".
  A key removed from `allowed_signers` → no longer `valid`. Swapping the
  file between the read and the verify → the board uses the bytes it
  verified.
- **Truncation.** 450 boxes with 210 alarms: blocks one and two start
  with all 210 alarms, the tile reads 210, and every row appears once.
- **Notices.** A changed alarm set with the same count → a new id. "Not
  now" does not hide it. At most 3 notices.
- **Never zero.** No declared cost → `unknown`.
- **Converter.** For each ranch selftest fixture, the level matches full
  Corral's, except `unapproved-1` with a live box, which is `bad` here,
  asserted as a known difference.

### 12.2 Seam and core (Light's repository)

- Light's suite passes with the module installed and removed.
- **The inbox bind.** The collector can read it and cannot write it. **The
  interactive CLI cannot see it at all**: assert its absence in the CLI's
  argv, not only that it is read-only. No `inbox: true` → no folder.
  `--purge` removes it. A symlinked inbox folder is refused.
- **The CLI has no `~/.ssh`.** A sentinel key is absent inside `cli.py
  setup`.
- **`core_api`.** With the core at 2, a manifest with `core_api: 1` still
  loads (FinOps, unchanged). A manifest with `core_api: 3` is refused.
  `core_reports` with `core_api: 1` is refused.

### 12.3 Phase 2 (a local sshd fixture)

- A **different key on a name `-F` finds** → `key-mismatch`, and a
  covered box goes `bad`.
- `@revoked` → `key-revoked`. A certificate → `cert-unsupported`. A
  hashed entry matches. `[host]:2222` is looked up for port 2222.
- A target not allowed by `allow-probe` is not contacted, even when
  `boxes.toml` or the inbox names it.
- The probe cannot reach a second listener. `~/.ssh/id_*` is absent in
  the profile. 32 targets finish within 30 s; one sleeping target is
  `not-checked`, not `no-answer`.

### 12.4 Live checks on this host (manual)

- A real charter, signed with the operator's key, verifies. A one-byte
  edit → `bad-sig` within one run.
- The converted ranch inbox matches full Corral's room for every fixture
  except the known difference.
- Deleting a charter whose box is listed keeps the rail's alarm.
- `module remove delegates --purge` leaves Light as it was.
