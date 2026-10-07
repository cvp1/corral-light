I’ll read the full plan, then check its security and accounting assumptions against the code, staying within this clone and using read-only commands.
The code confirms Light’s dedicated Codex home and Claude’s per-pane credential links, but macOS Claude can fall back to the shared home. Two larger gaps stand out: the subprocess design does not enforce read-only access, and the proposed manifest cannot be parsed by the existing TOML fallback on Python 3.9/3.10.
**AMEND — PROVEN:** M6’s isolation claim is false: the proposed readable state directory contains the wall’s cookie-signing key (`auth.py:29,216`).

**2. THE SEAM**

- **M1 — AMEND. PROVEN:** The existing ACP launcher inherits the parent environment before applying overrides (`corral_core/acp.py:116`); copying that pattern would violate the proposed environment contract. Build an explicit environment allowlist. **SUSPECTED:** A subprocess can exhaust machine memory, fill stderr buffers, or escape process-group cleanup by starting another session. Bound stdout/stderr while reading, impose resource and concurrency limits, and describe process separation as fault containment, not sandboxing.
- **M2 — ADOPT, with stricter validation. PROVEN:** The contract specifies text rendering and HTTPS links, but limits only selected shapes (`docs/finops-module-plan.md:177`). Add bounds for tile items, notices, labels, notes, nesting, and total DOM nodes; require finite meter values and validated object types. Parse URLs and check their protocol; use `noopener noreferrer` for new tabs. **SUSPECTED:** Properly implemented, this closes module-supplied HTML/script injection, but does not establish collector confinement.
- **M3 — ADOPT. PROVEN:** The claimed independence check exists: it rejects root-level `finops.py` and selected imports (`test_corral_light.py:185`). It does **not** establish that arbitrary nested module code is absent. Keep fixtures explicitly synthetic and strengthen the structural check if broader independence is intended.
- **M4 — AMEND. PROVEN:** Digest checking is promised without defining digest coverage or check-to-execution synchronization; CLI and doctor entry points are also declared (`docs/finops-module-plan.md:121,145`). Define canonical hashing of executable content, modes, symlinks and untracked/importable files; reject external symlink targets. Verify **every** execution path. **SUSPECTED:** An in-place update can change files after verification; PATH executables, Python startup configuration and dependencies can execute unpinned code. Use staged version directories, a controlled interpreter, and one atomic generation switch. Pins detect changes relative to a trusted record; they cannot defend against a same-user attacker who can rewrite that record.
- **M5 — AMEND. PROVEN:** Explicit updates are specified, but rollback and concurrent-run behavior are not (`docs/finops-module-plan.md:200`). Serialize update/remove/run operations, retain the previous generation, and define ledger migration rollback. An upstream-check failure must not prevent a core update.
- **M6 — REJECT as a security guarantee. PROVEN:** `STATE/session.key` signs cookies (`auth.py:29,225`); pane config directories link real credentials (`sessions.py:476`). Read-only access to the whole state directory therefore exposes authentication material. Omitting a hub URL is ineffective: the default port is in `hub.py:69`. Native code can supply HTTP headers itself. Export a narrow, versioned metadata/usage feed and state explicitly that unsandboxed modules are trusted operator code. If “cannot act on the wall” remains a requirement, enforce filesystem and network isolation.
  
  **PROVEN:** Static traversal is already resolved and confined to `STATIC` (`hub.py:56`), so installing outside that directory creates no direct static exposure. **SUSPECTED:** An unconstrained same-user module can instead modify writable core/static files. Also keep module errors and account paths out of unauthenticated `/health` (`hub.py:496`).
- **M7 — AMEND. PROVEN:** The wrapper has explicit core dispatch followed by rejection (`corral-light:47`). Preserve that precedence, reserve every actual verb—including `cli`, `later`, `search`, `digest`, and `port`—and apply pin verification to fall-through execution. Never interpolate module arguments into shell commands.
- **M8 — ADOPT. PROVEN:** Notices are separate snapshot objects (`docs/finops-module-plan.md:172`). Specify count limits, stable IDs, expiry, deduplication and stale-snapshot behavior so frozen quota warnings do not persist indefinitely.

**3. THE MODULE**

**PROVEN — discovery claims checked:** Light does set its dedicated Codex home from `CORRAL_CODEX_HOME`, overriding ambient `CODEX_HOME` (`codex_launcher.py:21,72`). Pass that resolved location explicitly; the proposed minimal environment otherwise loses overrides. Claude’s pane credential links are real, but macOS always declines private-config isolation (`sessions.py:466,548`). Therefore “every Light pane has private transcripts” is not portable. Keychain-backed plan discovery needs an explicit unknown state.

**PROVEN — another claim checked:** `usage_update` only updates memory (`sessions.py:1404`); persisted `turn_end` events contain the latest usage (`sessions.py:1808`). A reader searching disk for `usage_update` events will miss that data. Define the feed and historical session mapping before promising attribution.

**SUSPECTED:** Two Codex homes can belong to one billed subscription, while one home can change accounts. Treat homes as sources, not account identities; support explicit merge/split and dated account assignments. Claude credential-link identity likewise does not identify the historical owner of every transcript.

**SUSPECTED:** Claude `requestId` alone needs evidence covering multiple assistant records, streaming revisions, missing IDs and conflicting copies. Specify which record wins; neither summing duplicates nor “first record wins” is generally justified by §2. Preserve event identities and provenance.

**PROVEN:** Daily/model aggregates alone cannot implement replacement by request ID as promised (`docs/finops-module-plan.md:328`). Retain an event-level dedupe index and update facts plus cursors transactionally.

**SUSPECTED:** Codex deltas need rules for duplicate/out-of-order events, counter resets, forked/copied sessions, model changes and missing initial history. A first cumulative total cannot safely be assigned entirely to its observation day. Preserve per-session baselines and mark uncertain attribution.

**SUSPECTED:** Price normalization can erase billing distinctions such as long-context tiers; cache categories and reasoning-token inclusion need explicit fixtures. Preserve raw model identifiers and pricing-relevant dimensions.

**PROVEN:** “Value per $” is proposed (`docs/finops-module-plan.md:304`). Replace it with “API-equivalent list cost”: a counterfactual price is not savings or subscription utilization. Keep fees, quotas and list estimates separate.

**PROVEN:** Privacy promises conflict with implementation needs: setup writes config outside the data directory (`docs/finops-module-plan.md:252,357`). Clarify collector versus setup permissions. **SUSPECTED:** Credential JSON parsing reads complete tokens transiently; sentinel-output tests establish non-disclosure only for tested paths. Require bounded parsing, sanitized errors, restrictive file permissions, retention rules and title-redaction options. Grok/Gemini file presence should say “credentials found,” not “logged in”; Ollama should say “provider charge not measured” rather than imply zero operating cost.

**4. MISSING**

- **PROVEN:** Python 3.9/3.10 cannot parse this manifest with the current fallback: it accepts only strings/integers and `[[name]]` headers (`corral_core/tomlmini.py:64,77`). Arrays, `[collector]`, booleans, floats and inline tables require another format/parser or a higher minimum Python version.
- **PROVEN:** The 45-second collector timeout conflicts with a permitted 60-second initial scan (`docs/finops-module-plan.md:118,444`). Use resumable scan budgets and visible backfill progress.
- **SUSPECTED:** Fresh installs need distinct states for no credentials, no records, unsupported format, incomplete history and verified zero usage. Unknown format versions should produce diagnostics, not silently skip an entire month.
- **SUSPECTED:** Large stores require bounded line length, crash-safe checkpoints, disk-full handling and concurrent CLI/collector locking.
- **SUSPECTED:** Multiple machines and remote-shell lanes require explicit local-only scope, machine identities and eventual cross-machine dedupe; subscription fees must not be summed once per machine.
- **SUSPECTED:** A second operator needs account ownership and visibility rules. Shared wall access must not silently expose another operator’s titles or spending.
- **PROVEN:** Coverage uses kind `derived`, which the snapshot enum rejects (`docs/finops-module-plan.md:187,306`). Define its classification and denominator, including shell/Ollama lanes.

**5. TESTS**

The three most important additions:

1. **PROVEN gap:** Test the authority boundary with fake state containing a signing key, credentials and writable static assets; exercise collector, CLI and doctor. Environment omission does not protect file-accessible secrets (`auth.py:29`).
2. **SUSPECTED failure:** Race execution against update/remove and simulate interruption at every installation stage; include symlink replacement, injected import files, stderr flooding and escaped descendants.
3. **SUSPECTED failure:** Verify ledger replay invariance: the same logical history delivered through duplicates, truncation, reset, account switch and crashes between fact/cursor writes must yield identical totals and uncertainty markers.

**PROVEN:** Existing tests can pass while their named property fails: output sentinels do not prove credentials were never read; patched Python sockets do not cover subprocess networking; one-byte tampering before launch does not cover execution races; mini-DOM string tests do not establish browser URL behavior (`docs/finops-module-plan.md:382,402,435`). Add real-browser adversarial tests and supported-Python parser tests.

**PROVEN:** The 5% Claude comparison is an acceptance criterion while its reference semantics remain unresolved (`docs/finops-module-plan.md:341,459`). It is a diagnostic until Phase 0 establishes comparable quantities.

**6. SEQUENCING AND SCOPE**

**SUSPECTED:** Cut v1 notices, a general first-party index, upstream-drift integration, “value per $,” and coverage percentages. Ship installation, lifecycle safety, declarative tables/tiles, explicit accounts and trustworthy Claude/Codex facts.

**PROVEN:** Phase 0 measures useful vendor behavior but omits request identity, counter-reset semantics, account switching and ACP-to-vendor session mapping (`docs/finops-module-plan.md:363`). Move those questions, the trust model, parser compatibility and scan-budget experiment before seam implementation.

**PROVEN:** Phase 3 promises attribution within one minute despite a five-minute collector period (`docs/finops-module-plan.md:117,366`). Change the target or introduce a bounded refresh trigger.

**7. SIX OPEN QUESTIONS**

1. **SUSPECTED:** Keep declarative views; validate the vocabulary against one concrete second-module use case before expanding it.
2. **SUSPECTED:** Periodic subprocesses are appropriate; resumable work matters more than eliminating startup overhead.
3. **SUSPECTED:** List-cost estimates are useful when clearly counterfactual; omit ROI language.
4. **SUSPECTED:** Defer Claude OAuth quota reads until endpoint behavior, token-refresh ownership and permissions are understood; then require account-specific opt-in.
5. **PROVEN:** Prefer a narrow feed; whole-state reads expose signing material (`auth.py:29`), although a feed alone cannot sandbox native code.
6. **PROVEN:** TOML compatibility, non-persisted usage updates, macOS Claude fallback and unauthenticated health output make §3.5 incomplete (`corral_core/tomlmini.py:77`; `sessions.py:1404,548`; `hub.py:496`).

**8. ONE SENTENCE**

**PROVEN:** Replace M6’s claimed isolation with an explicit trust model and a secret-free input boundary, because the current proposal hands collectors the wall’s signing key (`auth.py:29`).