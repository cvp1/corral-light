1. AMEND. The module strictly ring-fences third-party code and accurately captures vendor telemetry, but the automatic setup logic institutionalizes unconfirmed guesses and the `grok usage` tool binding risks hanging the collector.

2. ROUND-ONE CLOSURE
- **M6 Read-only inputs:** CLOSED. The bubblewrap allowlist directly mitigates the arbitrary disk read vulnerability on Linux systems, keeping third-party reads strictly contained.
- **macOS Keychain:** CLOSED. Deferring authentication discovery to the hub core intelligently avoids requiring the module to trigger `security` checks on macOS.
- **Cursor seeking UTF-8 limits:** CLOSED. Resolved via partial-line detection logic mapping to the next read cycle.
- **Vendor format drift:** CLOSED. Freezing a source below the 90% parse boundary correctly prevents silent overrides and drops.
- **Wrong Claim Judgment:** The synthesis correctly judged my R1 claim ("`usage_update` is never on disk") as WRONG. (PROVEN: `corral_core/sessions.py:698` serializes `self.events` to the `events.jsonl` log file; the final `usage_update` explicitly emitted in `turn_end` indeed reaches the disk, validating the synthesizer's correction.)

3. SOURCES OF TRUTH
- **Order:** The precedence hierarchy (Vendor Local > Billing API > Declared > List Price) is logical and highly defensive.
- **Authoritative figures:** Categorizing offline `grok usage` computation as an authoritative `vendor` truth is suspect. If xAI updates their pricing model, an older Grok binary will calculate wildly inaccurate offline token costs based on stale hardcoded constants. It should be flagged as an estimate if the local binary is severely outdated.
- **Claude rate-limit fix:** Correct. (PROVEN: `spike/node_modules/@agentclientprotocol/claude-agent-acp/dist/acp-agent.js:522` attaches `_meta: { "_claude/rateLimit": message.rate_limit_info }` directly to the transient `usage_update` event. Intercepting this prior to the monolithic dict overwrite in `sessions.py:1404` flawlessly secures the quota record.)
- **Quota staleness:** Sound. Overwriting an older payload upon its known `resetsAt` expiry cleanly halts hallucinated capacities.

4. THE GROK TOOL BINDING
- **Safety:** Reasonably safe. Setting `HOME` to an isolated throwaway overlay effectively blinds the executable from accessing global `.aws`/`.ssh` or `.grok/auth.json` keychains, while the missing network namespace guarantees zero downstream exfiltration.
- **What it can do:** It maintains unrestrained read access across the entire target `~/.grok/sessions` directory, exposing completely confidential prompt logic across all sessions. Moreso, it can intentionally or unintentionally exhaust RAM/CPU analyzing multi-gigabyte session footprints.
- **Inherited turn dedupe:** Sound. The probability of parallel branched turn-events coincidentally matching distinct timestamps, tokens, and integer ticks is negligible.

5. AUTOMATIC SETUP
- The automatic account setup harbors a configuration hazard. §6.3 explicitly asserts `setup --yes` "accepts every proposal without questions, for scripted installs." Consequently, a best-guess `list` price generated entirely by `plans.toml` estimations converts instantaneously into an operator-backed `declared` commitment. Running automated rollout scripts will permanently institutionalize inaccurate tier costs under false pretenses.

6. BILLING API FETCHERS
- **Scope & Key Management:** Secure setup. Sandboxing egress to discrete domains mapped exclusively to vendor endpoints and forcing the secret key outside general state files demonstrates excellent defensive scaling.
- **Missing (Mapping):** There is absolutely no mapping context defined bridging the operator's generic `finops.toml` profiles with the fetcher's returned network profiles. Commercial API key requests (e.g., Anthropic, OpenAI) return aggregate telemetry across the entire Org, directly aggregating hundreds of shared users. Juxtaposing an overarching enterprise bill locally against a constrained `codex-sessions:3f9a1c` individual fingerprint introduces lethal accounting discrepancies.
- **Missing (Resilience):** Fetchers lack declared retry/back-off specifications for 429 Rate Limits.

7. TESTS
- **Missing Test 1 (Automatic Setup Bias):** A behavioral test proving that executing `finops setup --yes` onto a completely blank configuration does not inappropriately serialize speculative `list` baseline pricing as user-verified `declared` tiers.
- **Missing Test 2 (Grok Exhaustion Limits):** Given `grok usage` is a massive binary spawned inside an enclave, a test validating memory and CPU ceilings (via cgroups/RLIMIT) must verify it is physically constrained against crashing the parent `collector.py` process.
- **Listed test that passes while broken:** *“Tool binding: inside the sandbox the Grok binary sees a HOME with only the sessions dir.”* This assert passes based simply on the wrapper orchestrating the environment. However, if `grok usage` immediately crashes due to attempting to cache a local `/tmp`, or missing a dependent `.grok/config` file to instantiate accurately, the test reports green despite the underlying pipeline being broken.

8. ONE SENTENCE
Implement explicit mapping identifiers connecting the opt-in vendor API fetchers with their designated local sub-accounts, to guarantee the aggregate enterprise telemetry doesn't radically misrepresent actual individual-tile spending.