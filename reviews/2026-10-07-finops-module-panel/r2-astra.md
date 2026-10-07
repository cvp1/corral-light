I’ll read rev 3 and the round-one record, then check its new code claims against the implementation. I’ll keep this read-only and stay within the clone.The Claude adapter confirms the overwrite problem, but it also drops rate-limit events before usage is available; the proposed hub fix cannot recover those. The sandbox code also needs a distinct collector profile: today, omitting the egress proxy shares the host network.**AMEND — PROVEN:** the Claude quota schema omits supported windows and optional fields, while Grok’s fallback dedupe can silently discard distinct usage; both undermine trustworthy automated figures.

**2. ROUND-ONE CLOSURE**

I’m continuing the Astra seat. Below, `P` means `docs/finops-module-plan.md`; CLOSED means resolved **in the design**, pending implementation.

- **CLOSED — PROVEN:** whole-state access replaced by an allowlisted feed/sandbox and explicit unsandboxed acknowledgement (`P:112`).
- **CLOSED — PROVEN:** pins correctly described as integrity controls; staged generations, comprehensive digests and execution verification specified (`P:136,341`).
- **CLOSED — PROVEN:** macOS private-directory assumption corrected; Keychain discovery assigned to core and Phase 0 (`P:70,234,545`; `sessions.py:548`).
- **CLOSED — PROVEN:** declarative rendering retained; pane-login quota requests dropped (`P:148,552`).
- **CLOSED — PROVEN:** JSON manifest and integer/string operator configuration resolve parser incompatibility (`P:156,389`; `corral_core/tomlmini.py:55,77`).
- **CLOSED — PROVEN:** resumable 30-second batches resolve the initial-scan/timeout contradiction (`P:171,450`).
- **CLOSED — PROVEN:** environment built from nothing (`P:334`).
- **CLOSED — PROVEN:** “value per $” replaced with API-equivalent list cost; invalid Coverage classification replaced with Sources (`P:438,441`).
- **STILL OPEN — PROVEN:** source/account separation and resets are specified, but historical account attribution remains underspecified; a current fingerprint cannot identify older records automatically (`P:379,467`).
- **STILL OPEN — PROVEN:** event index and transactional replacement are adopted, but replay invariance contradicts ignoring out-of-order Codex observations (`P:459,467,495`). Sorting/reconciliation must precede delta attribution.
- **CLOSED — PROVEN:** staged updates, serialization and generation rollback specified (`P:151,353`). **STILL OPEN — PROVEN:** ledger migration rollback remains unspecified.
- **CLOSED — PROVEN:** unauthenticated health exposes counts only (`P:286`); notices deferred with bounds/expiry (`P:154`); attribution target now matches collector cadence (`P:679`).

**PROVEN:** the synthesis did not unfairly reject any Astra claim. It correctly distinguished my persisted-`turn_end` finding from the other reviewers’ “never on disk” claims (`reviews/2026-10-07-finops-module-panel/synthesis.md`, “Reviewer claims that were wrong”; `sessions.py:1808`).

**3. SOURCES OF TRUTH**

**PROVEN:** the hierarchy compares unlike quantities (`P:86`). Local session cost, organizational billed spend, subscription commitment and quota require separate metric identities. For matching account, scope, currency and period, billed cost should control *billed spend*; local vendor cost must not suppress it merely because it ranks first. Likewise, partial local coverage must not outrank complete billing coverage.

**SUSPECTED:** Grok’s number is appropriately labelled vendor-calculated cost, but calling it authoritative payment would be unjustified. The supplied survey establishes a vendor interface, not invoice equivalence. Usage totals, prepaid balances and billing exports also should not universally become `billed` without endpoint-specific semantics (`P:514,529`).

**PROVEN — code check 1:** the overwrite is real: `sessions.py:1404` replaces `self.usage`; `sessions.py:1808` persists the latest value. The adapter forwards `message.rate_limit_info` unchanged under the stated metadata key, then can emit ordinary usage without that key. Evidence: `spike/node_modules/@agentclientprotocol/claude-agent-acp/dist/acp-agent.js:4919,4102`. Separate storage is correct.

**PROVEN:** that fix is incomplete. The adapter forwards rate limits only when `lastAssistantTotalUsage !== null` (`acp-agent.js:4920` at the path above). Hub-only capture cannot recover suppressed events.

**PROVEN:** the bundled SDK declares optional `rateLimitType` and `resetsAt`, additional `seven_day_opus`, `seven_day_sonnet`, `seven_day_overage_included` windows, and separate overage fields. Evidence: `spike/node_modules/@agentclientprotocol/claude-agent-acp/node_modules/@anthropic-ai/claude-agent-sdk/sdk.d.ts:5606`. Rev 3’s three-window schema loses information. Preserve validated vendor fields, handle missing types explicitly, and do not claim generic cross-lane support from a Claude-specific key.

**PROVEN:** expiry must compare **current time** against reset time; “payload older than its own reset” is ambiguous (`P:482`). **SUSPECTED:** reset-only freshness is insufficient: an old weekly observation can mislead before reset. Add observation age, missing-reset handling, account-change invalidation and account-scoped quota storage; current `quota.json` is merely lane-scoped (`P:231`). Persist observations on arrival so interrupted turns and restarts retain them.

**4. GROK TOOL BINDING**

**PROVEN — code check 2:** existing sandbox reuse needs substantive changes. `review_sandbox.py:229` adds `--share-net` when no egress proxy exists; line 231 mounts the host root read-only. Neither implements the proposed offline allowlist profile.

**PROVEN:** HOME reduction is not per-tool confinement. A binary launched inside the collector’s sandbox can access every collector mount and writable directory. Furthermore, exposing the executable while requiring a wrapper does not prevent direct execution with other arguments (`P:192`). Use a separate broker/tool sandbox if argv restriction is an enforced capability; otherwise describe the wrapper as input validation.

**SUSPECTED:** the binary can also exhaust memory/processes/disk, inspect other usage sources, or place transcript content into outputs. Specify runtime dependencies, resource limits and process cleanup. No-network confinement does not itself enforce content-free snapshots.

**PROVEN:** timestamp/tokens/ticks equality is not identity (`P:480`): two distinct turns with identical tuples collapse into one under the stated rule. Require verified ancestry/stable identifiers; until measured, mark dedupe uncertainty instead of silently dropping facts. Month attribution must use turn dates, not “this month’s sessions.”

**5. AUTOMATIC SETUP**

**PROVEN:** `setup --yes` converts every proposed catalogue price into an accepted, hence `declared`, amount (`P:424,435`). An incorrect mapping can therefore look operator-confirmed without individual review.

Keep catalogue provenance and acceptance provenance separately; distinguish “accepted default” from an operator-entered amount. Preserve unknown prices as null and expose mixed confirmed/unconfirmed totals.

**PROVEN:** “existing login checks” do not already supply all promised facts: `claude_auth.py:82` returns expiry timestamps, while `codex_launcher.py:47` returns credential presence. Add explicit sanitized plan/fingerprint extraction work to §4.6.

**6. BILLING API FETCHERS**

**PROVEN:** the existing proxy permits vendor-domain suffixes, not manifest-specific exact hosts (`review_egress.py:32,53,168`). It tunnels CONNECT traffic without inspecting API methods or paths (`review_egress.py:162,185`). Domain confinement cannot enforce read-only billing access; actual key permissions must.

**SUSPECTED:** the proposed least-privilege scopes and Google authentication flow need vendor-specific validation before implementation. Specify token-exchange hosts, required query permissions and whether the credential truly prevents writes.

**PROVEN:** §6.7 leaves the fetcher-to-collector handoff unspecified (`P:526`). Define disjoint data directories and a bounded, core-mediated result contract; the fetcher must not read collector state containing transcript-derived data. Also specify secret ownership/symlink checks, rotation, redacted exceptions, fetcher pin verification, pagination, currencies, organization/project scope, credits, revision lookback and partial-response handling. Day replacement must occur only after a complete fetch.

**7. TESTS**

The two highest-priority additions:

1. **PROVEN gap:** exercise actual adapter-shaped quota events: missing type/reset, every supported window, pre-usage rejection, account switch, interruption and restart. Assert capture and uncertainty end-to-end, including events the adapter currently suppresses (`acp-agent.js:4920`; SDK declaration above).
2. **PROVEN gap:** adversarial Grok ancestry fixtures: identical independent turns, inherited turns encountered child-first, subsequent corrections, cross-month forks and replay permutations. Assert both no double-counting and no false merging (`P:473`).

**PROVEN:** the wrapper test can pass while direct binary execution bypasses it (`P:593`). The quota test injects an already-forwarded update, so it misses adapter suppression (`P:589`). The source-order test compares vendor versus estimate only, so it misses incorrect vendor-versus-billed precedence and scope mismatches (`P:638`).

**8. ONE SENTENCE**

**PROVEN:** replace the global source ranking with account-scoped metric contracts carrying provenance, coverage, freshness and uncertainty, so automation cannot promote incomplete observations or heuristic matches into confirmed financial facts (`P:86,231,480`).