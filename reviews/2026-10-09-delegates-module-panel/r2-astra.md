I’ll read rev 2 in full, then the round-one synthesis, and check its code claims against the local seam. I’ll keep the review read-only and offline.The ledger addresses deletion, but rev 2 leaves renewal and box reuse unresolved. I also found a completeness mismatch: the ranch report enumerates charters, while the converter proposes treating it as complete box inventory. I’m checking the resulting alarm and retention edge cases.AMEND — **PROVEN:** the proposed “complete” ranch source can omit boxes when charters disappear, undermining the ledger’s central deletion guarantee (`docs/delegates-phase0.md:12`; `docs/delegates-module-plan.md:337`).

**1. ROUND ONE**

References below use **P** for `docs/delegates-module-plan.md`. “FIXED” means fixed in the specification, not demonstrated by implementation.

| Synthesis item | Assessment |
|---|---|
| Converged: deletion/tier change must preserve alarms | **PARTLY — PROVEN:** ledger and tier retention specified, but completeness and lifecycle defects remain (P:162, P:496; below). |
| Converged: setup cannot see SSH files | **FIXED — PROVEN:** pasted key replaces discovery (P §6). Independently checked writable CLI config at `modules.py:886` and isolated HOME at `module_sandbox.py:154`. |
| Converged: Phase 2 sandbox | **PARTLY — PROVEN:** dedicated profile and explicit endpoints specified; certificate verification and nonstandard-port lookup remain underspecified (P:358–372). |
| Converged: lanes belong in core | **FIXED — PROVEN:** core checks grants before opening and every send (P §8). |
| Converged: no gpg | **FIXED — PROVEN:** OpenSSH verification specified; namespace confirmation remains explicitly pending (P:252, P:522). |
| Converged: stale/absent data is not termination evidence | **PARTLY — PROVEN:** intended rule exists, but mixed-source coverage and failed-cache states are inconsistent (P:118–121, P:335). |
| Two-of-three: recompute inbox grants locally | **PARTLY — PROVEN:** expiry recomputed, but approval/signature evidence is missing from the schema (P:314–323). |
| Two-of-three: separate axes | **PARTLY — PROVEN:** axes exist; severity ignores source health when any fresh source lists the box (P:123–138). |
| Two-of-three: stable identifiers | **NOT FIXED — PROVEN:** delegate identity remains its renameable name; box identity remains source/name; no distinct grant identity exists (P:154–175). |
| Two-of-three: 200-row cap | **PARTLY — PROVEN:** splitting fixes ordinary overflow, but “every alarm row” cannot fit the first block when there are 201 alarms (P:398–404). Independently checked `MAX_ROWS = 200` at `modules.py:1004` and text-only cells at `static/app.js:3986`. |
| Two-of-three: notices opt-in | **FIXED — PROVEN:** manifest includes it (P §6); independently checked enforcement at `modules.py:1232`. |
| Two-of-three: signature arguments/cache/same bytes | **FIXED — PROVEN:** all specified (P:252–273); actual sandbox verification remains Phase 0. |
| Two-of-three: charter grammar | **PARTLY — PROVEN:** comments, duplicates, bytes and names addressed; edit-distance and approval-marker defects remain (below). |
| Two-of-three: sibling `core_reports` | **FIXED — PROVEN:** explicit at API 2 (P:381). |

**2. NEW DEFECTS / REMAINING REV 2 GAPS**

- **PROVEN — axes are not exhaustive.** If source A is fresh/complete and omits a box, while covering source B fails without usable history, neither `not-listed` (“every source”) nor `unknown` (“no fresh complete source”) applies. A retained failed observation is neither explicitly fresh nor stale. The continuity tests demand `bad`, but `unknown` produces `warn` (P:118–138, P:335, P:536–549). Specify precedence and cached-positive handling.

- **PROVEN — source health does not actually participate in severity.** `valid × listed` is `ok` even with another relevant source failed/partial. A backwards clock can also resurrect an expired grant while adding only a clock warning; no monotonic expiry state is recorded (P:130, P:163–165, P:185–189). Decide whether the grant remains ended or becomes uncertain.

- **PROVEN — legitimate reuse and rename alarm indefinitely.** Delegate A ends; a new valid delegate B uses the same continuously listed box. A remains `bad`, despite B’s grant, because A’s association cannot retire until physical absence. Renaming produces exactly this situation (P:154–175). Shared boxes make physical disappearance an unsuitable universal closure condition. Define signed supersession or explicit retirement of the old association without treating any unrelated grant as authorization.

- **PROVEN — revocation ordering lacks its required data.** The ledger compares approval dates, but the example has only `status: approved`; `approval` accepts arbitrary nonempty text, and signed revocation has no required timestamp or sequence (P:173–175, P:218–227, P:241–250, P:280). “Newer signed charter” is not implementable deterministically as specified.

- **PROVEN — retention discards replay protection.** After 30 days of absence, deleting the entry deletes its revocation high-water mark. Restoring an older, still-in-date signed approval can then validate again (P:162–175). Conversely, a retired source that never supplies fresh absence can retain associations forever. Keep compact revocation tombstones separately and specify source retirement, ledger corruption and atomic persistence.

- **PROVEN — edit distance rejects legitimate extensions.** Under P:246–248, `role` is distance 2 from `name` (`name → rame → role`), and `job` is distance 2 from `box` (`box → jox → job`). Both become fatal. Reserve an extension namespace instead. Also, rev 2 omits ranch’s unapproved markers `>` and `|`; its bare-string/nonempty rule can approve them (P:105, P:237, P:242; `docs/delegates-phase0.md:49`).

- **PROVEN — inbox grants cannot establish validity.** Expiry and revocation alone cannot distinguish signed approval from draft or bad signature. `verified_by` is merely display text, and the schema carries no approval evidence (P:104–107, P:314–323). Local shadowing also needs an explicit rule preserving inbox revocations rather than suppressing them (P:324).

- **PROVEN — completeness is neither scoped nor per-snapshot.** It lives in config, yet the converter supposedly changes it according to `gcp_visible`, and the example has no completeness field (P:301–340). More seriously, Phase 0 says `collect()` enumerates charter files: successful GCP visibility does not make its output exhaustive inventory. Deleting a charter can remove its box row. “Complete for the boxes it lists” likewise does not define coverage after a declared row is deleted (P:289). Require explicit coverage and per-snapshot success, including pagination/partial failures.

- **SUSPECTED — Phase 2 promises more than its command sequence establishes.** `ssh-keygen -F` is a lookup, not certificate validation; the plan provides no certificate-request/validation procedure or `[host]:port` lookup construction (P:368–372). Specify certificate principal, lifetime, CA and revocation checks, and precedence across multiple keys. The core also accepts targets from CLI-writable config, so the approved target boundary must be explicit before granting private-network access (`modules.py:887–891`; P:358–367).

**3. Q2**

Choose the separate core-mounted inbox for operational separation. **PROVEN:** excluding it from the CLI mount narrows that sandbox’s writable surface (`modules.py:887–891`; P:294–299). However, its claimed protection against an ordinary same-user converter is false: directory location and mode 0700 do not isolate processes sharing the owner UID. Document the benefit accurately; actual converter isolation requires its own restricted execution environment.

**4. Q6**

Keep a fixed minimum of 30 days for resolved observation history, allow longer retention, and retain compact revocation tombstones indefinitely; **PROVEN:** the current combined deletion rule loses replay protection (P:162–175).

**5. THREE MOST IMPORTANT TEST GAPS**

1. **Lifecycle sequences:** rename, signed supersession on a continuously listed shared box, source retirement, retention expiry, then replay. **PROVEN:** existing continuity tests omit these (P:536–544).
2. **Completeness contract:** charter-filtered ranch output with `gcp_visible=true`, partial successful refresh, and mixed fresh/failed covering sources. Assert that none falsely proves termination. **PROVEN:** current tests cover individual source categories, not these combinations (P:547–557).
3. **Actual approval evidence:** inbox draft/bad-signature grants with future expiry, `approval: >`/`|`, and revocation followed by dated renewal. **PROVEN:** current inbox and grammar tests do not establish these distinctions (P:555; P §12.1).

**6. SINGLE CHANGE**

Define a grant-and-association lifecycle with immutable grant identity, signed supersession, explicit source coverage and durable revocation tombstones before implementing the severity table.