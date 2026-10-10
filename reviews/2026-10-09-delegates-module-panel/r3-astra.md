I’ll read rev 3, the round-two findings, and the Phase 0 evidence, then check the plan’s claims against the code. I’ll stay read-only and won’t run tests or use the network.The box model fixes the basic reuse case, but identity and lifecycle rules still conflict: defaults can implicitly name a box, tombstones use unqualified delegate names, and the ranch converter needs fields absent from the measured JSON. I’ve also confirmed the code’s API equality check, CLI mount behavior, and notice dismissal key; I’m checking alarm expiry next.**AMEND — PROVEN: Phase 1 cannot implement the ranch converter as written: it requires `created` and charter-byte identity absent from its specified JSON input (plan:89,339–345; fixture computation below).**

**2. ROUND TWO**

References `P:n` mean `docs/delegates-module-plan.md:n`. FIXED describes the specification, not built behavior.

*Converged:*

- **Reuse/rename — PARTLY, PROVEN:** box-level coverage resolves explicit reassignment, but default `box` and cross-source identity remain ambiguous (P:108–123,166–168,244).
- **Unknown downgrades alarms — PARTLY, PROVEN:** retaining the last listed level fixes the original case, but conflicts with subsequent coverage changes and probe escalation (P:134,142–147,403–405).
- **Edit-distance rejects ordinary keys — FIXED, PROVEN:** removed; required fields and grammar replace it (P:229–250).

*Two of three:*

- **Revocation ordering — PARTLY, PROVEN:** `issued` exists, but name collisions, ranch compatibility, and persistent source revocations remain unresolved (P:96,163–165,240–242,329–345).
- **Replay protection retention — FIXED for revocations, PROVEN:** permanent tombstones (P:163–165); expiry replay remains weaker.
- **Completeness — PARTLY, PROVEN:** per-file permission and truncation rules are explicit; which boxes a complete source “covers” remains unspecified (P:116,320–323).
- **Shadowed revocations — FIXED narrowly, PROVEN:** revocations survive shadowing; unconditional application now conflicts with renewal (P:329–331).
- **Inbox approval evidence — PARTLY, PROVEN:** approval and verification gate validity, but signature enum and invalid-value handling are incomplete (P:102–104,314–328,572).
- **Ranch approval markers — FIXED, PROVEN:** `>` and `|` are drafts (P:99).
- **Probe key comparison — PARTLY, PROVEN:** blobs and ports are specified; certificate detection and multiple-key precedence are not (P:375–389).
- **Module-controlled probe targets — FIXED, PROVEN:** core-side pins authorize targets (P:365–368).

**3. NEW DEFECTS / REMAINING CONTRACT GAPS**

- **Box binding — PROVEN:** “names it in `box:`” coexists with absent/misspelled `box` defaulting to `name` (P:121–123,244–245). State explicitly whether that default authorizes coverage. Renaming without explicit binding then targets the new name, not the old box. A misspelling can authorize the unintended same-name box.

- **Identity and aliases — PROVEN:** source-qualified identity is followed by automatic same-name joining through a grant; aliases are merely “names” (P:108–110,287–289). No qualification, collision, or alias-removal rules exist. **SUSPECTED:** two unrelated machines named `worker` could share authorization, revocation, or retirement evidence. One machine observed under two names also needs stable identity across alias edits.

- **Unknown and ledger-only boxes — PROVEN:** never-listed unknown boxes get `warn`; previously listed ones inherit their old level regardless of current coverage (P:134). Thus an old `ok` can survive grant expiry, while an old `bad` can survive a valid replacement, contrary to the continuity prose. The ledger stores “last level,” not specifically “last listed level” (P:156–157). Ledger-only scope is promised, but the observations and alias history required to retain its identity are not specified (P:125–126).

- **Retirement — PROVEN:** deleting a `boxes.toml` entry explicitly lowers an alarm; this is now an operator assertion, not independent termination evidence (P:290–293). Define its authority over aliased sources: a fresh positive listing and complete omission both satisfy presence rules (P:114–116). Ranch-only retirement cannot start the 30-day clock; Phase 1 needs manual declaration/removal, with retained alias coverage, or another permitted complete source (P:161,340–341). “Only” `boxes.toml` can clear also contradicts permitted complete inbox sources (P:146–147,320–323).

- **Tombstones and renewal — PROVEN:** tombstones use unqualified delegate names, so unrelated same-name charters across sources collide (P:163). Meanwhile “revocation from any source” and “earlier expires … always apply” have no issuance boundary: an old source record can permanently defeat a newer renewal (P:96,329–331).

- **Expiry replay and acknowledgement — PROVEN:** expiry latches are per byte-hash grant ID, but no high-water `issued` record enforces the claimed newer-issuance requirement (P:156–160). Changed signed bytes with unchanged issuance escape that particular latch. `ack-ledger` acknowledges lost history without restoring tombstones or defining what alarms must remain; its write protocol also conflicts with “only the collector writes” (P:151–154).

- **Time validity — PROVEN:** validity lacks `issued <= now < expires` and `issued < expires` requirements (P:94–100,175–185). A future-issued grant can therefore qualify immediately; a far-future revocation can obstruct renewal. Reject reversed intervals and define future-issuance handling.

- **Inbox identity — PROVEN:** inbox grants can cover boxes because they can be `valid`; this is source-attested authorization (P:102–104,121). The schema supplies neither charter bytes nor their hash, despite the universal byte-hash grant ID requirement (P:89,314–317). Specify `verified | absent | failed`, strict types, missing/unknown handling, and stale-grant behavior; `signature: failed` is tested but not formally defined.

- **Probe and API — PROVEN:** pins bind box to endpoint, but endpoint replacement, removal, DNS refresh, and result-age invalidation are unspecified (P:365–397). **SUSPECTED:** an old endpoint’s matching result could remain attached after a box moves. Certificate classification lacks an explicit acquisition path; multiple scanned keys need match/mismatch/revoked precedence. “Any `core_api <= CORE_API`” needs a positive-integer supported range, excluding booleans and zero (P:399–402).

- **Q7 — PROVEN, code checked:** failed collection preserves old `fresh_at`; notices disappear at twice the interval (`modules.py:1279–1283,1350`). Making continuity optional leaves the central alarm guarantee incomplete.

**Code claims independently checked — PROVEN:** current API validation uses equality (`modules.py:297`); fetched data is mounted before the interactive branch, while CLI config is writable (`modules.py:886–900`); dismissal includes module, ID, level, and title (`static/app.js:3249–3250`). The plan’s cited line numbers have drifted.

**4. PHASE 0**

- **PROVEN:** both highlighted measurements are incorporated correctly: module-side refusal of missing `namespaces=` and a permanently partial ranch converter (P:274–279,340–341). However, “five negative cases failing” misstates the successful no-namespace verification (P:517; `docs/delegates-phase0.md:159–164`).
- **PROVEN, reproduced read-only computation:** all 16 fixture rows lack `created`, `issued`, `revoked_at`, and `grant_id`. The converter needs additional charter/audit inputs or an upstream schema extension. Local ranch charters also have `created`, not required `issued`; mapping must preserve the signed original bytes (`docs/delegates-phase0.md:104–113`).
- **PROVEN:** the fixture has no passing LIVE/READY/UNAPPROVED signature cases; parity claims need additional fixtures (`docs/delegates-phase0.md:46–51,194–197`). Its revoked event uses `2026-08-16T000000Z`, requiring explicit audit timestamp parsing.
- **PROVEN:** unconditional CRLF rejection in §12 conflicts with exact-byte signature verification: correctly signed CRLF can be valid (P:230–231,590).
- **PROVEN:** the public seam’s macOS sandbox denies subprocess creation, conflicting with collector-launched `ssh-keygen`; declare Linux-only support or design a supported verifier path (`module_sandbox.py:194–200`).

**5. RECOMMENDATIONS**

**Q2:** Adopt the separate collector-only inbox; the checked CLI mount behavior supports this separation.

**Q7:** Require a core-owned unresolved-stale-alarm card in Phase 1, with explicit recovery and dismissal behavior.

**6. THREE MOST IMPORTANT TEST GAPS**

1. **PROVEN gap (P:527–568):** identity/state sequences covering implicit binding, same-name different-source boxes, alias deletion, ledger-only scope, and expiry while unknown.
2. **PROVEN gap (P:553–558):** renewal versus persistent old revocations, cross-source name collisions, future/reversed issuance, changed-byte expiry replay, and tombstone corruption followed by acknowledgement.
3. **PROVEN gap (P:601–615):** measured converter input through validated snapshot and rail, including missing issuance/hash inputs, passing signatures, collector failure beyond notice expiry, and recovery.

**7. SINGLE CHANGE**

Define one authoritative, source-qualified identity and lifecycle contract covering grant binding, issuance ordering, retirement authority, and persisted alarm transitions before building Phase 1.