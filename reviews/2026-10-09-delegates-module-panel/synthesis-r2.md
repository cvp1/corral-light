# Delegates module plan, rev 2: panel synthesis, round two (2026-10-09)

Arms, cold, read-only clone of `delegates-plan` at 2995341, given the
round-one record:

| Arm | Model | Verdict | Wall | File |
|---|---|---|---|---|
| Codex | gpt-6-astra (read-only mode) | AMEND | 82 s | r2-astra.md |
| Grok | grok-4.7, effort high | AMEND | 979 s | r2-grok.md |
| Gemini | gemini-pro-agent | AMEND (was REJECT) | 138 s | r2-gemini.md |

Round-one items all three call FIXED: `setup` pastes a key; lanes are
core; no gpg; `"notices": true`; the signature flags and cache key.
Each of the others is FIXED or PARTLY in at least one arm. What is left
is below.

Line numbers: `MAX_ROWS` is `modules.py:1004`, not 1003 as rev 2 said (Grok,
Gemini, Astra).

## Converged (all three) → rev 3

1. **A reused or renamed box raises an alarm forever.** Delegate A ends.
   Delegate B, valid, uses the same box, which stays listed. A's ledger row
   stays `bad`, because it closes only when the box is gone. A rename is
   the same case. Rev 3 makes the **box** the unit of the alarm: a listed
   box is covered when at least one valid grant **names it**
   (`box:`). An ended grant's association is then closed and recorded as
   "superseded by B". A grant that does not name the box never covers it
   (Astra's condition: an unrelated grant is not authorization).
2. **A non-valid grant on an `unknown` box was `warn`.** That is the
   deletion bug under a new label. Gemini's version: delete the box from
   `boxes.toml`, or break the source, and the alarm drops to warn.
   Rev 3: a box that was last observed listed keeps its last level until
   a fresh complete source reports it `not-listed`. `unknown` can keep an
   alarm, never quiet one.
3. **The edit-distance rule rejects ordinary keys.** `note`, `date`,
   `lane`, `job`, `log`, `os`, `boxes` and `bot` all fall within 2 of a
   security key. Rev 3 drops the rule and makes the security keys
   **required** instead: no `expires` is `bad-charter`, and neither
   `status` nor `approval` is `bad-charter`. A misspelling then shows up
   as the key being missing. A key line that fails the key grammar (for
   example `Status:`) is `bad-charter`.

## Two of three → rev 3

- **Revocation ordering has no field to order by** (Astra, Grok). Rev 3
  makes `issued` (RFC 3339) a required, signed key. "Newer" means a greater
  `issued`. The ledger keeps the highest revoked `issued` per delegate. A
  restored file with an older or equal `issued` stays revoked.
- **Retention throws away replay protection** (Astra; Grok fixes it at 30
  days). Q6: association history is kept for a fixed 30 days.
  **Revocation tombstones are kept forever**, in their own small file.
  Neither can be configured (Grok: zero would be a switch that quiets the
  alarm). Gemini wanted it configurable down to zero, which conflicts with
  fix 2; not adopted.
- **"Complete" is undefined and the ranch converter misuses it** (Astra,
  Grok, Gemini on `boxes.toml`). `status.py` lists charters, not boxes, so
  a successful run is not a complete inventory. Rev 3:
  - `complete` is a per-file field the source writes. It is honoured only
    when `config.toml` also allows that source to claim it.
  - A file cut at any limit is `partial`.
  - The ranch converter never claims `complete`.
  - `boxes.toml` is complete: deleting a line is the operator saying the
    box is gone. Sources shows "removed from boxes.toml by hand" for 7
    days.
- **Inbox revocations were lost under shadowing** (Astra, Grok). A
  revocation or earlier `expires` from any source always applies, even
  when a local charter shadows that grant.
- **Inbox grants had no approval evidence** (Astra; Grok in effect). The
  schema gains `approved` and `signature` (`verified | absent | failed`),
  both labelled as the source's claims. An inbox grant is `valid` only
  when the source says it is approved and verified. The row then reads
  "valid per <source>".
- **Ranch markers `>` and `|`** are missing from the unapproved list
  (Astra, Grok).
- **The Phase 2 probe never compares keys** (Grok; Astra on
  certificates; Gemini on `@cert-authority`). `ssh-keygen -F` only looks a
  name up. Rev 3:
  - It compares the scanned key blobs with the keys `-F` returns.
  - It looks up `[host]:port` for non-standard ports.
  - Certificates are out of v2: a box presenting one is
    `cert-unsupported` (warn).
  - Mismatch and revoked are `bad`; unknown and no-answer are `warn`.
  - A target unfinished at the deadline is `not-checked`, not
    `no-answer`.
- **Probe targets come from module-writable config** (Astra, Grok). Rev 3:
  targets need a core-side allow (`corral-light module allow-probe
  delegates <host:port>`), stored in the core's pins, not in the module's
  config.

## One arm, adopted

- **`core_api` 2 would refuse every current module** (Grok,
  `modules.py:55, 295-297`). Rev 3: the core accepts any `core_api` up to
  its own, and `core_reports` is additive.
- **"The same way as `fetched`" is wrong** (Grok). `fetched` is added to
  the read-only list before the interactive branch, so the CLI sees it
  too (`modules.py:882-891`). The inbox must be left out of the
  interactive argv explicitly.
- **`unknown (done)` is not a snapshot level** (Grok): use `info`.
- **Grant values are not exclusive** (Grok). Rev 3 sets a precedence:
  `bad-charter` > `bad-sig` > `ended:revoked` > `ended:expired` >
  `ended:removed` > `unsigned` > `draft` > `valid`.
- **A delegate with several ledger boxes** (Grok): the table has one row
  per (delegate, box) pair.
- **A fresh complete omission beats a stale listing** (Grok). The stale
  listing is kept as a note.
- **The clock running backwards revives an expired grant** (Astra). The
  ledger records `ended_at` the first time it sees a grant end. Only a
  greater `issued` reopens it.
- **More than 200 alarm rows** (Astra): alarm rows fill blocks in order.
- **A grant id** (Astra, Grok partly): the SHA-256 of the charter bytes,
  shown shortened.
- **Probe caps** (Grok): the caps allow 40 s of work in a 30 s budget.
  Rev 3: 8 at a time, `-T 3`, 30 s.

## New open question (Grok, `modules.py:1236, 1314`)

When a collector fails, `fresh_at` does not advance, and the module's
notices expire at `fresh_at + 2 × every_s`. **A broken Delegates collector
drops its own alarm from the rail.** That is how the seam behaves today
for every module. Rev 3 asks the operator (Q7) whether the core should
keep one "module stale; its last alarm is unresolved" card for a module
whose last good snapshot carried a `bad` notice. That would be a seam
change.

## Q2, round two

All three now favour the separate core-mounted inbox, including Astra,
who argued for the config folder in round one. All three also note that
it is not isolation from same-user processes, and that the plan must say
so plainly (Astra). The decision is still the operator's.
