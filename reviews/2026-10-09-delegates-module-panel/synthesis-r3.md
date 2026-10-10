# Delegates module plan, rev 3: panel synthesis, round three (2026-10-10)

Arms, cold, read-only clone at `phase0-measured` 837aaa1 (rev 3 at
6d738ed plus the measured Phase 0 and its fixture), via `corral-light
consult ask`, charge `charge-r3.md`:

| Arm | Model | Verdict | Wall | File |
|---|---|---|---|---|
| Codex | gpt-6-astra | AMEND | 89 s | r3-astra.md |
| Grok | grok-4.6, effort high | AMEND | 308 s | r3-grok.md |
| Gemini | gemini-pro-agent | AMEND | 174 s | r3-gemini.md |

No arm said ADOPT. All three put the same two defects first, and the
author confirmed each claim marked PROVEN below against the clone:

- The macOS Seatbelt profile denies process-fork, so a sandboxed module
  starts no child processes (`module_sandbox.py:194-200`,
  `docs/finops-macos-sandbox.md §3`). The collector's `ssh-keygen -Y
  verify` cannot run on macOS as designed. Astra, PROVEN.
- `inbox` is not in `MANIFEST_KEYS` and unknown keys are refused
  (`modules.py:70-72, 292-294`): Phase 1's core change is real. Grok,
  PROVEN.
- The plan's seam line numbers are stale after the hub-links merge:
  `CORE_API` is `modules.py:56`, `MAX_ROWS` is `:1042`, `NOTICES_PER_MODULE`
  is `:1140`, the notice drop is `:1350`. All three. Cosmetic, but a rev 4
  should cite by name, not line.
- In the fixture, `unapproved-1` has `box: null`, so §12.1's "known
  difference" test (unapproved with a live box) has no fixture row to run
  on; `naive-1` has `expires_unparseable: true`, which rev 3 calls
  `bad-charter` and full Corral does not; `revoked-1`'s `last_event` is
  `revoke 2026-08-16T000000Z`, a compact timestamp the converter must
  parse. Grok, PROVEN; Astra on the missing passing-signature rows.

## Converged (all three) → rev 4

1. **"Names it in `box:`" is undefined when `box:` is absent.** §4.1 says a
   missing or misspelled `box` falls back to `name`; §2.3 says only a
   grant that names the box in `box:` covers it. An implementer cannot
   tell whether the default is naming. Consequences each arm traced: a
   rename without an explicit `box:` covers the new name and abandons the
   old box; a misspelled `box` (`boxx:`, `bxo:`) is kept as "other" and
   silently binds the charter's own name. **Rev 4:** `box` is a required,
   signed key with no default (Grok's single change). A charter without
   it is `bad-charter`. The default was the last place a typo could
   change what a grant authorises.
2. **A ranch-only box can never reach `not-listed`.** The ranch converter
   never claims complete (correctly, from Phase 0), the 30-day clock
   starts only at `not-listed`, and `boxes.toml` can only retire a box
   that has a line in it. A terminated ranch box keeps its last level
   forever and the ledger never ages it out; the only exit is the
   unspecified ritual of adding a line and deleting it. **Rev 4:** every
   in-scope box needs an operator gone-record that is not tied to
   `boxes.toml` membership. Two mechanisms were proposed: Grok, a
   `cli.py gone <box>` that writes a dated, signed-when-signing-is-on
   retirement into config; Gemini, let the converter emit a per-box
   `not-listed` from `status.py`'s `box: null`. The author adopts Grok's
   and rejects Gemini's: `box: null` means the charter's box was not
   found by that lookup, not that the inventory was complete, and
   turning it into termination evidence is exactly the inference Phase 0
   showed to be unsafe. The gone-record is the operator's assertion,
   shown in Sources for 7 days like a `boxes.toml` removal.
3. **Tombstones are keyed by delegate name alone.** Two sources (ranch
   and local) with the same delegate name share one revocation
   high-water mark, so one's revocation poisons the other's grant. **Rev
   4:** tombstones are keyed by `<source-id>/<name>`; a local charter
   that shadows an inbox grant of the same name inherits the inbox
   tombstone only through the explicit "revocation from any source
   applies" rule, which stays.
4. **`issued` has no bounds.** Nothing requires `issued < expires` or
   `issued <= now`. A future-dated `issued` is `valid` now, outranks any
   tombstone, and reopens a revoked name. **Rev 4:** `issued > expires` is
   `bad-charter`; `issued` more than 10 minutes in the future (the inbox
   skew limit, §2.5) is `bad-charter`; `issued` is compared only against
   tombstones of the same `<source-id>/<name>`.
5. **`signature` in the inbox schema is `verified` in §4.3 and `failed`
   in §12.1**, and the other values are undefined. **Rev 4:** the enum is
   `verified | failed | absent | unverified`; anything else fails the
   source's file. The ranch map is `sig_ok` → `verified`, `signed and not
   sig_ok` → `failed`, `not signed` → `absent`. Only `verified` can make an
   inbox grant `valid`.

## Two of three → rev 4

- **`unknown` keeps "the level it had", not "the level it had when last
  listed", and ignores later grant changes** (Astra, Grok). An old `ok`
  can survive the grant expiring while the box is `unknown`; an old `bad`
  can survive a valid grant arriving. **Rev 4:** presence and coverage
  are recomputed every run; what the ledger carries over for an `unknown`
  box is its last **presence** (`listed` or `listed-stale`, with the
  time), and severity is then derived from current coverage as if the box
  were still listed, labelled "last listed <age> ago".
- **The ranch keys live boxes as `<name>-node` on Lightsail and `<name>`
  on GCP** (Grok; Astra on identity). A local charter with `box: cal-1`
  does not cover ranch `cal-1-node`, and automatic same-name joining
  across sources would merge two unrelated machines called `worker`.
  **Rev 4:** no automatic joining by name. A box is `<source-id>/<name>`;
  a grant's `box:` may list several qualified names (`box: [gcp/cal-1,
  lightsail/cal-1-node]`) and `aliases` in `boxes.toml` is dropped. A bare
  `box:` value with no source prefix covers the `declared` source only.
- **`ack-ledger` can empty the tombstones** (Astra, Grok). After a corrupt
  `tombstones.json` is moved aside and acknowledged, every revoked
  charter replays. **Rev 4:** a corrupt tombstone file is moved aside but
  its parseable lines are salvaged into the new file; `ack-ledger` records
  the acknowledgement in the data dir (it is the one CLI write there, and
  §2.4 says so) and does not touch tombstones; the Sources row then reads
  "tombstones rebuilt from a damaged file on <date>" for 30 days.
- **`ended:removed` disappeared from rev 3** (Grok; Astra in effect). The
  Grants table has no status for a deleted charter, while §12.1 still
  wants "A shown as ended". **Rev 4:** a grant id the ledger knows and no
  charter file supplies is listed as `ended:removed` in the Grants table
  for 30 days.
- **Phase 2 pins bind a box to one address** (Gemini, Grok; Astra on
  result age). A box whose address changes keeps being probed at the old
  pair, and a `key-matches` result can stay attached after a move. **Rev
  4:** a pin is `<box> <host>[:port]`, the operator changes it with the
  same `allow-probe` verb, and every outcome carries the address it was
  measured against; a result for an address no longer pinned is dropped,
  not shown. Gemini's "rotating cloud IPs cause false alarms" is not
  adopted as a defect: a `resolve-failed` is warn, and a hostname that
  resolves differently is the operator's record to update.
- **CRLF on disk is `bad-sig` unconditionally** (Astra; Grok in round
  two). A charter signed as CRLF bytes verifies. **Rev 4:** the §12.1 case
  becomes "CRLF on disk with an LF-signed file → `bad-sig`"; a CRLF file
  signed as CRLF is `valid`.

## One arm, adopted

- **macOS: the collector cannot spawn `ssh-keygen`** (Astra, PROVEN above).
  **Rev 4:** v1 verifies signatures on Linux only. On macOS the collector
  reports every signed charter `bad-sig: cannot verify (no subprocess in
  this sandbox)`, which is honest and not reassuring, and §11 gains a
  later phase: a core-provided verifier the module can call without
  forking.
- **`core_api <= CORE_API` needs a type** (Astra): a positive integer; a
  boolean or zero is refused.
- **Compact audit timestamps** (`2026-08-16T000000Z`) in `last_event`
  (Grok): the converter parses both forms.
- **Converter value types** (Grok, Gemini, Phase 0): `budget_usd` string
  passed through as text, `capabilities` raw text or null, `box` a dict
  whose `manifest_key` is the box name, `generated_at` with a colon-less
  offset.

## Not adopted, with the reason

- **`box: null` as a `not-listed` assertion** (Gemini's single change):
  see item 2.
- **A `cert-unsupported` warn "attacks" certificate-based sites**
  (Gemini): the plan does not claim certificate support in v2, and a warn
  that says so is correct until it does.
- **Alarms must never be lowerable by deleting a `boxes.toml` line**
  (Gemini): the operator is the authority on the operator's own hand
  inventory; §4.2 shows the removal for 7 days and the ledger keeps the
  history.
- **Q7 as a new card type** (Astra, Gemini): Grok's narrower fix is the
  one to take (below).

## Q2 and Q7

- **Q2:** all three, for the third round running, favour the separate
  core-mounted, collector-only inbox. The author recommends the operator
  settle it so rev 4 can drop the alternative text.
- **Q7:** all three want the core to stop silencing a failing module's
  alarm. Grok's shape is the smallest change: while a module's run state
  is `failing`, the rail keeps serving the `bad` notices from its last
  good snapshot until `NOTICE_MAX_AGE_S`, with a "last good <age> ago"
  suffix. No new card type. It is a seam change that helps every module;
  the author recommends it for Phase 1.

## Tests rev 4 must add (§12)

1. Binding: a charter without `box:` is `bad-charter`; a rename with the
   same `box:` keeps coverage; `box: gcp/cal-1` does not cover
   `lightsail/cal-1`; two machines named `worker` under two sources are
   two boxes.
2. Exit: a ranch-only box that leaves the ranch output stays at its last
   presence and level; `cli.py gone lightsail/cal-1-node` moves it to
   `not-listed` and starts the 30-day clock; a tombstone survives a
   damaged `tombstones.json` plus `ack-ledger`.
3. Time and identity: `issued > expires` and `issued` 11 minutes ahead are
   `bad-charter`; a future `issued` does not outrank a tombstone;
   same-name delegates from two sources have separate tombstones;
   `signature: absent` and an unknown string; `naive-1` from the fixture
   is `bad-charter` and asserted as a known difference next to
   `unapproved-1`.
4. Seam: on macOS, every signed charter reads "cannot verify" and the
   board says why; with the module `failing`, its last `bad` notice is
   still on the rail after two cadences (if Q7 is adopted).

## Verdict for the operator

Three AMENDs with one shared root: rev 3 fixed the alarm's logic but left
its **identity** rules (which box a grant names, which source a name
belongs to, how a box ever leaves) to the implementer. Rev 4 is a
contained change to §2.2 to §2.4, §4.1 to §4.3 and §12, and does not
reopen the model. The author's recommendation: write rev 4 from this
synthesis, then **build Phase 1** without a fourth round; the remaining
risk is in the implementation, which §12 can catch.
