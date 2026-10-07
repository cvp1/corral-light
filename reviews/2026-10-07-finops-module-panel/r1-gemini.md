1. REJECT the plan as a whole: the seam provides zero actual isolation for third-party code running as the user, and the module's data sources rely on `usage_update` events and macOS files that exist in memory but do not actually exist on disk.

2. THE SEAM (§3)
- **M1 (Out-of-process): AMEND.** Running the module as a subprocess protects the hub from `MemoryError` and crashes, but it runs as the operator's UID relying on standard Python. It has full read/write access to wall files and credentials. Declare the module as fully trusted, or use a genuine OS sandbox.
- **M2 (Declarative UI): ADOPT.** A strict block vocabulary cleanly prevents XSS, stops malicious JS from reading session cookies, and sidesteps phishing for permission cards entirely.
- **M3 (External repos): ADOPT.** (PROVEN: `test_corral_light.py:186` explicitly bans `finops.py` from the codebase, verifying structural independence). 
- **M4 (Install pinning and digests): AMEND.** A digest pinned in `modules.toml` prevents running a half-cloned update, but provides no security against a malicious module, which can overwrite the `modules.toml` file itself to change the pin since it runs under the same user.
- **M5 (Explicit updates): ADOPT.** (PROVEN: `update.py:312` schedules core updates only when the hub is idle). 
- **M6 (Read-only inputs): REJECT.** Stating the module gets "read-only" inputs is false advertising. Environment variables do not restrict `open()`. The python subprocess can write anywhere the user can.
- **M7 (CLI verbs): ADOPT.** Clean namespace separation.
- **M8 (Quiet notices): ADOPT.** Actionable but non-intrusive.

3. THE MODULE (§4)
- **Discovery: AMEND.** (PROVEN: `sessions.py:548` `darwin_keychain_blocks_isolation()` skips private pane config dirs on macOS). Discovery will find zero pane-specific Claude configurations on Macs. macOS also places Claude credentials in the Keychain, so looking for `subscriptionType` in `.claude/.credentials.json` will yield nothing. Require `/usr/bin/security` integration for macOS discovery to work.
- **Dedupe rules: AMEND.** Codex `cumulative` delta parsing and Claude `requestId` uniqueness work well. However, the `usage_update` cross-check is impossible: (PROVEN: `sessions.py:1404-1405` assigns `usage_update` data to `self.usage` in memory, but never writes it to disk or `meta.json`). The offline module scanner will never see it.
- **Notional value at list price: ADOPT.** Highly honest and useful. Users need to know if their $100 flat fee is yielding $5 or $500 of API equivalent cost to evaluate the plan. 
- **Privacy rules: REJECT.** "The module writes only inside its data dir" is an unenforceable honor-system rule unless strictly sandboxed.

4. MISSING
- **macOS Keychain extraction:** The module has no mechanism mapped out to run Apple's `security` binary to unpack the Claude plan tier, meaning macOS discovery will fail.
- **Cursor seeking limits:** `seek()` to a byte offset risks corrupting JSON parsing if the previous run ended mid-write inside a multi-byte UTF-8 character.
- **Multiple machines:** If a user runs Corral Light on two laptops sharing one sync'd vendor account, `Committed` will over-count their subscription rates or the local ledgers will fragment their total usage.
- **Second operator account:** Two operators under the same OS user sharing API profiles will overlap. Discovery merges accounts strictly by home directory footprint, failing to separate distinct keys.
- **Vendor formatting drift:** If transcripts change their `model` strings from `prices.toml` names, huge volumes of usage will fall silently completely into "unpriced."

5. TESTS (§6)
- **False test property:** "Environment: the collector's environment has no hub URL... it gets exactly the documented variables." This test passes but its implied security property is broken: the collector can easily read `~/.config/corral-light/port` or tokens directly from the file system.
- **Missing 1:** A module that specifically attempts to write to `~/.claude/` or `modules.toml` and fails (this validates whether a true security sandbox exists).
- **Missing 2:** macOS discovery runs the `/usr/bin/security` Keychain command and successfully extracts the Claude plan tier.
- **Missing 3:** Incremental cursor tests that deliberately write partial multi-byte UTF-8 sequences at EOF before `seek()`, validating it doesn't crash on resume.

6. SEQUENCING AND SCOPE (§5)
- **Cut from v1:** Network API balance reads (Phase 3). Keep the module strictly offline first to focus on bullet-proof core data extraction.  
- **Move earlier:** Phase 4. Have the hub core write a `usage_update.jsonl` feed into `CORRAL_LIGHT_STATE/panes/*/` on disk. Without this, the module has zero Light attribution or cross-checks.
- **Phase 0:** It asks the right questions but failed to identify *where* macOS credentials actually live, and *whether* `usage_update` events ever touch disk.

7. THE PLAN'S SIX OPEN QUESTIONS (§7)
1. **Declarative views:** The block vocabulary is plenty. It completely outclasses iframe JS in phishing mitigation and mobile responsiveness. Do not adopt iframes.
2. **Out-of-process:** A subprocess every 5 minutes is cheap. But do not pretend it isolates the hub from malicious file tampering. 
3. **Notional value:** Extremely useful. It tells users the actual ROI of their subscription tier.
4. **Claude quota endpoint:** Absolutely not. A read-only analytical module must never consume an active auth token to perform an invisible network action.
5. **Reading state dir directly:** Brittle and invasive. The hub should expose a persistent, read-only JSONL event stream on disk containing pane lifecycles and usage updates.
6. **Current tree hurdles:** macOS private dirs are explicitly disabled (`sessions.py:548`), `usage_update` runs only in memory (`sessions.py:1405`), and the Python subprocess lacks any native filesystem permission restriction.

8. ONE SENTENCE
Acknowledge that third-party Python modules run as fully trusted code with root-equivalent access to the user's data unless placed in a genuine OS sandbox, and have the hub log `usage_update` events to disk so the module can actually fulfill its cross-check duties.