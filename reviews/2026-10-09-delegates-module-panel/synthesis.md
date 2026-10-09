# Delegates module plan, rev 1: panel synthesis (2026-10-09)

Arms, cold, read-only clone of `delegates-plan` at c2fd8e0, via `corral-light consult`:

| Arm | Model | Verdict | Wall | File |
|---|---|---|---|---|
| Codex | gpt-6-astra (read-only mode) | AMEND | 108 s | r1-astra.md |
| Grok | grok-4.6, effort high (lane default; FinOps round two had 4.7) | AMEND | 430 s | r1-grok.md |
| Gemini | gemini-pro-agent | REJECT | 114 s | r1-gemini.md |

The author re-checked these against the code before adopting them:
`MAX_ROWS = 200` (`modules.py:1003`); notices are dropped unless the
manifest sets `"notices": true` (`modules.py:310-312`); the runner enforces
`timeout_s`, and `budget_s` is only a value handed to the module
(`modules.py:355-360, 1224`); notice dismissal is keyed on
module, id, level and title, not the body (`static/app.js:3250`).

## Converged (all three) → adopt in rev 2

1. **Deleting an expired charter downgrades the alarm.** `OVERDUE-REVOKE`
   (bad) becomes `ORPHAN` (warn), and the Q5 tier filter could hide it
   entirely. The plan's "deletion cannot hide a box" test asserts the
   downgrade. Rev 2: a **grant ledger** in the module's data dir records
   every grant-to-box association it has seen. A box once tied to a grant
   stays `OVERDUE-REVOKE` until the box leaves inventory, as reported by a
   fresh source. Deleting or editing the charter does not clear it. The
   test asserts severity and the retained history, not the label.
2. **`setup` cannot see `~/.ssh`.** The CLI runs sandboxed, with `HOME`
   set to the data dir (`module_sandbox.py`, `modules.py:886-896`). §6 was
   wrong. Rev 2: the operator pastes a principal and public key, which
   `setup` writes to `<config>/allowed_signers`. Gemini instead wants a
   core `ssh-pubkeys` read; **not adopted**, since pasting a public key
   needs no new capability.
3. **Phase 2's sandbox does not exist as described.** The report profile
   has no network, and `review_egress.py` allows only CONNECT on port 443
   and rejects private addresses. Comparing against `known_hosts` must
   handle hashed entries (`ssh-keygen -F`), `HostName` and
   `HostKeyAlias`, non-standard ports and certificate authorities. Rev 2:
   Phase 2 gets its own sandbox profile and an explicit endpoint for each
   box in config, rather than evaluating `ssh_config`. It is still not in
   v1.
4. **Lanes are core.** A `delegate:` lane type sits beside `host:` and
   takes its list from the module's inventory. `OVERDUE-REVOKE` (and a box
   with no valid grant) blocks opening one. A module snapshot never
   authorizes sending anything.
5. **Q4: no gpg in v1.** Inbox `verified_by` is enough for the ranch.
6. **Stale or absent data is not evidence.** A box missing from a stale
   or failed source has not been shown to be terminated. A box present
   only in a stale source is labelled stale; it is not shown as LIVE.

## Two of three → adopt

- **Recompute every inbox row on this host's clock** (Astra, Grok). An
  inbox's `state` and `verified_by` are the source's assertions: show them
  as "source reports …", and work out expiry here from `expires` and
  `status`. A converter that has stopped can no longer show `LIVE` forever.
- **Replace the single state with separate axes** (Astra; Grok in effect):
  grant validity, box observation and source health, with severity
  derived from all three. A box listed with no currently valid grant is
  the alarm, whatever the reason: expired, revoked, draft, unsigned or
  bad signature. Plain-language labels: "granted; box listed", "grant
  ended; box still listed", "grant ended; box not seen".
- **Stable identifiers** (Astra, Grok; Gemini: one host can carry many
  delegates). Separate delegate, grant and provider-scoped box ids from
  display names. Several delegates may share one box.
- **The 200-row table cap** (Astra, Grok). Sort alarm rows first and split
  across blocks. Table cells take no `level`, so severity goes in the
  text and in the tiles. Test that row 201, an overdue one, is still
  visible.
- **`"notices": true`** belongs in the manifest (Astra, Grok).
- **Signature verification**: name `-I <principal>`, `-n` and `-s`
  explicitly, and key the cache on the `allowed_signers` digest too, so
  removing a key invalidates it (Astra, Grok). Verify and parse the same
  bytes.
- **Charter grammar** (Astra, Grok): define comments, CRLF, duplicate keys,
  a name charset matching the filename, and a refusal of misspelled
  security keys rather than keeping them as "other".
- **Q1, `core_reports`** as a sibling manifest key at `core_api` 2 (Grok,
  Gemini; Astra would only document it). Adopt. It is decided properly
  when Phase 2 is built.

## One arm, adopted

- **The aggregate notice cannot reappear after "Not now"** (Astra). Put an
  incident generation (a hash of the overdue set) in the notice id.
- **`budget_s` is not enforced** (Astra). Say `timeout_s` is the deadline.
- **`CORRAL_MODULE_CONFIG` is the file**; the folder is its dirname
  (Grok).
- **`modules/index.json` is a one-line core edit**: "index entry only",
  not "none" (Grok).
- **`require_signature` defaults to `yes` once `allowed_signers` exists**
  (Grok). Unsigned, anyone who can write the config can push `expires`
  out.
- **Replay** (Astra): the fixed namespace does not stop an older signed
  approval from being restored after a revocation. The ledger (item 1)
  remembers the highest-dated revocation seen for each delegate.

## Split → the operator decides

- **Q2, where the inbox lives.** Grok and Gemini: a separate
  `<state>/module-inbox/<name>/`, mounted read-only by the core, so a
  converter cannot rewrite `expires` or `allowed_signers`. Astra: keep it
  in config, because the same-user writer is trusted either way and a
  separate path is organisation, not a security boundary. Author's view:
  the separate dir. The cost is a small core bind, and the plan stops
  claiming "no core change". The gain is that tool output and grant text
  no longer share a folder, which also stops the CLI's writable-config
  mount from covering the inbox.
- **Q5, `ORPHAN`.** Grok: only boxes marked `tier: delegate`, with `tier`
  added to `boxes.toml` and the inbox schema. Gemini: always warn, plus
  the ledger. With item 1 the two agree: a previously chartered box never
  becomes a benign orphan, and other untiered servers are Fleet's.

## Not adopted

- Gemini's REJECT rests on §6's `~/.ssh` claim (fixed by item 2) and the
  deletion downgrade (item 1). With both fixed, its remaining points are
  AMEND-level.
- Container-fleet accounting beyond letting several delegates share one box.
